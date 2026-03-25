"""
PR Review Service
=================
Pipeline:
  1. Fetch diff + PR metadata + head SHA
  2. Parse hunks → changed files + line ranges
  3. Fetch full file contents at head SHA (post-PR state)
  4. AST-parse every PR file with full extraction (source, docstring, call sites with context)
  5. Build structured AST summary (functions, classes, call graph, external calls)
  6. Build Explorer prompt with PR-specific investigation goals
  7. Run Explorer agent to intelligently fetch context from Graph DB
  8. Run lint + LLM review in parallel
  9. Post comment, save run to Firestore
"""

import asyncio
import importlib
import logging
import re
import traceback
from pathlib import Path
from typing import Dict, List, Tuple

from api.models.ast_results import (
    ASTSummary,
    CallSite,
    ClassDef,
    FunctionDef,
    Import,
    ParsedFile,
    summarize_for_explorer,
)
from api.services.firebase_service import firebase_service
from api.services.lint_service import run_lint
from api.utils.comment_formatter import (
    format_github_comment,
    format_inline_comment,
    format_review_summary,
)
from code_review_agent.agent.runner import run_review
from code_review_agent.config import token_limits
from code_review_agent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview
from common.debug_writer import make_review_dir, write_step
from common.diff_line_mapper import (
    build_hunk_summary_for_prompt,
    get_valid_comment_lines,
    validate_issue_line,
)
from common.diff_parser import parse_unified_diff
from common.firebase_models import PRMetadata, PrReviewStatus, ReviewRunData
from common.github_client import get_github_client
from common.languages import EXT_TO_LANG, LANG_PARSER_REGISTRY
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)

# ── Parser cache ───────────────────────────────────────────────────────────────

_parser_cache: Dict[str, object] = {}


def _get_lang_parser(lang: str, index_source: bool = True):
    """Get language-specific parser with source extraction enabled."""
    if lang in _parser_cache:
        parser = _parser_cache[lang]
        parser.index_source = index_source
        return parser
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
        parser_instance.index_source = index_source
        _parser_cache[lang] = parser_instance
        return parser_instance
    except Exception as e:
        logger.warning("Failed to load parser for %s: %s", lang, e)
        return None


# ── AST parsing with full extraction ────────────────────────────────────────────


def _ast_parse_file_full(file_path: str, source: str) -> ParsedFile:
    """Parse one file with full AST extraction including source and docstring."""
    ext = Path(file_path).suffix.lower()
    lang = EXT_TO_LANG.get(ext)
    if not lang:
        return ParsedFile(
            path=file_path,
            language="unknown",
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=f"Unsupported extension: {ext}",
        )

    parser = _get_lang_parser(lang, index_source=True)
    if not parser:
        return ParsedFile(
            path=file_path,
            language=lang,
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=f"No parser for {lang}",
        )

    try:
        tree = parser.parser.parse(source.encode("utf-8"))
        root = tree.root_node

        # Extract full data using parser methods (with index_source=True)
        raw_imports = parser._find_imports(root) if hasattr(parser, "_find_imports") else []
        raw_functions = (
            parser._find_functions(root, index_source=True)
            if hasattr(parser, "_find_functions")
            else []
        )
        raw_classes = (
            parser._find_classes(root, index_source=True)
            if hasattr(parser, "_find_classes")
            else []
        )
        raw_calls = parser._find_calls(root) if hasattr(parser, "_find_calls") else []

        # Convert to structured data
        functions = []
        for f in raw_functions:
            func = FunctionDef(
                name=f.get("name", ""),
                line_number=f.get("line_number", 0),
                end_line=f.get("end_line", f.get("line_number", 0)),
                args=f.get("args", []),
                cyclomatic_complexity=f.get("cyclomatic_complexity", 1),
                context=f.get("context") or "",
                context_type=f.get("context_type") or "",
                class_context=f.get("class_context") or "",
                decorators=f.get("decorators", []),
                docstring=f.get("docstring"),
                source=f.get("source", ""),
                is_method=bool(f.get("class_context")),
            )
            functions.append(func)

        classes = []
        for c in raw_classes:
            cls = ClassDef(
                name=c.get("name", ""),
                line_number=c.get("line_number", 0),
                end_line=c.get("end_line", c.get("line_number", 0)),
                bases=c.get("bases", []),
                context=c.get("context") or "",
                decorators=c.get("decorators", []),
                docstring=c.get("docstring"),
                source=c.get("source", ""),
            )
            classes.append(cls)

        imports = []
        for imp in raw_imports:
            imports.append(
                Import(
                    name=imp.get("name", ""),
                    full_import_name=imp.get("full_import_name") or imp.get("source", ""),
                    line_number=imp.get("line_number", 0),
                    alias=imp.get("alias"),
                )
            )

        call_sites = []
        for call in raw_calls:
            context_info = call.get("context") or (None, None, None)
            if not isinstance(context_info, (list, tuple)):
                context_info = (None, None, None)
            call_sites.append(
                CallSite(
                    name=call.get("name", ""),
                    full_name=call.get("full_name", call.get("name", "")),
                    line_number=call.get("line_number", 0),
                    args=call.get("args", []),
                    context=context_info[0] if context_info[0] else "",
                    context_type=context_info[1] if len(context_info) > 1 else "",
                    class_context=call.get("class_context") or "",
                )
            )

        return ParsedFile(
            path=file_path,
            language=lang,
            functions=functions,
            classes=classes,
            imports=imports,
            call_sites=call_sites,
        )
    except Exception as e:
        logger.warning("AST parse failed for %s: %s", file_path, e)
        return ParsedFile(
            path=file_path,
            language=lang,
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=str(e),
        )


# ── Context building ───────────────────────────────────────────────────────────


def _build_ast_context_section(
    ast_summary: ASTSummary, hunks: Dict[str, List[Tuple[int, int]]]
) -> str:
    """Build a structured AST context section for the Explorer prompt.

    This replaces the previous approach of dumping all external symbols blindly.
    Instead, we provide:
    - Changed symbols with their source/docstring (from PR files)
    - Internal call graph (which PR functions call which PR functions)
    - External calls with context (who's calling what external symbol)
    - Investigation hints for the Explorer

    Uses token_limits from config for truncation.
    """
    parts: List[str] = []

    # ── Changed Functions ───────────────────────────────────────────────────────
    functions = ast_summary.functions
    if functions:
        parts.append("## Changed Functions (from PR files)")
        parts.append("")
        for fn in functions[:30]:  # Configurable limit
            name = fn.get("name", "?")
            file = fn.get("file", "?")
            line = fn.get("line", 0)
            args = ", ".join(fn.get("args", []))
            complexity = fn.get("complexity", 1)
            class_ctx = fn.get("class_context", "")
            docstring = fn.get("docstring", "")
            source = fn.get("source", "") or ""

            # Determine risk based on complexity and hunk overlap
            risk = "simple" if complexity <= 5 else "moderate" if complexity <= 10 else "complex"

            if class_ctx:
                parts.append(f"### `{class_ctx}.{name}({args})` — {file}:{line}")
            else:
                parts.append(f"### `{name}({args})` — {file}:{line}")

            parts.append(f"**Complexity:** {complexity} ({risk})")
            if docstring:
                parts.append(f"**Docstring:** {docstring[: token_limits.docstring_max_chars]}...")

            if source:
                # Truncate source to keep context manageable
                max_src = token_limits.function_source_max_chars
                src_preview = source[:max_src] if len(source) > max_src else source
                lang = Path(file).suffix.lstrip(".")
                parts.append(f"```{lang}")
                parts.append(src_preview)
                if len(source) > max_src:
                    parts.append("# ... truncated")
                parts.append("```")
            parts.append("")

    # ── Changed Classes ────────────────────────────────────────────────────────
    classes = ast_summary.classes
    if classes:
        parts.append("## Changed Classes (from PR files)")
        parts.append("")
        for cls in classes[:15]:
            name = cls.get("name", "?")
            file = cls.get("file", "?")
            line = cls.get("line", 0)
            bases = cls.get("bases", [])
            docstring = cls.get("docstring", "")
            source = cls.get("source", "") or ""

            parts.append(f"### `{name}` — {file}:{line}")
            if bases:
                parts.append(f"**Inherits from:** {', '.join(bases)}")
            if docstring:
                parts.append(f"**Docstring:** {docstring[: token_limits.docstring_max_chars]}...")

            if source:
                max_src = token_limits.class_source_max_chars
                src_preview = source[:max_src] if len(source) > max_src else source
                lang = Path(file).suffix.lstrip(".")
                parts.append(f"```{lang}")
                parts.append(src_preview)
                if len(source) > max_src:
                    parts.append("# ... truncated")
                parts.append("```")
            parts.append("")

    # ── External Calls (symbols called but not defined in PR) ────────────────────
    external_calls = ast_summary.external_calls
    if external_calls:
        parts.append("## External Calls (called but not defined in PR files)")
        parts.append("")
        parts.append(
            "These symbols are called from the PR but defined elsewhere. The Explorer agent"
        )
        parts.append(
            "should use `find_callers`, `find_function`, or `find_class` tools to investigate."
        )
        parts.append("")

        for ec in external_calls[: token_limits.external_calls_max_count]:
            name = ec.get("name", "?")
            count = ec.get("call_count", 0)
            callers = ec.get("callers", [])

            parts.append(f"### `{name}` — called {count} time(s)")
            if callers:
                parts.append("**Called from:**")
                for c in callers[: token_limits.external_callers_max_count]:
                    caller = c.get("caller", "?")
                    cls = c.get("class", "")
                    ln = c.get("line", 0)
                    if cls:
                        parts.append(f"  - `{cls}.{caller}()` at line {ln}")
                    elif caller:
                        parts.append(f"  - `{caller}()` at line {ln}")
                    else:
                        parts.append(f"  - line {ln}")
            parts.append("")

    # ── Internal Call Graph ────────────────────────────────────────────────────
    call_graph = ast_summary.internal_call_graph
    if call_graph:
        parts.append("## Internal Call Graph (who calls what within this PR)")
        parts.append("")
        parts.append("Use this to trace how changes propagate through the codebase:")
        parts.append("")
        for key, data in list(call_graph.items())[:20]:
            name = data.get("name", "?")
            file = data.get("file", "?")
            calls = data.get("calls", [])
            if calls:
                callees = ", ".join(f"`{c['name']}()`" for c in calls[:5])
                parts.append(f"- `{name}()` in `{file}` → calls: {callees}")
        parts.append("")

    # ── Investigation Hints ─────────────────────────────────────────────────────
    parts.append("## Investigation Hints for Explorer")
    parts.append("")
    parts.append("Use the Neo4j tools to investigate:")
    parts.append("")

    # Find high-complexity functions
    complex_funcs = [f for f in functions if f.get("complexity", 1) > 10]
    if complex_funcs:
        parts.append("**High complexity functions to review carefully:**")
        for f in complex_funcs[:5]:
            parts.append(f"  - `{f['name']}` (complexity: {f.get('complexity', 1)})")
        parts.append("")

    # Find external calls that might need investigation
    high_usage_external = [e for e in external_calls if e.get("call_count", 0) > 3]
    if high_usage_external:
        parts.append("**High-usage external symbols (check API contracts, error handling):**")
        for e in high_usage_external[:5]:
            parts.append(f"  - `{e['name']}` (called {e.get('call_count', 0)} times)")
        parts.append("")

    # Find classes with inheritance
    inherited_classes = [c for c in classes if c.get("bases")]
    if inherited_classes:
        parts.append("**Classes with inheritance (use `get_class_hierarchy`):**")
        for c in inherited_classes[:5]:
            parts.append(f"  - `{c['name']}` extends {', '.join(c.get('bases', []))}")
        parts.append("")

    return "\n".join(parts) if parts else ""


def _build_explorer_goals(ast_summary: ASTSummary, files_changed: List[str]) -> str:
    """Build PR-specific investigation goals for the Explorer agent."""
    functions = ast_summary.functions
    classes = ast_summary.classes
    external_calls = ast_summary.external_calls

    changed_fn_names = [f["name"] for f in functions[:20]]
    changed_class_names = [c["name"] for c in classes[:10]]

    goals = []
    goals.append("## PR Context")
    goals.append(f"**Changed files:** {', '.join(f'`{f}`' for f in files_changed[:10])}")

    if changed_fn_names:
        goals.append(f"**Changed functions:** {', '.join(f'`{n}`' for n in changed_fn_names)}")

    if changed_class_names:
        goals.append(f"**Changed classes:** {', '.join(f'`{n}`' for n in changed_class_names)}")

    goals.append("")
    goals.append("## Investigation Tasks")

    if changed_fn_names:
        goals.append("**Trace impact of changed functions:**")
        for fn in changed_fn_names[:10]:
            goals.append(f"- `{fn}`: use `find_callers` to find who uses it outside this PR")
        goals.append("")

    if changed_class_names:
        goals.append("**Check class inheritance:**")
        for cls in changed_class_names[:5]:
            goals.append(f"- `{cls}`: use `get_class_hierarchy` to check inheritance")
        goals.append("")

    high_usage_external = [e for e in external_calls if e.get("call_count", 0) > 5]
    if high_usage_external:
        goals.append("**High-usage external calls:**")
        for e in high_usage_external[:3]:
            goals.append(f"- `{e['name']}`: use `find_imports` to see where it's used")
        goals.append("")

    return "\n".join(goals)


def _build_review_prompt(
    repo_id: str,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    diff_text: str,
    hunks: Dict[str, List[Tuple[int, int]]],
    imported_symbols: Dict[str, List[Dict]],
    ast_context_section: str,
    explorer_goals: str,
    prev_run: dict | None,
    files_changed: List[str],
    diff_text_for_lines: str = "",
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

    # ── Critical: Line number guidance for inline comments ──────────────────────
    # Tell the LLM EXACTLY which line numbers can receive inline comments.
    # This prevents the LLM from reporting issues on deleted lines or lines not in the diff.
    if diff_text_for_lines:
        try:
            valid_lines_summary = build_hunk_summary_for_prompt(diff_text_for_lines)
            parts.append(valid_lines_summary)
            parts.append("")
            parts.append(
                "**IMPORTANT**: When reporting issues, use line numbers from the "
                "NEW file (post-PR state)."
            )
            parts.append(
                "Line numbers must match the ranges listed above. If your issue is on a line"
            )
            parts.append("not in the diff, use the nearest valid line number from the list above.")
            parts.append("")
        except Exception as e:
            logger.warning("Failed to build hunk summary: %s", e)

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
    max_diff = token_limits.diff_max_chars
    parts.append(diff_text[:max_diff] if len(diff_text) > max_diff else diff_text)
    if len(diff_text) > max_diff:
        parts.append("# ... truncated")
    parts.append("```")
    parts.append("")
    parts.append("---")
    parts.append("")

    # AST Context section (changed symbols with source/docstring)
    if ast_context_section:
        parts.append(ast_context_section)
        parts.append("---")
        parts.append("")

    # Explorer goals (PR-specific investigation hints)
    if explorer_goals:
        parts.append(explorer_goals)
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
                max_sym = token_limits.imported_symbol_max_chars
                parts.append(f"```{ext}")
                parts.append(src[:max_sym] if len(src) > max_sym else src)
                if len(src) > max_sym:
                    parts.append("# ... truncated")
                parts.append("```")
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
    """Format previous LLM findings for injection into the Review Agent prompt.

    Lint issues are excluded — they are re-detected fresh each run.

    The Review Agent MUST:
    1. Include every previous issue in its output with status `fixed` or `still_open`
    2. Mark `fixed` only if the code clearly changed to address the specific problem
    3. Mark `still_open` if the problem persists or if unsure
    4. Fixed issues are NOT shown to users — only still_open and new issues appear in comments
    """
    all_issues = prev_run.get("issues", [])
    issues = [i for i in all_issues if not _is_lint_issue(i)]
    if not issues:
        return ""

    run_num = prev_run.get("runNumber", "?")
    lines = [
        f"## Previous Review Findings (Run #{run_num})",
        "",
        "### CRITICAL: You Must Track Previous Issues",
        "",
        "For EVERY issue below, you MUST include it in your output with one of these statuses:",
        "",
        "- **`fixed`**: The code has clearly changed to address this specific problem.",
        "  Example: Previous issue was 'division by zero on line 50' →",
        "  Code now shows `if divisor != 0` before division → mark as `fixed`.",
        "  YOU MUST STILL INCLUDE IT with status `fixed`, but it will NOT be shown to the user.",
        "",
        "- **`still_open`**: The problem persists. Either:",
        "  - The problematic code pattern is still present",
        "  - Not enough changed to confirm a fix",
        "  When in doubt, mark `still_open`.",
        "",
        "Issues you discover that are NOT in this list → use status `new`.",
        "",
        "---",
        "",
    ]

    for idx, issue in enumerate(issues, 1):
        title = issue.get("title") or ""
        fp = issue.get("file") or ""
        line_start = issue.get("line_start", "?")
        line_end = issue.get("line_end") or line_start
        category = issue.get("category") or "?"
        confidence = issue.get("confidence", "?")
        desc = (issue.get("description") or "")[:500]
        issue_type = issue.get("issue_type") or "Issue"
        suggestion = issue.get("suggestion") or ""
        code_snippet = issue.get("code_snippet") or ""

        loc = (
            f"`{fp}:{line_start}`" if line_start == line_end else f"`{fp}:{line_start}-{line_end}`"
        )
        lines.append(f"### Issue {idx}: [{issue_type}] {title}")
        lines.append(f"**Location:** {loc}")
        lines.append(f"**Category:** `{category}` · **Confidence:** {confidence}/10")
        lines.append("")
        if desc:
            lines.append(f"**Description:** {desc}")
            lines.append("")
        if code_snippet:
            lines.append("**Code:**")
            lines.append("```")
            lines.append(code_snippet[:200])
            lines.append("```")
            lines.append("")
        if suggestion:
            lines.append(f"**Suggested Fix:** {suggestion[:200]}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(
        "**Remember:** Include ALL above issues in output. Mark each `fixed` or `still_open`."
    )
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

    old_defs = {m.group(1) for line in removed if (m := def_re.match(line))}
    new_defs = {m.group(1) for line in added if (m := def_re.match(line))}
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
        pkgs = [re.search(r'name\s*=\s*"([^"]+)"', line) for line in added]
        pkg_names = [m.group(1) for m in pkgs if m]
        if pkg_names:
            return f"Add dependency: {', '.join(pkg_names[:3])}"
        return f"Update dependencies (+{add_count} / -{rem_count} lines)"

    # Heuristic 3 — variable / import renames in Python
    import_old = [re.search(r"import\s+(\w+)", line) for line in removed]
    import_new = [re.search(r"import\s+(\w+)", line) for line in added]
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

        # ── Step 4: AST-parse every PR file with full extraction ───────────────
        parsed_files: List[ParsedFile] = [
            _ast_parse_file_full(fp, source) for fp, source in pr_files.items()
        ]

        # Build structured summary for Explorer
        ast_summary = summarize_for_explorer(parsed_files)
        stats = ast_summary.stats

        logger.info(
            "AST parsed: %d functions, %d classes, %d external calls",
            stats.get("total_functions", 0),
            stats.get("total_classes", 0),
            stats.get("total_external_calls", 0),
        )

        write_step(
            review_dir,
            "03_ast_results.md",
            "\n".join(
                [
                    "# Step 3 — AST Results (Full Extraction)",
                    "",
                    f"**Functions:** {stats.get('total_functions', 0)}",
                    f"**Classes:** {stats.get('total_classes', 0)}",
                    f"**Imports:** {stats.get('total_imports', 0)}",
                    f"**External calls:** {stats.get('total_external_calls', 0)}",
                    "",
                    "## Changed Functions",
                    *[
                        f"- `{f['name']}` in `{f['file']}:{f['line']}` "
                        f"(complexity: {f.get('complexity', 1)})"
                        for f in ast_summary.functions[:15]
                    ],
                    "",
                    "## Changed Classes",
                    *[
                        f"- `{c['name']}` in `{c['file']}:{c['line']}`"
                        for c in ast_summary.classes[:10]
                    ],
                    "",
                    "## External Calls (top 20)",
                    *[
                        f"- `{e['name']}` — called {e.get('call_count', 0)} time(s)"
                        for e in ast_summary.external_calls[:20]
                    ],
                ]
            ),
        )

        # ── Step 5: Build Explorer prompt with PR-specific goals ──────────────
        # The Explorer will use Neo4j tools to investigate callers, dependencies
        # for the changed symbols and external calls identified in AST analysis.
        if neo4j is None:
            neo4j = get_neo4j_client()
        query_service = CodeSearchService(neo4j)

        # Build AST context section (changed symbols with source/docstring)
        ast_context_section = _build_ast_context_section(ast_summary, hunks)

        # Build Explorer goals (PR-specific investigation hints)
        explorer_goals = _build_explorer_goals(ast_summary, files_changed)

        write_step(
            review_dir,
            "04_explorer_goals.md",
            explorer_goals,
        )

        # ── Step 6: Optional — Pre-fetch high-priority external symbol sources ──
        # Only fetch sources for high-usage external symbols (called >= threshold times)
        # The Explorer can fetch the rest on demand using tools.
        imported_symbols: Dict[str, List[Dict]] = {}
        high_usage_external = [
            e
            for e in ast_summary.external_calls[:15]
            if e.get("call_count", 0) >= token_limits.high_usage_call_threshold
        ]

        for ext in high_usage_external:
            name = ext.get("name")
            if not name:
                continue
            try:
                rows = query_service.find_by_function_name(name, repo_id=repo_id)
                kind = "function"
                if not rows:
                    rows = query_service.find_by_class_name(name, repo_id=repo_id)
                    kind = "class"
                for row in rows[:1]:
                    src = row.get("source") or row.get("source_code") or ""
                    if src and not row.get("is_dependency"):
                        path = row.get("path", "unknown")
                        imported_symbols.setdefault(path, []).append(
                            {
                                "name": name,
                                "kind": kind,
                                "source": src[: token_limits.imported_symbol_max_chars],
                            }
                        )
                        break
            except Exception:
                pass

        logger.info(
            "Pre-fetched %d high-usage external symbol sources",
            sum(len(v) for v in imported_symbols.values()),
        )

        write_step(
            review_dir,
            "05_prefetched_syources.md",
            "\n".join(
                [
                    "# Step 5 — Pre-fetched External Sources",
                    "",
                    f"**High-usage symbols pre-fetched:** "
                    f"{sum(len(v) for v in imported_symbols.values())}",
                    "",
                    *[
                        f"- `{path}`: " + ", ".join(f"`{s['name']}`" for s in syms)
                        for path, syms in imported_symbols.items()
                    ],
                ]
            ),
        )

        # Calculate risk level from AST analysis
        complex_funcs = [f for f in ast_summary.functions if f.get("complexity", 1) > 10]
        total_external_calls = stats.get("total_external_calls", 0)

        if len(complex_funcs) > 3 or total_external_calls > 20:
            risk_level = "high"
        elif len(complex_funcs) > 0 or total_external_calls > 5:
            risk_level = "medium"
        else:
            risk_level = "low"

        logger.info(
            "AST analysis: %d complex functions, %d external calls, risk=%s",
            len(complex_funcs),
            total_external_calls,
            risk_level,
        )

        # ── Step 7: Load previous Firestore run ────────────────────────────────────
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
            imported_symbols=imported_symbols,
            ast_context_section=ast_context_section,
            explorer_goals=explorer_goals,
            prev_run=prev_run,
            files_changed=files_changed,
            diff_text_for_lines=diff_text,
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

        # Build PR context summary for Review Agent (separate from Explorer goals)
        # This goes to the Synthesis agent, not the Explorer
        pr_context_summary = f"""## PR Summary
- Files changed: {len(files_changed)}
- Functions: {stats.get("total_functions", 0)}
- Classes: {stats.get("total_classes", 0)}
- External calls: {stats.get("total_external_calls", 0)}
- Risk level: {risk_level}
"""

        lint_issues_raw, review_results = await asyncio.gather(
            run_lint(pr_files),
            run_review(
                review_prompt=review_prompt,
                repo_id=repo_id,
                pr_number=pr_number,
                query_service=query_service,
                explorer_goals=explorer_goals,  # Injected into Explorer system prompt
                pr_context=pr_context_summary,  # Passed to Synthesis agent
                output_dir=review_dir,  # For debug output
            ),
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

        # Build symbol names from AST summary
        all_symbol_names = [f["name"] for f in ast_summary.functions[:20]]
        all_symbol_names.extend(c["name"] for c in ast_summary.classes[:10])

        context_data = ContextData(
            files_changed=files_changed,
            modified_symbols=all_symbol_names,
            total_callers=stats.get("total_external_calls", 0),
            risk_level=risk_level,
        )

        # ── Build valid comment lines from diff ────────────────────────────────
        # Parse the actual diff to find which lines can receive inline comments.
        # GitHub only allows comments on:
        #   - Added lines (+)
        #   - Context lines (unchanged lines shown in diff)
        # NOT on deleted lines (-)
        valid_comment_lines = get_valid_comment_lines(diff_text)

        # Log for debugging
        total_valid_lines = sum(len(lines) for lines in valid_comment_lines.values())
        logger.info(
            "Valid comment lines: %d files, %d total lines",
            len(valid_comment_lines),
            total_valid_lines,
        )

        inline_candidates = [
            i for i in reconciled.issues if i.status in ("new", "still_open") and i.confidence >= 7
        ]
        has_blocking = any(i.category in ("bug", "security") for i in inline_candidates)
        review_event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

        # ── Post inline comments with line validation ──────────────────────────
        # For each issue, validate/adjust the line number to a valid diff position.
        # If the LLM reported a line not in the diff, try to find the nearest valid line.
        inline_posted = inline_skipped = inline_adjusted = 0
        for issue in inline_candidates:
            # Validate and possibly adjust line numbers
            valid_start, valid_end = validate_issue_line(
                issue.file,
                issue.line_start,
                issue.line_end,
                valid_comment_lines,
            )

            if valid_start is None:
                # No valid line found for this issue
                logger.debug(
                    "Skipping issue '%s' at %s:%d - line not in diff",
                    issue.title,
                    issue.file,
                    issue.line_start,
                )
                inline_skipped += 1
                continue

            # Check if line was adjusted
            if valid_start != issue.line_start:
                logger.debug(
                    "Adjusted line for '%s': %s:%d -> %d",
                    issue.title,
                    issue.file,
                    issue.line_start,
                    valid_start,
                )
                inline_adjusted += 1
                # Update the issue's line numbers for accurate reporting
                issue.line_start = valid_start
                if issue.line_end and valid_end:
                    issue.line_end = valid_end

            body = format_inline_comment(issue)
            ok = await gh.post_inline_comment(
                owner, repo, pr_number, head_sha, issue.file, valid_start, body
            )
            if ok:
                inline_posted += 1
            else:
                inline_skipped += 1

        logger.info(
            "Inline comments: %d posted, %d adjusted, %d skipped",
            inline_posted,
            inline_adjusted,
            inline_skipped,
        )
        debug_info = {
            "tool_rounds_used": review_results.tool_rounds_used,
            "lint_raw_count": len(lint_issues_raw),
            "lint_on_diff_count": len(lint_issues),
            "lint_raw": [i.model_dump() for i in lint_issues_raw],
            "inline_adjusted": inline_adjusted,
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
                "🚨 **BugViper Review Failed**\n\n"
                "There was a critical error running the review pipeline:\n"
                f"```text\n{error_msg}\n```\n"
                "Please check the server logs for more details or try running the review again.",
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
