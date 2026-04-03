"""Context builder for code review.

This module provides utilities to build the markdown context that is passed
to the 3-node agent. It includes:
- Diff formatting
- File content with line numbers
- Hunk ranges
- AST summary
- Previous issues
- Code samples from Neo4j
"""

from pathlib import Path
from typing import Any, Dict, List

from api.models.ast_results import ParsedFile
from common.diff_line_mapper import (
    format_file_with_line_numbers,
    get_hunk_ranges,
    get_valid_comment_lines,
)
from common.languages import EXT_TO_LANG


def build_file_context(
    file_path: str,
    full_diff: str,
    full_file_content: str,
    file_ast: Any,
    previous_issues: str = "",
    explorer_context: str = "",
    code_samples: Dict[str, List[dict]] | None = None,
) -> str:
    """Build the full file context for the review prompt.

    Args:
        file_path: Path to the file
        full_diff: Unified diff
        full_file_content: Post-PR file content
        file_ast: Parsed AST
        previous_issues: Previous issues for this file
        explorer_context: External context from graph exploration
        code_samples: Code samples from Neo4j

    Returns:
        Markdown string with all context
    """
    ast_summary = _build_ast_summary(file_path, full_file_content, file_ast)

    parts = [
        f"# Review Agent Prompt: {file_path}",
        "",
        "## File Under Review",
        f"**File**: {file_path}",
        "",
        "## Raw Unified Diff",
        "```diff",
        full_diff or "(no diff available)",
        "```",
        "",
        "## POST-PR File Content (with line numbers)",
        "```",
        format_file_with_line_numbers(full_file_content) or "(no file content available)",
        "```",
        "",
        _build_hunk_ranges_section(full_diff),
        "",
        "## AST Summary",
        ast_summary or "(No AST)",
        "",
        "## Previous Issues",
        previous_issues or "No previous issues for this file.",
        "",
        "## Explorer Context (Neo4j Graph Intelligence)",
        explorer_context or "No external context available",
        "Context from code search and graph queries about this file and related entities",
    ]

    definitions_section = _render_definitions_section(code_samples)
    if definitions_section:
        parts.append("")
        parts.append(definitions_section)

    return "\n".join(parts)


def _build_hunk_ranges_section(diff_text: str) -> str:
    """Build a section describing which line ranges are part of the diff hunks."""
    hunk_ranges = get_hunk_ranges(diff_text)
    valid_lines = get_valid_comment_lines(diff_text)

    if not hunk_ranges:
        return ""

    lines = ["## Diff Hunk Line Ranges", ""]
    lines.append("Line ranges in the POST-PR file that are part of the diff:")

    for file_path, ranges in sorted(hunk_ranges.items()):
        range_parts = [str(s) if s == e else f"{s}-{e}" for s, e in ranges]
        lines.append(f"- `{file_path}` hunk ranges: {', '.join(range_parts)}")

        file_valid = valid_lines.get(file_path, set())
        if file_valid:
            sorted_valid = sorted(file_valid)
            lines.append(f"  Valid comment lines: {sorted_valid}")

    lines.append("")
    lines.append("**Within hunk ranges → inline comment. Outside hunk ranges → regular comment.**")

    return "\n".join(lines)


def _build_ast_summary(file_path: str, content: str, ast: Any) -> str:
    """Build a text summary of the AST for a file."""
    if not ast or ast.error:
        return f"Could not parse AST for {file_path}: {ast.error if ast else 'Unknown error'}"

    lines = [f"# {file_path}"]

    if hasattr(ast, "functions") and ast.functions:
        lines.append(f"\n## Functions ({len(ast.functions)})")
        for f in ast.functions[:20]:
            complexity = getattr(f, "cyclomatic_complexity", 1)
            risk = "HIGH" if complexity > 10 else "MEDIUM" if complexity > 5 else "LOW"
            lines.append(
                f"- `{f.name}()` at line {f.line_number} (complexity: {complexity}, risk: {risk})"
            )
            if hasattr(f, "docstring") and f.docstring:
                lines.append(f"  Doc: {f.docstring[:100]}")

    if hasattr(ast, "classes") and ast.classes:
        lines.append(f"\n## Classes ({len(ast.classes)})")
        for c in ast.classes[:10]:
            lines.append(f"- `{c.name}` at line {c.line_number}")
            if hasattr(c, "bases") and c.bases:
                lines.append(f"  Inherits: {', '.join(c.bases)}")

    if hasattr(ast, "imports") and ast.imports:
        lines.append(f"\n## Imports ({len(ast.imports)})")
        for imp in ast.imports[:10]:
            alias_note = f" as {imp.alias}" if hasattr(imp, "alias") and imp.alias else ""
            lines.append(f"- `{imp.name}`{alias_note} at line {imp.line_number}")

    if hasattr(ast, "call_sites") and ast.call_sites:
        lines.append(f"\n## Call Sites ({len(ast.call_sites)})")
        for call in ast.call_sites[:15]:
            ctx = call.context if hasattr(call, "context") and call.context else "function"
            lines.append(
                f"- `{call.full_name}()` at line {call.line_number} (in {call.class_context}.{ctx})"
            )

    return "\n".join(lines[:100])


def _render_definitions_section(
    code_samples: Dict[str, List[dict]] | None,
) -> str:
    """Render code samples (classes, functions, imports) as markdown sections."""
    if not code_samples:
        return ""

    parts = []
    for sample_type, samples in code_samples.items():
        if not samples:
            continue

        type_label = sample_type.capitalize()
        names_list = ", ".join(f"`{s.get('name', 'Unknown')}`" for s in samples)
        parts.append(f"## {type_label} Definitions: {names_list}")

        for sample in samples:
            name = sample.get("name", "Unknown")
            source = sample.get("source_code") or ""
            docstring = sample.get("docstring") or ""
            path = sample.get("path", "")
            parts.append(f"### `{name}` ({path})")
            if docstring:
                parts.append(f"Docstring: {docstring}")
            if source:
                parts.append(f"```python\n{source}\n```")
            parts.append("")
        parts.append("")

    return "\n".join(parts)


def build_file_diff_from_patch(file_path: str, patch: str) -> str:
    """Build a unified diff string from a patch."""
    if not patch:
        return f"(No diff for {file_path})"
    header = f"diff --git a/{file_path} b/{file_path}\n--- a/{file_path}\n+++ b/{file_path}\n"
    return header + patch


def format_previous_issues(previous_issues: List[dict]) -> str:
    """Format previous issues for the prompt."""
    if not previous_issues:
        return "No previous issues for this file."

    lines = []
    for i, issue in enumerate(previous_issues, 1):
        title = issue.get("title", "Unknown issue")
        file = issue.get("file", "")
        line = issue.get("line_start", "?")
        category = issue.get("category", "?")
        desc = issue.get("description", "")[:200]
        status = issue.get("status", "new")

        lines.append(f"### Previous Issue #{i} (status: {status})")
        lines.append(f"**Title:** {title}")
        lines.append(f"**Location:** `{file}:{line}`")
        lines.append(f"**Category:** {category}")
        if desc:
            lines.append(f"**Description:** {desc}")
        lines.append("")

    return "\n".join(lines)


def get_file_language(file_path: str) -> str:
    """Get the language identifier for syntax highlighting."""
    ext = Path(file_path).suffix.lower()
    return EXT_TO_LANG.get(ext, "text")
