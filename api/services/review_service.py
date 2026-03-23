"""
PR Review Service
=================
Pipeline:
  1. Fetch diff + PR metadata + head SHA
  2. Parse hunks → changed files + line ranges
  3. Fetch full file contents at head SHA (post-PR state)
  4. AST-parse every PR file → imports, functions, classes, call-sites
  5. Resolve local imports → fetch + AST-parse → extract only referenced symbols
  6. Optional: enrich with callers from Neo4j graph
  7. Load previous Firestore run (for fixed/still_open tracking)
  8. Build review prompt (diff + full files + referenced symbol sources + history)
  9. Run lint + LLM review in parallel
 10. Post comment, save run to Firestore
"""

import asyncio
import importlib
import logging
import re
import traceback
from pathlib import Path
from typing import Dict, List, Set, Tuple

from api.services.firebase_service import firebase_service
from api.services.lint_service import run_lint
from api.utils.comment_formatter import (
    format_github_comment,
    format_inline_comment,
    format_review_summary,
)
from api.utils.graph_context import build_graph_context_section
from code_review_agent.agent.runner import run_review
from code_review_agent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview
from common.call_skip import get_call_skip
from common.debug_writer import make_review_dir, write_step
from common.diff_parser import parse_unified_diff
from common.firebase_models import PRMetadata, PrReviewStatus, ReviewRunData
from common.github_client import get_github_client
from common.languages import EXT_TO_LANG, LANG_PARSER_REGISTRY
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)

# ── Parser cache ───────────────────────────────────────────────────────────────

_parser_cache: Dict[str, object] = {}


def _get_lang_parser(lang: str):
    if lang in _parser_cache:
        return _parser_cache[lang]
    if lang not in LANG_PARSER_REGISTRY:
        return None
    module_path, class_name = LANG_PARSER_REGISTRY[lang]
    try:
        from common.tree_sitter_manager import create_parser, get_language_safe

        class _Adapter:
            pass

        adapter = _Adapter()
        adapter.language_name = lang
        adapter.language = get_language_safe(lang)
        adapter.parser = create_parser(lang)

        module = importlib.import_module(module_path)
        parser_class = getattr(module, class_name)
        parser_instance = parser_class(adapter)
        parser_instance.index_source = False
        _parser_cache[lang] = parser_instance
        return parser_instance
    except Exception as e:
        logger.warning("Failed to load parser for %s: %s", lang, e)
        return None


# ── AST parsing ────────────────────────────────────────────────────────────────


def _collect_call_names(node) -> Set[str]:
    """Recursively collect every called function/method name from a tree-sitter node."""
    names: Set[str] = set()
    if node.type in ("call", "call_expression", "new_expression"):
        fn = node.child_by_field_name("function") or node.child_by_field_name("constructor")
        if fn:
            if fn.type in ("identifier", "simple_identifier"):
                try:
                    names.add(fn.text.decode("utf-8"))
                except Exception:
                    pass
            elif fn.type in (
                "attribute",
                "member_expression",
                "field_expression",
                "qualified_identifier",
                "scoped_identifier",
            ):
                attr = (
                    fn.child_by_field_name("attribute")
                    or fn.child_by_field_name("property")
                    or fn.child_by_field_name("field")
                )
                if attr:
                    try:
                        names.add(attr.text.decode("utf-8"))
                    except Exception:
                        pass
    for child in node.children:
        names |= _collect_call_names(child)
    return names


def _ast_parse_file(file_path: str, source: str) -> Dict:
    """Parse one file with tree-sitter.

    Returns:
        imports    — list of dicts from _find_imports (name, full_import_name/module, ...)
        functions  — list of dicts from _find_functions (name, line_number, source, ...)
        classes    — list of dicts from _find_classes (name, line_number, ...)
        call_sites — set of bare function/method names called anywhere in the file
    """
    empty = {"imports": [], "functions": [], "classes": [], "call_sites": set()}
    ext = Path(file_path).suffix.lower()
    lang = EXT_TO_LANG.get(ext)
    if not lang:
        return empty

    parser = _get_lang_parser(lang)
    if not parser:
        return empty

    try:
        tree = parser.parser.parse(source.encode("utf-8"))
        root = tree.root_node
        skip = get_call_skip(lang)

        imports = parser._find_imports(root) if hasattr(parser, "_find_imports") else []
        functions = parser._find_functions(root) if hasattr(parser, "_find_functions") else []
        classes = parser._find_classes(root) if hasattr(parser, "_find_classes") else []
        call_sites = _collect_call_names(root) - skip

        return {
            "imports": imports,
            "functions": functions,
            "classes": classes,
            "call_sites": call_sites,
        }
    except Exception as e:
        logger.warning("AST parse failed for %s: %s", file_path, e)
        return empty


# ── Context building ───────────────────────────────────────────────────────────


def _build_review_prompt(
    repo_id: str,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    diff_text: str,
    hunks: Dict[str, List[Tuple[int, int]]],
    pr_files: Dict[str, str],
    imported_symbols: Dict[str, List[Dict]],
    graph_section: str,
    prev_run: dict | None,
    files_changed: List[str],
) -> str:
    """Build the complete prompt sent to the LLM reviewer."""
    parts: List[str] = []

    # Header
    parts.append(f"## PR #{pr_number} in {repo_id}")
    parts.append("")
    if pr_title:
        parts.append(f"**Title:** {pr_title}")
    if pr_body and pr_body.strip():
        parts.append(f"**Description:** {pr_body.strip()}")
    parts.append("")

    # Hunk map — the ONLY lines the agent should report issues on
    parts.append("## Changed Hunks (report issues ONLY on these lines)")
    parts.append("")
    for fp in files_changed:
        file_hunks = hunks.get(fp, [])
        ranges = ", ".join(f"lines {s}–{e}" for s, e in file_hunks) or "entire file"
        parts.append(f"- `{fp}`: {ranges}")
    parts.append("")
    parts.append("---")
    parts.append("")

    # The diff
    parts.append("## Diff")
    parts.append("")
    parts.append("```diff")
    parts.append(diff_text[:60_000] if len(diff_text) > 60_000 else diff_text)
    if len(diff_text) > 60_000:
        parts.append("# ... truncated")
    parts.append("```")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Full post-PR file contents
    parts.append("## Full File Contents (post-PR state)")
    parts.append("")
    for fp, source in pr_files.items():
        ext = Path(fp).suffix.lstrip(".")
        parts.append(f"### `{fp}`")
        parts.append(f"```{ext}")
        parts.append(source[:20_000] if len(source) > 20_000 else source)
        if len(source) > 20_000:
            parts.append("# ... truncated")
        parts.append("```")
        parts.append("")
    parts.append("---")
    parts.append("")

    # Referenced external symbols (context only — exact source of imported functions/classes)
    if imported_symbols:
        parts.append("## Referenced External Symbols (context only — do NOT report issues here)")
        parts.append("")
        for fp, syms in imported_symbols.items():
            ext = Path(fp).suffix.lstrip(".")
            parts.append(f"### From `{fp}`")
            parts.append("")
            for sym in syms:
                parts.append(f"#### `{sym['name']}` ({sym['kind']})")
                src = sym["source"]
                parts.append(f"```{ext}")
                parts.append(src[:3_000] if len(src) > 3_000 else src)
                if len(src) > 3_000:
                    parts.append("# ... truncated")
                parts.append("```")
                parts.append("")
        parts.append("---")
        parts.append("")

    # Graph context (callers, dependencies)
    if graph_section and graph_section != "No graph context available.":
        parts.append("## Graph Context (callers & dependencies)")
        parts.append("")
        parts.append(graph_section)
        parts.append("")
        parts.append("---")
        parts.append("")

    # Previous issues
    if prev_run:
        prev_section = _build_prev_issues_context(prev_run)
        if prev_section:
            parts.append(prev_section)
            parts.append("")
            parts.append("---")
            parts.append("")

    # Walk-through instruction
    parts.append(
        "**Files changed in this PR** (walk_through MUST contain one entry per file below):"
    )
    for f in files_changed:
        parts.append(f"- `{f}`")

    return "\n".join(parts)


def _is_lint_issue(issue: dict) -> bool:
    """Lint issues have titles like '[ruff] E501: ...' or '[bandit] B105: ...'.
    They come from deterministic tools and should NOT be re-evaluated as
    still_open/fixed by the LLM on re-review — they will simply rerun.
    """
    title = issue.get("title") or ""
    return title.startswith("[") and "]" in title


def _build_prev_issues_context(prev_run: dict) -> str:
    """Format previous LLM findings for injection into the Synthesizer prompt.

    Lint issues are excluded — they are re-detected fresh each run.
    """
    all_issues = prev_run.get("issues", [])
    # Only LLM-originated issues — filter out lint tool findings
    issues = [i for i in all_issues if not _is_lint_issue(i)]
    if not issues:
        return ""

    run_num = prev_run.get("runNumber", "?")
    lines = [
        f"## Previous Review Findings (Run #{run_num})",
        "",
        "**Instructions**: For EVERY issue listed below you MUST include it in your output",
        "with one of these statuses:",
        "  - `still_open` — the same problem is still present in the code (not fixed)",
        "  - `fixed`       — the code has been changed to address this specific problem",
        "",
        "Issues you find that are NOT in this list → status: `new`",
        "",
    ]
    for idx, issue in enumerate(issues, 1):
        title = issue.get("title") or ""
        fp = issue.get("file") or ""
        line_start = issue.get("line_start", "?")
        line_end = issue.get("line_end") or line_start
        category = issue.get("category") or "?"
        confidence = issue.get("confidence", "?")
        desc = (issue.get("description") or "")[:300]
        issue_type = issue.get("issue_type") or "Issue"

        loc = (
            f"`{fp}:{line_start}`" if line_start == line_end else f"`{fp}:{line_start}-{line_end}`"
        )
        lines.append(f"{idx}. **[{issue_type}] {title}** — {loc}")
        lines.append(f"   Category: `{category}` · Confidence: {confidence}/10")
        if desc:
            lines.append(f"   > {desc}")
        lines.append("")
    return "\n".join(lines)


def _diff_summary_for_file(diff_text: str, file_path: str) -> str:
    """Return a human-readable one-liner describing what changed in *file_path*.

    Used as a fallback walkthrough description when the LLM agent was unable to
    produce one (e.g. rate-limit failure).  Heuristics applied in order:

    1. Detect function/symbol renames (``-def foo`` / ``+def foo_new``).
    2. Detect lock-file / dependency-manifest changes.
    3. Fall back to ``+N / -M lines`` counts.
    """
    # Collect the hunk lines for this specific file
    in_file = False
    added: list[str] = []
    removed: list[str] = []
    add_count = rem_count = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            in_file = file_path in line
            continue
        if not in_file:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            add_count += 1
            added.append(line[1:].strip())
        elif line.startswith("-") and not line.startswith("---"):
            rem_count += 1
            removed.append(line[1:].strip())

    # Heuristic 1 — function/class rename
    def_re = re.compile(r"^(?:def|class|async def)\s+(\w+)")

    old_defs = {m.group(1) for l in removed if (m := def_re.match(l))}
    new_defs = {m.group(1) for l in added if (m := def_re.match(l))}
    renamed = old_defs & {n for n in new_defs if n not in old_defs}
    if not renamed and old_defs and new_defs and old_defs != new_defs:
        old_name = next(iter(old_defs - new_defs), None)
        new_name = next(iter(new_defs - old_defs), None)
        if old_name and new_name:
            return f"Rename `{old_name}` → `{new_name}`"

    # Heuristic 2 — lock / dependency file
    ext = file_path.rsplit(".", 1)[-1].lower()
    base = file_path.rsplit("/", 1)[-1].lower()
    if ext in {"lock", "toml", "cfg", "ini"} or base in {
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "cargo.toml",
    }:
        pkgs = [re.search(r'name\s*=\s*"([^"]+)"', l) for l in added]
        pkg_names = [m.group(1) for m in pkgs if m]
        if pkg_names:
            return f"Add dependency: {', '.join(pkg_names[:3])}"
        return f"Update dependencies (+{add_count} / -{rem_count} lines)"

    # Heuristic 3 — variable / import renames in Python
    import_old = [re.search(r"import\s+(\w+)", l) for l in removed]
    import_new = [re.search(r"import\s+(\w+)", l) for l in added]
    old_imports = {m.group(1) for m in import_old if m}
    new_imports = {m.group(1) for m in import_new if m}
    if old_imports != new_imports and old_imports and new_imports:
        return f"Update imports: {', '.join(old_imports)} → {', '.join(new_imports)}"

    # Fallback — raw counts
    if add_count == 0 and rem_count == 0:
        return "No line changes detected"
    return f"+{add_count} / -{rem_count} lines changed"


def _parse_files_changed(
    diff_text: str,
    issues: List[Issue],
    walk_through: List[str] | None = None,
) -> List[FileSummary]:
    """Derive FileSummary list from diff line counts."""
    file_stats: Dict[str, Tuple[int, int]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git a/"):
            parts = line.split(" ", 3)
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    current_file = b_path[2:]
                else:
                    a_path = parts[2]
                    current_file = a_path[2:] if a_path.startswith("a/") else b_path
                file_stats.setdefault(current_file, (0, 0))
            continue
        elif line.startswith("+++ b/"):
            current_file = line[6:]
            file_stats.setdefault(current_file, (0, 0))
            continue

        if current_file:
            if line.startswith("+") and not line.startswith("+++"):
                a, r = file_stats[current_file]
                file_stats[current_file] = (a + 1, r)
            elif line.startswith("-") and not line.startswith("---"):
                a, r = file_stats[current_file]
                file_stats[current_file] = (a, r + 1)

    file_to_desc: Dict[str, str] = {}
    for entry in walk_through or []:
        if " — " in entry:
            wt_file, wt_desc = entry.split(" — ", 1)
            file_to_desc[wt_file.strip().strip("`")] = wt_desc.strip()
    for issue in issues:
        if issue.file not in file_to_desc:
            file_to_desc[issue.file] = issue.title

    return [
        FileSummary(
            file=fp,
            lines_added=a,
            lines_removed=r,
            what_changed=file_to_desc.get(fp, "Modified"),
        )
        for fp, (a, r) in file_stats.items()
    ]


# ── Main pipeline ──────────────────────────────────────────────────────────────


async def execute_pr_review(
    owner: str, repo: str, pr_number: int, neo4j: Neo4jClient | None = None
) -> None:
    """Full PR review pipeline."""
    uid = firebase_service.lookup_uid_by_github_username(owner)
    repo_id = f"{owner}/{repo}"

    try:
        try:
            pr_number = int(pr_number)
            if pr_number <= 0:
                raise ValueError
        except (ValueError, TypeError):
            logger.error("Invalid pr_number %r — aborting", pr_number)
            return

        logger.info("Starting review for %s/%s#%s", owner, repo, pr_number)
        review_dir = make_review_dir(owner, repo, pr_number)

        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.RUNNING,
                ),
            )

        # ── Step 1: Fetch diff + PR metadata + head SHA ────────────────────
        gh = get_github_client()
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )
        if not diff_text:
            logger.warning("Empty diff — skipping review")
            if uid:
                firebase_service.upsert_pr_metadata(
                    uid,
                    owner,
                    repo,
                    pr_number,
                    PRMetadata(
                        owner=owner,
                        repo=repo,
                        pr_number=pr_number,
                        repo_id=repo_id,
                        review_status=PrReviewStatus.COMPLETED,
                    ),
                )
            return

        pr_title = pr_info.get("title", "")
        pr_body = pr_info.get("body", "") or ""
        logger.info("Diff: %d chars | head: %s | title: %r", len(diff_text), head_sha[:7], pr_title)

        write_step(
            review_dir,
            "01_diff.md",
            "\n".join(
                [
                    "# Step 1 — Raw Diff",
                    f"**PR:** {owner}/{repo}#{pr_number}",
                    f"**Title:** {pr_title}",
                    f"**Chars:** {len(diff_text)}",
                    "",
                    pr_body or "*(no description)*",
                    "",
                    "```diff",
                    diff_text,
                    "```",
                ]
            ),
        )

        # ── Step 2: Parse hunks ────────────────────────────────────────────
        changes = parse_unified_diff(diff_text)
        files_changed = list({c["file_path"] for c in changes})

        # hunk map: file → list of (start_line, end_line)
        hunks: Dict[str, List[Tuple[int, int]]] = {}
        for c in changes:
            hunks.setdefault(c["file_path"], []).append(
                (int(c.get("start_line", 1)), int(c.get("end_line", 1)))
            )

        logger.info("Parsed %d hunks across %d files", len(changes), len(files_changed))

        write_step(
            review_dir,
            "02_parsed_diff.md",
            "\n".join(
                [
                    "# Step 2 — Parsed Hunks",
                    f"**Files:** {len(files_changed)}  |  **Hunks:** {len(changes)}",
                    "",
                    *[
                        f"- `{fp}`: " + ", ".join(f"lines {s}–{e}" for s, e in hunks.get(fp, []))
                        for fp in files_changed
                    ],
                ]
            ),
        )

        # ── Step 3: Fetch full file contents at head SHA ───────────────────
        _raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in files_changed],
            return_exceptions=True,
        )
        pr_files: Dict[str, str] = {
            fp: content
            for fp, content in zip(files_changed, _raw)
            if not isinstance(content, Exception) and content is not None
        }
        logger.info("Fetched %d/%d PR files", len(pr_files), len(files_changed))

        # ── Step 4: AST-parse every PR file ───────────────────────────────
        ast_results: Dict[str, Dict] = {
            fp: _ast_parse_file(fp, source) for fp, source in pr_files.items()
        }

        all_functions = {fn["name"] for ast in ast_results.values() for fn in ast["functions"]}
        all_call_sites = {name for ast in ast_results.values() for name in ast["call_sites"]}
        external_calls = all_call_sites - all_functions  # called but not defined in PR files

        write_step(
            review_dir,
            "03_ast_results.md",
            "\n".join(
                [
                    "# Step 3 — AST Results",
                    "",
                    *[
                        f"## `{fp}`\n"
                        f"- Imports: {len(ast['imports'])}\n"
                        f"- Functions: {len(ast['functions'])} ({', '.join(f['name'] for f in ast['functions'][:10])})\n"
                        f"- Classes: {len(ast['classes'])}\n"
                        f"- Call-sites: {len(ast['call_sites'])}"
                        for fp, ast in ast_results.items()
                    ],
                    "",
                    f"## External call-sites (called but not defined in PR files): {len(external_calls)}",
                    *[f"- `{n}`" for n in sorted(external_calls)],
                ]
            ),
        )

        # ── Step 5: Look up referenced external symbols from Neo4j ────────
        # For every name called in PR files but not defined there, query the
        # graph by function / class / variable name and grab its source.
        if neo4j is None:
            neo4j = get_neo4j_client()
        query_service = CodeSearchService(neo4j)

        imported_symbols: Dict[str, List[Dict]] = {}  # file_path → [{name, kind, source}]

        for name in list(external_calls)[:30]:  # cap to keep queries bounded
            try:
                rows = query_service.find_by_function_name(name, repo_id=repo_id)
                kind = "function"
                if not rows:
                    rows = query_service.find_by_class_name(name, repo_id=repo_id)
                    kind = "class"
                if not rows:
                    rows = query_service.find_by_variable_name(name, repo_id=repo_id)
                    kind = "variable"
                for row in rows[:1]:
                    src = (
                        row.get("source")
                        or row.get("source_code")
                        or row.get("context")
                        or row.get("value", "")
                    )
                    if src and not row.get("is_dependency"):
                        path = row.get("path", "unknown")
                        imported_symbols.setdefault(path, []).append(
                            {"name": name, "kind": kind, "source": src}
                        )
                        break
            except Exception:
                pass

        total_imported_syms = sum(len(v) for v in imported_symbols.values())
        logger.info(
            "Imported symbols: %d symbols from %d files (checked %d external names)",
            total_imported_syms,
            len(imported_symbols),
            len(external_calls),
        )

        write_step(
            review_dir,
            "04_imported_sources.md",
            "\n".join(
                [
                    "# Step 4 — Imported Symbols (from Neo4j)",
                    "",
                    f"**External names checked:** {len(external_calls)}  |  "
                    f"**Symbols resolved:** {total_imported_syms}",
                    "",
                    *[
                        f"- `{fp}`: " + ", ".join(f"`{s['name']}` ({s['kind']})" for s in syms)
                        for fp, syms in imported_symbols.items()
                    ],
                ]
            ),
        )

        # ── Step 6: Neo4j graph context (callers + dependencies) ───────────
        # query_service already created in Step 5
        graph_context = query_service.get_diff_context_enhanced(repo_id, changes)
        graph_section = build_graph_context_section(
            graph_context,
            changed_files=set(pr_files.keys()),  # exclude stale pre-PR symbols for changed files
        )

        total_callers = sum(len(e.get("callers", [])) for e in graph_context.get("callers", []))
        all_symbol_names = [s.get("name", "") for s in graph_context.get("affected_symbols", [])]

        if total_callers > 10 or len(all_symbol_names) > 5:
            risk_level = "high"
        elif total_callers > 3 or len(all_symbol_names) > 2:
            risk_level = "medium"
        else:
            risk_level = "low"

        logger.info(
            "Graph: %d affected symbols, %d callers, risk=%s",
            len(all_symbol_names),
            total_callers,
            risk_level,
        )

        write_step(
            review_dir,
            "05_graph_context.md",
            "\n".join(
                [
                    "# Step 5 — Graph Context",
                    "",
                    f"**Affected symbols:** {len(all_symbol_names)}",
                    f"**Callers:** {total_callers}",
                    f"**Risk level:** {risk_level}",
                    "",
                    "## Rendered section (sent to agent)",
                    graph_section,
                ]
            ),
        )

        # ── Step 7: Load previous Firestore run ────────────────────────────
        prev_run: dict | None = None
        if uid:
            prev_run = firebase_service.get_latest_review_run(uid, owner, repo, pr_number)
            if prev_run:
                logger.info("Loaded previous run #%s", prev_run.get("runNumber"))
        else:
            logger.warning(
                "No Firestore repo metadata for %s/%s — skipping history",
                owner,
                repo,
            )

        # ── Step 8: Build review prompt ────────────────────────────────────
        review_prompt = _build_review_prompt(
            repo_id=repo_id,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_body=pr_body,
            diff_text=diff_text,
            hunks=hunks,
            pr_files=pr_files,
            imported_symbols=imported_symbols,
            graph_section=graph_section,
            prev_run=prev_run,
            files_changed=files_changed,
        )

        write_step(
            review_dir,
            "06_review_prompt.md",
            "\n".join(
                [
                    "# Step 6 — Review Prompt",
                    "",
                    review_prompt,
                ]
            ),
        )

        # ── Step 9: Lint + LLM review in parallel ─────────────────────────
        logger.info("Step 9: running lint + LLM review in parallel")
        lint_issues_raw, review_results = await asyncio.gather(
            run_lint(pr_files),
            run_review(review_prompt, repo_id, pr_number, query_service),
        )

        # Lint: keep all issues from PR files (full file analysis — no line filter)
        # Kept SEPARATE from LLM issues — merged only when saving to Firestore
        diff_file_set: set[str] = set(pr_files.keys())
        lint_issues = [i for i in lint_issues_raw if i.file in diff_file_set]
        logger.info(
            "Lint: %d total → %d in PR files",
            len(lint_issues_raw),
            len(lint_issues),
        )

        # Ensure all PR files appear in walkthrough.
        # Always run — even if the agent returned an empty list, every changed
        # file must have an entry so the comment table is never blank.
        covered = {
            e.split(" — ")[0].strip().strip("`") for e in (review_results.walk_through or [])
        }
        for fp in files_changed:
            if fp not in covered:
                review_results.walk_through = list(review_results.walk_through or [])
                review_results.walk_through.append(
                    f"{fp} — {_diff_summary_for_file(diff_text, fp)}"
                )

        review_results.files_changed_summary = _parse_files_changed(
            diff_text, review_results.issues, walk_through=review_results.walk_through
        )
        logger.info(
            "Review done: %d issues (%d open)",
            len(review_results.issues),
            sum(1 for i in review_results.issues if i.status != "fixed"),
        )

        # ── Step 10: Save to Firestore ─────────────────────────────────────
        if uid:
            firebase_service.save_review_run(
                uid,
                owner,
                repo,
                pr_number,
                ReviewRunData(
                    issues=[i.model_dump() for i in lint_issues + review_results.issues],
                    positive_findings=review_results.positive_findings,
                    summary=review_results.summary,
                    files_changed=files_changed,
                    repo_id=repo_id,
                    pr_number=pr_number,
                ),
            )

        reconciled = ReconciledReview(
            issues=review_results.issues,  # LLM issues only
            positive_findings=review_results.positive_findings,
            summary=review_results.summary,
        )
        context_data = ContextData(
            files_changed=files_changed,
            modified_symbols=all_symbol_names,
            total_callers=total_callers,
            risk_level=risk_level,
        )

        # Build the set of all lines GitHub will accept for inline comments.
        # GitHub's review comment API accepts any line that appears in a diff hunk
        # (added, removed, OR context lines) — not just '+' lines.
        # We use the hunk header ranges from parse_unified_diff which give us the
        # new-file line span [start, end] for each hunk, inclusive.
        valid_diff_lines: set[tuple[str, int]] = {
            (fp, ln)
            for fp, file_hunks in hunks.items()
            for s, e in file_hunks
            for ln in range(s, e + 1)
        }

        inline_candidates = [
            i for i in reconciled.issues if i.status in ("new", "still_open") and i.confidence >= 7
        ]
        has_blocking = any(i.category in ("bug", "security") for i in inline_candidates)
        review_event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

        # Post one inline comment per issue — GitHub allows multiple comment threads
        # on the same line when using individual POST /pulls/{pr}/comments calls.
        inline_posted = inline_skipped = 0
        for issue in inline_candidates:
            if (issue.file, issue.line_start) in valid_diff_lines:
                body = format_inline_comment(issue)
                ok = await gh.post_inline_comment(
                    owner, repo, pr_number, head_sha, issue.file, issue.line_start, body
                )
                if ok:
                    inline_posted += 1
                else:
                    inline_skipped += 1
            else:
                inline_skipped += 1

        logger.info("Inline: %d posted, %d skipped", inline_posted, inline_skipped)
        debug_info = {
            "tool_rounds_used": review_results.tool_rounds_used,
            "lint_raw_count": len(lint_issues_raw),
            "lint_on_diff_count": len(lint_issues),
            "lint_raw": [i.model_dump() for i in lint_issues_raw],
        }

        review_body = format_review_summary(
            reconciled,
            context_data,
            pr_number,
            lint_issues=lint_issues,
            files_changed_summary=review_results.files_changed_summary,
            walk_through=review_results.walk_through,
            inline_posted=inline_posted,
            inline_skipped=inline_skipped,
            raw_agent_json=review_results.raw_agent_json,
            debug_info=debug_info,
        )

        try:
            await gh.post_pr_review(owner, repo, pr_number, head_sha, review_body, review_event)
            logger.info("Posted PR review (%s) on %s/%s#%s", review_event, owner, repo, pr_number)
        except Exception:
            logger.warning("PR review API failed — falling back to issue comment")
            fallback = format_github_comment(
                reconciled,
                context_data,
                pr_number,
                lint_issues=lint_issues,
                files_changed_summary=review_results.files_changed_summary,
                walk_through=review_results.walk_through,
                raw_agent_json=review_results.raw_agent_json,
                debug_info=debug_info,
            )
            await gh.post_comment(owner, repo, pr_number, fallback)
            logger.info("Posted fallback comment on %s/%s#%s", owner, repo, pr_number)

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        logger.error("Review pipeline failed:\n%s", traceback.format_exc())

        try:
            gh = get_github_client()
            await gh.post_comment(
                owner,
                repo,
                pr_number,
                f"🚨 **BugViper Review Failed**\n\nThere was a critical error running the review pipeline:\n```text\n{error_msg}\n```\nPlease check the server logs for more details or try running the review again.",
            )
        except Exception as gh_e:
            logger.error("Failed to post failure comment: %s", gh_e)

        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.FAILED,
                    failed_reasons=[error_msg],
                ),
            )
    else:
        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.COMPLETED,
                ),
            )
