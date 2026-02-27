"""
PR Review Service
=================
Orchestrates the full review pipeline:
  1. Fetch PR diff from GitHub
  2. Parse diff → extract added source per file
  3. Use ingestion_service language parsers (tree-sitter) to extract imports & symbols
  4. Resolve those imports against the Neo4j graph (same repo)
  5. Find callers of changed functions (impact analysis)
  6. Build context → run LLM agents → post comment
"""

import asyncio
import importlib
import logging
import os
import traceback
from pathlib import Path
from typing import Dict, List, Set, Tuple

from api.utils.comment_formatter import format_github_comment
from api.utils.graph_context import build_graph_context_section
from common.debug_writer import make_review_dir, write_step
from api.services.firebase_service import firebase_service
from common.firebase_models import PRMetadata, ReviewRunData
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService
from deepagent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview
from deepagent.agent.review_pipeline import run_review
from common.github_client import GitHubClient
from common.diff_parser import parse_unified_diff

logger = logging.getLogger(__name__)


# ==========================================================================
# File extension → tree-sitter language name mapping (all 17 supported)
# ==========================================================================
_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".scala": "scala", ".sc": "scala",
    ".swift": "swift",
    ".php": "php",
    ".hs": "haskell",
}

# ==========================================================================
# Language parser registry
# Maps language name → (module path, class name) for lazy import.
# These are the SAME parsers used by the ingestion service to build the graph.
# ==========================================================================
_LANG_PARSER_REGISTRY = {
    "python":     ("ingestion_service.languages.python",     "PythonLangTreeSitterParser"),
    "javascript": ("ingestion_service.languages.javascript", "JavascriptLangTreeSitterParser"),
    "typescript": ("ingestion_service.languages.typescript", "TypescriptLangTreeSitterParser"),
    "go":         ("ingestion_service.languages.go",         "GoLangTreeSitterParser"),
    "java":       ("ingestion_service.languages.java",       "JavaLangTreeSitterParser"),
    "rust":       ("ingestion_service.languages.rust",       "RustLangTreeSitterParser"),
    "c":          ("ingestion_service.languages.c",          "CLangTreeSitterParser"),
    "cpp":        ("ingestion_service.languages.cpp",        "CppLangTreeSitterParser"),
    "ruby":       ("ingestion_service.languages.ruby",       "RubyLangTreeSitterParser"),
    "c_sharp":    ("ingestion_service.languages.csharp",     "CSharpLangTreeSitterParser"),
    "php":        ("ingestion_service.languages.php",        "PhpLangTreeSitterParser"),
    "kotlin":     ("ingestion_service.languages.kotlin",     "KotlinLangTreeSitterParser"),
    "scala":      ("ingestion_service.languages.scala",      "ScalaLangTreeSitterParser"),
    "swift":      ("ingestion_service.languages.swift",      "SwiftLangTreeSitterParser"),
    "haskell":    ("ingestion_service.languages.haskell",    "HaskellLangTreeSitterParser"),
}

# Cache so we only instantiate each language parser once
_parser_cache: Dict[str, object] = {}


# ==========================================================================
# Parser loading
# ==========================================================================

def _get_lang_parser(lang: str):
    """
    Get the tree-sitter language parser for `lang`.

    Each language parser (e.g. PythonLangTreeSitterParser) expects a wrapper
    with .language_name, .language, and .parser attributes.  We build a
    lightweight adapter instead of pulling in the full ingestion TreeSitterParser
    which depends on Neo4j/jobs.
    """
    # Return from cache if already loaded
    if lang in _parser_cache:
        return _parser_cache[lang]

    # Unknown language → skip
    if lang not in _LANG_PARSER_REGISTRY:
        return None

    module_path, class_name = _LANG_PARSER_REGISTRY[lang]
    try:
        from common.tree_sitter_manager import get_language_safe, create_parser

        # Build the adapter the language parser constructors expect
        class _Adapter:
            pass
        adapter = _Adapter()
        adapter.language_name = lang
        adapter.language = get_language_safe(lang)   # tree-sitter Language object
        adapter.parser = create_parser(lang)         # tree-sitter Parser object

        # Dynamically import the language-specific parser class
        module = importlib.import_module(module_path)
        parser_class = getattr(module, class_name)

        # Instantiate and configure
        parser_instance = parser_class(adapter)
        parser_instance.index_source = False  # we don't need source code stored on each node

        _parser_cache[lang] = parser_instance
        return parser_instance
    except Exception as e:
        logger.warning(f"Failed to load parser for {lang}: {e}")
        return None


# ==========================================================================
# Diff parsing helpers
# ==========================================================================

def _extract_added_source_by_file(diff_text: str) -> Dict[str, str]:
    """
    Walk through a unified diff and collect added lines per file.

    Returns {"app/services/foo.py": "from x import y\\ndef bar(): ..."}

    Only lines starting with '+' (but not '+++ b/...') are collected,
    with the leading '+' stripped so tree-sitter can parse the source.
    """
    files: Dict[str, List[str]] = {}
    current_file = None

    for line in diff_text.splitlines():
        # "+++ b/path/to/file" marks the start of a new file in the diff
        if line.startswith("+++ b/"):
            current_file = line[6:]  # strip "+++ b/" prefix
            if current_file not in files:
                files[current_file] = []
        # Lines starting with "+" are added lines (skip the "+++ b/" header)
        elif line.startswith("+") and not line.startswith("+++") and current_file:
            files[current_file].append(line[1:])  # strip the leading "+"

    # Join lines into a single source string per file, skip empty files
    return {f: "\n".join(lines) for f, lines in files.items() if lines}


# Import & symbol extraction from diff (tree-sitter powered)
def extract_imports_from_diff(
    diff_text: str,
    file_contents: Dict[str, str] | None = None,
) -> List[Dict]:
    """
    Extract imports from changed files using tree-sitter.

    Prefers full post-PR file content (``file_contents``) over just the
    added diff lines so that imports declared outside a changed hunk are
    still captured.  Falls back to added-lines-only when the full file
    is unavailable.

    Uses the same language parsers as the ingestion service, so the import
    format matches what the graph stores:
      Python:     {"name": "foo", "full_import_name": "bar.foo", ...}
      JavaScript: {"name": "useState", "source": "react", ...}
    """
    all_imports: List[Dict] = []
    added_source = _extract_added_source_by_file(diff_text)

    for file_path, fallback_source in added_source.items():
        ext = Path(file_path).suffix.lower()
        lang = _EXT_TO_LANG.get(ext)
        if not lang:
            continue

        parser = _get_lang_parser(lang)
        if not parser or not hasattr(parser, "_find_imports"):
            continue

        # Full file gives accurate AST; diff-only fragment may be incomplete
        source = (file_contents or {}).get(file_path) or fallback_source

        try:
            tree = parser.parser.parse(source.encode("utf-8"))
            file_imports = parser._find_imports(tree.root_node)
            all_imports.extend(file_imports)
        except Exception as e:
            logger.warning(f"Import extraction failed for {file_path}: {e}")

    return all_imports


def extract_symbols_from_diff(
    diff_text: str,
    file_contents: Dict[str, str] | None = None,
) -> Tuple[Set[str], Set[str]]:
    """
    Extract function and class names defined in changed files using tree-sitter.

    Prefers full post-PR file content (``file_contents``) so that symbols
    whose ``def``/``class`` header was outside the changed hunk are not
    missed.  Falls back to added-lines-only when the full file is unavailable.

    Returns (function_names, class_names) as sets of strings.
    """
    functions: Set[str] = set()
    classes: Set[str] = set()

    added_source = _extract_added_source_by_file(diff_text)

    for file_path, fallback_source in added_source.items():
        ext = Path(file_path).suffix.lower()
        lang = _EXT_TO_LANG.get(ext)
        if not lang:
            continue

        parser = _get_lang_parser(lang)
        if not parser:
            continue

        source = (file_contents or {}).get(file_path) or fallback_source

        try:
            tree = parser.parser.parse(source.encode("utf-8"))
            root = tree.root_node

            if hasattr(parser, "_find_functions"):
                for func in parser._find_functions(root):
                    functions.add(func.get("name", ""))

            if hasattr(parser, "_find_classes"):
                for cls in parser._find_classes(root):
                    classes.add(cls.get("name", ""))
        except Exception as e:
            logger.warning(f"Symbol extraction failed for {file_path}: {e}")

    return functions, classes


# ==========================================================================
# Context formatting (for LLM agents)
# ==========================================================================

def _build_agent_context(
    diff_text: str,
    graph_section: str,
    pr_title: str = "",
    pr_body: str = "",
) -> str:
    """
    Build the markdown context string sent to LLM agents.

    The graph section already contains affected symbol source, callers,
    outbound dependencies, imports, and class hierarchy — all fetched in
    one query by get_diff_context_enhanced.  We just prefix it with the
    PR metadata and diff so the agent sees intent before reading the changes.
    """
    parts = []

    # Section 0: PR metadata (title + description)
    if pr_title:
        parts.append("## Pull Request")
        parts.append(f"**Title:** {pr_title}")
        if pr_body and pr_body.strip():
            parts.append("")
            parts.append("**Description:**")
            parts.append(pr_body.strip())
        parts.append("")
        parts.append("---")
        parts.append("")

    # Section 1: THE DIFF (what's changing)
    parts.append("## Code Changes (Diff)")
    parts.append("*This is what the PR modifies:*")
    parts.append("")
    parts.append("```diff")
    if len(diff_text) > 50000:
        parts.append(diff_text[:50000])
        parts.append("# ... (diff truncated - very large PR)")
    else:
        parts.append(diff_text)
    parts.append("```")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Section 2: Full graph context (affected symbols, callers, imports, deps, hierarchy)
    if graph_section and graph_section != "No graph context available.":
        parts.append("## Repository Context (from dependency graph)")
        parts.append(graph_section)
        parts.append("")

    return "\n".join(parts) if parts else "No additional context available."


def _parse_files_changed(
    diff_text: str,
    issues: list[Issue],
    walk_through: list[str] | None = None,
) -> list[FileSummary]:
    """Derive FileSummary list mechanically from diff line counts."""
    file_stats: Dict[str, tuple[int, int]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in file_stats:
                file_stats[current_file] = (0, 0)
        elif current_file and line.startswith("+") and not line.startswith("+++"):
            added, removed = file_stats[current_file]
            file_stats[current_file] = (added + 1, removed)
        elif current_file and line.startswith("-") and not line.startswith("---"):
            added, removed = file_stats[current_file]
            file_stats[current_file] = (added, removed + 1)

    # Build a per-file description from the agent's walkthrough (preferred) or
    # fall back to the first issue title for the file, then "Modified".
    file_to_description: Dict[str, str] = {}
    for entry in (walk_through or []):
        if " — " in entry:
            wt_file, wt_desc = entry.split(" — ", 1)
            file_to_description[wt_file.strip().strip("`")] = wt_desc.strip()
    for issue in issues:
        if issue.file not in file_to_description:
            file_to_description[issue.file] = issue.title

    return [
        FileSummary(
            file=file_path,
            lines_added=added,
            lines_removed=removed,
            what_changed=file_to_description.get(file_path, "Modified"),
        )
        for file_path, (added, removed) in file_stats.items()
    ]


def _build_review_prompt(
    agent_context: str,
    repo_id: str,
    pr_number: int,
) -> str:
    """Assemble the final prompt string that is sent verbatim to the LLM agents."""
    return f"## PR #{pr_number} in {repo_id}\n\n{agent_context}"


# ==========================================================================
# ==========================================================================
# Reconciliation helpers
# ==========================================================================

def _reconcile(
    new_issues: list[Issue],
    prev_run: dict | None,
) -> ReconciledReview:
    """
    Diff new agent output against the previous run.

    Each issue gets a status:
      - "fixed"      — fingerprint was in prev run but not in new run
      - "still_open" — fingerprint in both runs
      - "new"        — fingerprint only in new run

    Fixed issues from the previous run are re-injected (with status="fixed")
    so the comment can render them in the ✅ Fixed section.
    """
    prev_issues: list[dict] = prev_run.get("issues", []) if prev_run else []
    prev_fp: set[str] = {i["fingerprint"] for i in prev_issues if i.get("fingerprint")}
    new_fp: set[str] = {i.fingerprint for i in new_issues}

    fixed_fp = prev_fp - new_fp
    still_open_fp = prev_fp & new_fp
    new_fp_only = new_fp - prev_fp

    tagged: list[Issue] = []
    for issue in new_issues:
        if issue.fingerprint in still_open_fp:
            issue.status = "still_open"
        else:
            issue.status = "new"
        tagged.append(issue)

    # Re-add fixed issues from prev run so the comment can show them
    for prev_issue_dict in prev_issues:
        fp = prev_issue_dict.get("fingerprint", "")
        if fp in fixed_fp:
            fixed_issue = Issue.model_validate({**prev_issue_dict, "status": "fixed"})
            tagged.append(fixed_issue)

    total = len(tagged)
    n_fixed = len(fixed_fp)
    n_open = len(still_open_fp)
    n_new = len(new_fp_only)
    run_label = f"Run #{(prev_run or {}).get('runNumber', 0) + 1}"

    if total == 0 or (n_fixed == total):
        summary = f"{run_label} — All issues resolved. No new issues found. ✅"
    else:
        summary = (
            f"{run_label} — {n_fixed} fixed, {n_open} still open, {n_new} new"
        )

    return ReconciledReview(
        issues=tagged,
        summary=summary,
        fixed_fingerprints=list(fixed_fp),
        still_open_fingerprints=list(still_open_fp),
        new_fingerprints=list(new_fp_only),
    )


def _build_previous_issues_section(prev_run: dict) -> str:
    """Format the previous run's open issues as a prompt context section."""
    open_issues = [i for i in prev_run.get("issues", []) if i.get("status") != "fixed"]
    if not open_issues:
        return ""
    lines = [
        "## Previously Flagged Issues (still open from last review)",
        "*These were reported in the last review run and NOT yet fixed. "
        "Do not re-report them unless the code has changed further.*",
        "",
    ]
    for issue in open_issues:
        lines.append(
            f"- [{issue.get('severity','?').upper()}] **{issue.get('title','')}** "
            f"in `{issue.get('file','')}` — {issue.get('description','')}"
        )
    return "\n".join(lines)


# ==========================================================================
# Main pipeline
# ==========================================================================

async def execute_pr_review(owner: str, repo: str, pr_number: int, neo4j: Neo4jClient | None = None) -> None:
    """
    Full PR review pipeline.

    Steps:
      1. Fetch the PR diff from GitHub
      2. Parse the diff into changed file paths and line ranges
      3. Extract imports and symbol definitions from added lines (tree-sitter)
      4. Connect to Neo4j and resolve context:
         a. Find graph symbols overlapping changed line ranges
         b. Look up imported symbols in the graph (get source code)
         c. Find callers of changed functions (impact analysis)
      5. Build risk level based on scope of changes
      6. Format everything into context for the LLM agents
      7. [DEBUG MODE] LLM call skipped — all context written to review dir
    """
    try:
        if not isinstance(pr_number, int) or pr_number <= 0:
            logger.error("execute_pr_review: invalid pr_number %r — aborting", pr_number)
            return

        logger.info(f"Starting review pipeline for {owner}/{repo}#{pr_number}")
        repo_id = f"{owner}/{repo}"  # matches the repo identifier stored in the graph

        # Create a timestamped folder for this review run
        review_dir = make_review_dir(owner, repo, pr_number)

        # ── Step 1: Fetch diff, PR metadata, and head SHA (all in parallel) ─
        gh = GitHubClient()
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )
        if not diff_text:
            logger.warning("Empty diff, skipping review")
            return
        pr_title = pr_info.get("title", "")
        pr_body = pr_info.get("body", "")
        logger.info(f"Fetched diff ({len(diff_text)} chars), PR title: {pr_title!r}, head: {head_sha[:7]}")

        write_step(review_dir, "01_diff.md", "\n".join([
            f"# Step 1 — Raw Diff",
            f"**PR:** {owner}/{repo}#{pr_number}",
            f"**Title:** {pr_title}",
            f"**Chars:** {len(diff_text)}",
            "",
            f"**Description:**",
            pr_body or "*(no description)*",
            "",
            "```diff",
            diff_text,
            "```",
        ]))

        # ── Step 2: Parse diff structure ────────────────────────────────
        changes = parse_unified_diff(diff_text)
        files_changed = list({c["file_path"] for c in changes})
        logger.info(f"Parsed {len(changes)} hunks across {len(files_changed)} files")

        write_step(review_dir, "02_parsed_diff.md", "\n".join([
            f"# Step 2 — Parsed Diff",
            f"**Hunks:** {len(changes)}  |  **Files changed:** {len(files_changed)}",
            "",
            "## Files Changed",
            *[f"- `{f}`" for f in files_changed],
            "",
            "## Hunks",
            *[
                f"- `{c['file_path']}` lines {c.get('start_line', '?')}–{c.get('end_line', '?')}"
                for c in changes
            ],
        ]))

        # ── Step 2.5: Fetch full post-PR file contents at head SHA ──────
        # Fetched here (before symbol extraction) so tree-sitter can parse
        # the complete file rather than just the added diff lines.
        _raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in files_changed],
            return_exceptions=True,
        )
        full_file_contents: Dict[str, str] = {
            fp: content
            for fp, content in zip(files_changed, _raw)
            if not isinstance(content, Exception) and content
        }
        logger.info(
            f"Fetched full content for {len(full_file_contents)}/{len(files_changed)} files"
        )

        # ── Step 3: Extract imports & symbols (full file preferred) ─────
        diff_imports = extract_imports_from_diff(diff_text, full_file_contents)
        diff_functions, diff_classes = extract_symbols_from_diff(diff_text, full_file_contents)
        all_diff_symbols = diff_functions | diff_classes
        logger.info(
            f"Diff extraction: {len(diff_imports)} imports, "
            f"{len(diff_functions)} functions, {len(diff_classes)} classes"
        )

        write_step(review_dir, "03_extracted_symbols.md", "\n".join([
            f"# Step 3 — Extracted Imports & Symbols",
            "",
            f"## Imports ({len(diff_imports)})",
            *[
                f"- `{imp.get('name', '?')}` "
                f"(full: `{imp.get('full_import_name', imp.get('source', '?'))}`)"
                for imp in diff_imports
            ],
            "",
            f"## Functions ({len(diff_functions)})",
            *[f"- `{fn}`" for fn in sorted(diff_functions)],
            "",
            f"## Classes ({len(diff_classes)})",
            *[f"- `{cls}`" for cls in sorted(diff_classes)],
        ]))

        # ── Step 4: Connect to Neo4j and resolve context ────────────────
        if neo4j is None:
            neo4j = get_neo4j_client()
        query_service = CodeSearchService(neo4j)

        # Single comprehensive graph query: affected symbols (with full source),
        # callers, outbound dependencies, imports, and class hierarchy.
        graph_context = query_service.get_diff_context_enhanced(repo_id, changes)
        graph_symbols = graph_context.get("affected_symbols", [])
        graph_section = build_graph_context_section(graph_context)

        total_callers = sum(
            len(entry.get("callers", [])) for entry in graph_context.get("callers", [])
        )
        logger.info(
            f"Graph context: {len(graph_symbols)} affected symbols, "
            f"{total_callers} callers, "
            f"{graph_context.get('total_imports', 0)} imports"
        )

        write_step(review_dir, "04_graph_context.md", "\n".join([
            "# Step 4 — Graph Context",
            "",
            f"**Affected symbols:** {len(graph_symbols)}",
            f"**Callers:** {total_callers}",
            f"**Imports resolved:** {graph_context.get('total_imports', 0)}",
            f"**Dependencies:** {len(graph_context.get('dependencies', []))}",
            f"**Class hierarchy entries:** {len(graph_context.get('class_hierarchy', []))}",
            "",
            "## Rendered section (sent to agents)",
            graph_section,
        ]))

        # ── Step 5: Determine risk level ────────────────────────────────
        all_symbol_names = all_diff_symbols | {s.get("name", "") for s in graph_symbols}
        changed_symbol_names = list(all_symbol_names)
        if total_callers > 10 or len(all_symbol_names) > 5:
            risk_level = "high"
        elif total_callers > 3 or len(all_symbol_names) > 2:
            risk_level = "medium"
        else:
            risk_level = "low"

        write_step(review_dir, "05_risk_level.md", "\n".join([
            "# Step 5 — Risk Level",
            "",
            f"**Risk level:** `{risk_level}`",
            f"**Total callers:** {total_callers}",
            f"**Total symbols:** {len(all_symbol_names)}",
            "",
            "## All Changed Symbols",
            *[f"- `{s}`" for s in sorted(changed_symbol_names)],
        ]))

        # ── Step 6: Build context for LLM agents ───────────────────────
        agent_context = _build_agent_context(diff_text, graph_section, pr_title, pr_body)

        write_step(review_dir, "06_agent_context.md", "\n".join([
            "# Step 6 — Agent Context (graph + imports + callers)",
            "",
            agent_context,
        ]))

        # ── Step 7: Load previous review run from Firestore ─────────────
        uid = firebase_service.lookup_uid_by_github_username(owner)
        prev_run: dict | None = None
        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(owner=owner, repo=repo, pr_number=pr_number, repo_id=repo_id),
            )
            prev_run = firebase_service.get_latest_review_run(uid, owner, repo, pr_number)
            if prev_run:
                logger.info(f"Loaded previous run #{prev_run.get('runNumber')} for {repo_id}#{pr_number}")
        else:
            logger.warning(f"No Firestore user found for GitHub owner '{owner}' — skipping history")

        # Inject previous open issues into agent context
        if prev_run:
            prev_section = _build_previous_issues_section(prev_run)
            if prev_section:
                agent_context = agent_context + "\n\n" + prev_section

        # ── Step 8: Assemble final prompt + run multi-agent review ───────
        review_prompt = _build_review_prompt(agent_context, repo_id, pr_number)

        write_step(review_dir, "06b_review_prompt.md", "\n".join([
            "# Step 6b — Final Review Prompt (sent to LLM agents)",
            "",
            review_prompt,
        ]))

        review_results = await run_review(review_prompt, repo_id, pr_number)
        review_results.files_changed_summary = _parse_files_changed(
            diff_text, review_results.issues, walk_through=review_results.walk_through
        )
        logger.info(f"Review complete: {len(review_results.issues)} issues found")

        # ── Step 9: Reconcile new results against previous run ───────────
        reconciled = _reconcile(review_results.issues, prev_run)
        reconciled.positive_findings = review_results.positive_findings

        # ── Step 10: Save run to Firestore ───────────────────────────────
        if uid:
            run_doc = ReviewRunData(
                issues=[i.model_dump() for i in reconciled.issues],
                positive_findings=reconciled.positive_findings,
                summary=reconciled.summary,
                fixed_fingerprints=reconciled.fixed_fingerprints,
                still_open_fingerprints=reconciled.still_open_fingerprints,
                new_fingerprints=reconciled.new_fingerprints,
                repo_id=repo_id,
                pr_number=pr_number,
            )
            firebase_service.save_review_run(uid, owner, repo, pr_number, run_doc)

        # ── Step 11: Format and post comment ─────────────────────────────
        context_data = ContextData(
            files_changed=files_changed,
            modified_symbols=changed_symbol_names,
            total_callers=total_callers,
            risk_level=risk_level,
        )
        final_comment = format_github_comment(
            reconciled, context_data, pr_number,
            files_changed_summary=review_results.files_changed_summary,
            walk_through=review_results.walk_through,
            raw_agent_json=review_results.model_dump_json(indent=2),
        )
        await gh.post_comment(owner, repo, pr_number, final_comment)
        logger.info(f"Posted review comment on {owner}/{repo}#{pr_number}")


    except Exception:
        logger.error(f"Review pipeline failed: {traceback.format_exc()}")
