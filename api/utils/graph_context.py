"""Build markdown graph-context sections for the LLM prompt."""

from pathlib import Path
from typing import Any

from common.languages import EXT_TO_LANG


def _lang_hint(file_path: str) -> str:
    return EXT_TO_LANG.get(Path(file_path).suffix.lower(), "")


def build_graph_context_section(
    graph_context: dict[str, Any], changed_files: set[str] | None = None
) -> str:
    """Build a markdown section from graph context for the LLM prompt.

    Args:
        graph_context: Graph context dict from Neo4j.
        changed_files: Set of file paths in the PR diff. Symbols from these files
            are excluded from the affected_symbols section because the full-file
            contents already show the authoritative post-PR source — the graph
            snapshot is always pre-PR state and would be stale/misleading.
    """
    parts: list[str] = []
    _changed = changed_files or set()

    # ── Affected symbols (functions, methods, classes) ───────────────────────
    affected = graph_context.get("affected_symbols", [])
    # Only keep symbols from files NOT in the PR diff.
    # Files in the diff already have full post-PR source in the prompt.
    affected = [
        s for s in affected if (s.get("change_file") or s.get("file_path", "")) not in _changed
    ]
    if affected:
        parts.append(
            f"**{len(affected)} symbol(s) from dependent files (pre-PR graph snapshot — for call-graph context only):**"
        )
        parts.append("")

        for sym in affected[:20]:
            sym_type = sym.get("type", "function")
            name = sym.get("name", "?")
            file_path = sym.get("change_file") or sym.get("file_path", "?")
            start = sym.get("start_line", "?")
            end = sym.get("end_line", "?")
            source = sym.get("source") or ""
            args = sym.get("args") or ""

            type_badge = {"class": "Class", "method": "Method", "function": "Function"}.get(
                sym_type, sym_type.capitalize()
            )

            if args:
                parts.append(f"### `{name}({args})` — {type_badge}")
            else:
                parts.append(f"### `{name}` — {type_badge}")

            parts.append(f"**File:** `{file_path}` (lines {start}–{end})")

            if sym.get("docstring"):
                parts.append(f"**Doc:** {sym['docstring'][:200]}")

            if source:
                if len(source) > 10000:
                    source = source[:10000] + "\n# ... (source truncated)"
                parts.append(f"```{_lang_hint(file_path)}")
                parts.append(source)
                parts.append("```")

            # For classes: list their methods with source so the agent knows
            # exactly what each method does without guessing.
            methods = sym.get("methods") or []
            if methods:
                parts.append(f"**Methods ({len(methods)}):**")
                for m in methods:
                    m_name = m.get("name", "?")
                    m_args = m.get("args") or ""
                    m_line = m.get("line_number", "?")
                    m_source = m.get("source") or ""
                    m_doc = m.get("docstring") or ""

                    parts.append(f"#### `{m_name}({m_args})` — line {m_line}")
                    if m_doc:
                        parts.append(f"*{m_doc[:150]}*")
                    if m_source:
                        if len(m_source) > 5000:
                            m_source = m_source[:5000] + "\n# ... (truncated)"
                        parts.append(f"```{_lang_hint(file_path)}")
                        parts.append(m_source)
                        parts.append("```")

            parts.append("")

    # ── Callers: who calls the changed symbols ────────────────────────────────
    callers = graph_context.get("callers", [])
    if callers:
        parts.append("**Downstream callers (code that calls the changed symbols):**")
        parts.append("")
        for entry in callers[:10]:
            sym_name = entry.get("symbol", "?")
            sym_type = entry.get("symbol_type", "")
            parts.append(f"#### `{sym_name}` ({sym_type}) is called by:")
            for c in entry.get("callers", [])[:5]:
                caller_name = c.get("caller_name", "?")
                caller_type = c.get("caller_type", "function")
                caller_path = c.get("caller_path", "?")
                call_line = c.get("call_line", "")
                line_info = f" at line {call_line}" if call_line else ""
                parts.append(f"  - `{caller_name}` ({caller_type}) in `{caller_path}`{line_info}")
        parts.append("")

    # ── Dependencies: what the changed symbols call ───────────────────────────
    dependencies = graph_context.get("dependencies", [])
    if dependencies:
        parts.append("**Upstream dependencies (what changed symbols call):**")
        parts.append("")
        for dep in dependencies[:10]:
            sym_name = dep.get("symbol", "?")
            parts.append(f"#### `{sym_name}` calls:")
            for d in dep.get("dependencies", [])[:8]:
                called_name = d.get("called_name", "?")
                called_type = d.get("called_type", "function")
                called_path = d.get("called_path") or "?"
                call_line = d.get("call_line", "")
                line_info = f" (line {call_line})" if call_line else ""
                parts.append(f"  - `{called_name}` ({called_type}) in `{called_path}`{line_info}")
        parts.append("")

    # ── Imports: in-repo symbols imported by changed files ───────────────────
    imports = graph_context.get("imports", [])
    if imports:
        parts.append(f"**Imported in-repo symbols ({len(imports)}):**")
        parts.append("")
        for imp in imports[:10]:
            name = imp.get("name", "?")
            imp_type = imp.get("type", "?")
            imp_path = imp.get("path", "?")
            imp_source = imp.get("source") or ""
            imp_doc = imp.get("docstring") or ""

            parts.append(f"#### `{name}` ({imp_type}) — `{imp_path}`")
            if imp_doc:
                parts.append(f"*{imp_doc[:150]}*")
            if imp_source:
                if len(imp_source) > 5000:
                    imp_source = imp_source[:5000] + "\n# ... (truncated)"
                parts.append(f"```{_lang_hint(imp_path)}")
                parts.append(imp_source)
                parts.append("```")
            parts.append("")

    # ── Class hierarchy ───────────────────────────────────────────────────────
    hierarchy = graph_context.get("class_hierarchy", [])
    if hierarchy:
        parts.append("**Class hierarchy:**")
        parts.append("")
        for entry in hierarchy[:5]:
            cls_name = entry.get("class", "?")
            parents = entry.get("parents", [])
            children = entry.get("children", [])
            methods = entry.get("methods", [])

            parts.append(f"#### `{cls_name}`")
            if parents:
                parent_names = ", ".join(p.get("parent_class", "?") for p in parents[:3])
                parts.append(f"  Inherits from: `{parent_names}`")
            if children:
                child_names = ", ".join(c.get("child_class", "?") for c in children[:3])
                parts.append(f"  Inherited by: `{child_names}`")
            if methods:
                method_sigs = ", ".join(f"`{m.get('method_name', '?')}`" for m in methods[:6])
                parts.append(f"  Methods: {method_sigs}")
        parts.append("")

    return "\n".join(parts) if parts else "No graph context available."
