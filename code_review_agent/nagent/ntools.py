import asyncio
import json

from langchain_core.tools import tool

from common.embedder import embed_texts
from db.code_serarch_layer import CodeSearchService

# Artifact item shape: {"path": str, "line_number": int|None, "name": str|None, "type": str|None}
# Every tool returns (content_for_llm: str, sources: list[dict]).
# LangChain stores sources in ToolMessage.artifact — zero parsing needed downstream.

_S = dict  # shorthand for a source dict


def get_code_review_tools(query_service: CodeSearchService, repo_id: str | None = None) -> list:

    def _src(
        path: str, line: int | None = None, name: str | None = None, typ: str | None = None
    ) -> _S:
        return {"path": path, "line_number": line, "name": name, "type": typ}

    # ── 1. Unified fulltext search ────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def search_code(query: str) -> tuple[str, list[_S]]:
        """Search functions, classes, variables and file content by name or keyword.
        Use this first when you need to locate any symbol or code snippet.
        Returns: type, name, file path, line number.
        """
        results = query_service.search_code(query, repo_id=repo_id)
        if not results:
            return f"No results for: '{query}'", []
        lines = [
            f"[{r.get('type')}] {r.get('name')}  →  {r.get('path')}:{r.get('line_number')}"
            for r in results
        ]
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), r.get("type"))
            for r in results
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 2. Peek file lines ────────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def peek_code(path: str, line: int, above: int = 10, below: int = 10) -> tuple[str, list[_S]]:
        """Read source code around a specific line in a file.
        Use after search_code or find_by_line to see the actual implementation.
        Args:
            path:  File path as returned by any search tool.
            line:  Anchor line number (1-indexed).
            above: Lines to show above the anchor (default 10, max 200).
            below: Lines to show below the anchor (default 10, max 200).
        Returns: annotated code snippet with line numbers.
        """
        result = query_service.peek_file_lines(path, line, min(above, 200), min(below, 200))
        if "error" in result:
            return f"Error: {result['error']}", []
        header = (
            f"File: {result['path']}  "
            f"(anchor: line {result['anchor_line']}, "
            f"total: {result['total_lines']} lines)"
        )
        sep = "-" * 60
        body = ""
        for entry in result["window"]:
            prefix = "→" if entry["is_anchor"] else " "
            body += (
                f"{prefix} {entry['line_number']:>4} │ {entry['content'].replace(chr(9), '    ')}\n"
            )
        return f"{header}\n{sep}\n{body}", [_src(path, line, typ="file")]

    # ── 3. Semantic vector search ─────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    async def semantic_search(question: str) -> tuple[str, list[_S]]:
        """Find semantically similar code using vector embeddings.
        Use when you need to find code by meaning rather than exact keywords.
        Args:
            question: A natural language description of what you're looking for.
        Returns: ranked code snippets with name, path, score, and docstring.
        """
        vectors: list[list[float]] = await asyncio.to_thread(embed_texts, [question])
        results = query_service.semantic_search(vectors[0], repo_id=repo_id)
        if not results:
            return "No semantic matches found.", []
        lines = []
        for r in results[:10]:
            score = f"{float(r.get('score') or 0):.3f}"
            lines.append(
                f"[{r.get('type')}] {r.get('name')}  "
                f"score={score}  →  {r.get('path')}:{r.get('line_number')}"
            )
            if r.get("docstring"):
                lines.append(f"    {r['docstring'][:120]}")
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), r.get("type"))
            for r in results[:10]
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 4. Find function by name ──────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_function(name: str, fuzzy: bool = False) -> tuple[str, list[_S]]:
        """Find a function by its exact name (or fuzzy match).
        Use when you know the function name and want its definition and location.
        Args:
            name:  Function name.
            fuzzy: Set True for partial / fuzzy matching.
        Returns: function name, file path, line number, docstring, source snippet.
        """
        results = query_service.find_by_function_name(name, fuzzy, repo_id=repo_id)
        if not results:
            return f"Function '{name}' not found.", []
        lines = []
        for r in results:
            lines.append(f"[function] {r.get('name')}  →  {r.get('path')}:{r.get('line_number')}")
            if r.get("docstring"):
                lines.append(f"  docstring: {r['docstring'][:200]}")
            if r.get("source") or r.get("source_code"):
                src = (r.get("source_code") or r.get("source") or "")[:300]
                lines.append(f"  source:\n    {src}")
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), "function")
            for r in results
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 5. Find class by name ─────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_class(name: str, fuzzy: bool = False) -> tuple[str, list[_S]]:
        """Find a class by its exact name (or fuzzy match).
        Use when you need the definition, methods, or inheritance of a class.
        Args:
            name:  Class name.
            fuzzy: Set True for partial / fuzzy matching.
        Returns: class name, file path, line number, docstring, source snippet.
        """
        results = query_service.find_by_class_name(name, fuzzy, repo_id=repo_id)
        if not results:
            return f"Class '{name}' not found.", []
        lines = []
        for r in results:
            lines.append(f"[class] {r.get('name')}  →  {r.get('path')}:{r.get('line_number')}")
            if r.get("docstring"):
                lines.append(f"  docstring: {r['docstring'][:200]}")
            if r.get("source") or r.get("source_code"):
                src = (r.get("source_code") or r.get("source") or "")[:300]
                lines.append(f"  source:\n    {src}")
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), "class")
            for r in results
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 6. Find variable by name ──────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_variable(name: str) -> tuple[str, list[_S]]:
        """Find a variable or constant by name (substring match).
        Use when you need to locate a specific variable definition.
        Returns: variable name, file path, line number.
        """
        results = query_service.find_by_variable_name(name, repo_id=repo_id)
        if not results:
            return f"Variable '{name}' not found.", []
        lines = [f"• {r.get('name')}  →  {r.get('path')}:{r.get('line_number')}" for r in results]
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), "variable")
            for r in results
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 7. Find by content ────────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_by_content(query: str) -> tuple[str, list[_S]]:
        """Search function/class/variable bodies for a specific code pattern or string.
        Use when you're looking for a particular implementation detail or usage pattern.
        Returns: symbol name, type, file path, line number.
        """
        results = query_service.find_by_content(query, repo_id=repo_id)
        if not results:
            return f"No content matches for: '{query}'", []
        lines = [
            f"[{r.get('type')}] {r.get('name')}  →  {r.get('path')}:{r.get('line_number')}"
            for r in results
        ]
        sources = [
            _src(r["path"], r.get("line_number"), r.get("name"), r.get("type"))
            for r in results
            if r.get("path")
        ]
        return "\n".join(lines), sources

    # ── 8. Find by file line ──────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_by_line(query: str, limit: int = 20) -> tuple[str, list[_S]]:
        """Search raw file content line-by-line for a specific string or pattern.
        Use when you need to find where a literal string appears across all files.
        Pair with peek_code to read context around a hit.
        Returns: file path, line number, and the matching line.
        """
        results = query_service.search_file_content(query, min(limit, 100), repo_id=repo_id)
        if not results:
            return f"No line matches for: '{query}'", []
        lines = [
            f"{r.get('path')}:{r.get('line_number')}  {r.get('match_line', '')}" for r in results
        ]
        sources = [_src(r["path"], r.get("line_number")) for r in results if r.get("path")]
        return "\n".join(lines), sources

    # ── 9. Find module ────────────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_module(name: str) -> tuple[str, list[_S]]:
        """Find a module (package, directory, or external dependency) by name.
        Use to understand what files import a given package/module.
        Returns: module name, language, files that import it.
        """
        results = query_service.find_by_module_name(name)
        if not results:
            return f"Module '{name}' not found.", []
        lines = []
        for r in results:
            lines.append(f"[module] {r.get('module_name')}  lang={r.get('lang')}")
            for f in (r.get("files") or [])[:10]:
                lines.append(f"  imported by: {f}")
        return "\n".join(lines), []

    # ── 10. Find imports ──────────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_imports(name: str) -> tuple[str, list[_S]]:
        """Find all import statements that reference a module, symbol, or alias by name.
        Use to understand where a dependency is used across the codebase.
        Returns: module name, imported name, alias, file path, line number.
        """
        results = query_service.find_imports(name, repo_id=repo_id)
        if not results:
            return f"No imports found for: '{name}'", []
        lines = [
            (
                f"{r.get('module_name')} → {r.get('imported_name')}  "
                f"alias={r.get('alias')}  {r.get('path')}:{r.get('line_number')}"
            )
            for r in results
        ]
        sources = [_src(r["path"], r.get("line_number")) for r in results if r.get("path")]
        return "\n".join(lines), sources

    # ── 11. Find method usages ────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_method_usages(method_name: str) -> tuple[str, list[_S]]:
        """Find all places where a specific method or function is called.
        Use to understand how widely a function is used before modifying it.
        Returns: method definition, callers, and call sites.
        """
        result = query_service.find_method_usages(method_name, repo_id=repo_id)
        if not result:
            return f"No usages found for method: '{method_name}'", []
        lines = []
        sources: list[_S] = []
        if result.get("method"):
            m = result["method"]
            lines.append(f"Definition: {m.get('name')}  →  {m.get('path')}:{m.get('line_number')}")
            if m.get("path"):
                sources.append(_src(m["path"], m.get("line_number"), m.get("name"), "function"))
        callers = result.get("callers") or []
        if callers:
            lines.append(f"\nCallers ({len(callers)}):")
            for c in callers:
                lines.append(f"  • {c.get('caller_name')}  →  {c.get('path')}:{c.get('call_line')}")
                if c.get("path"):
                    sources.append(_src(c["path"], c.get("call_line"), c.get("caller_name")))
        else:
            lines.append("No callers found.")
        return "\n".join(lines), sources

    # ── 12. Find callers ──────────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def find_callers(symbol_name: str) -> tuple[str, list[_S]]:
        """Find all functions/methods that call a given symbol.
        Use to trace call chains and understand dependencies.
        Returns: symbol definitions and all callers with their file locations.
        """
        result = query_service.find_callers(symbol_name, repo_id=repo_id)
        if not result:
            return f"No callers found for: '{symbol_name}'", []
        lines = []
        sources: list[_S] = []
        for defn in result.get("definitions") or []:
            lines.append(
                f"Definition: [{defn.get('type')}] {defn.get('name')}  →  "
                f"{defn.get('path')}:{defn.get('line_number')}"
            )
            if defn.get("path"):
                sources.append(
                    _src(defn["path"], defn.get("line_number"), defn.get("name"), defn.get("type"))
                )
        callers = result.get("callers") or []
        if callers:
            lines.append(f"\nCallers ({len(callers)}):")
            for c in callers:
                lines.append(f"  • {c.get('name')}  →  {c.get('path')}:{c.get('line_number')}")
                if c.get("path"):
                    sources.append(_src(c["path"], c.get("line_number"), c.get("name")))
        else:
            lines.append("No callers found.")
        return "\n".join(lines), sources

    # ── 13. Class hierarchy ───────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def get_class_hierarchy(class_name: str) -> tuple[str, list[_S]]:
        """Get the inheritance tree for a class — both parent and child classes.
        Use when you need to understand OOP structure or polymorphism.
        Returns: the class, its parents, its children, and their file locations.
        """
        result = query_service.get_class_hierarchy(class_name, repo_id=repo_id)
        if not result:
            return f"Class '{class_name}' not found.", []
        lines = []
        sources: list[_S] = []
        if result.get("class"):
            c = result["class"]
            lines.append(f"[class] {c.get('name')}  →  {c.get('path')}:{c.get('line_number')}")
            if c.get("path"):
                sources.append(_src(c["path"], c.get("line_number"), c.get("name"), "class"))
        for parent in result.get("parents") or []:
            lines.append(f"  ↑ inherits from: {parent.get('name')}  ({parent.get('path')})")
            if parent.get("path"):
                sources.append(
                    _src(parent["path"], parent.get("line_number"), parent.get("name"), "class")
                )
        for child in result.get("children") or []:
            lines.append(f"  ↓ subclassed by: {child.get('name')}  ({child.get('path')})")
            if child.get("path"):
                sources.append(
                    _src(child["path"], child.get("line_number"), child.get("name"), "class")
                )
        return "\n".join(lines) if lines else f"No hierarchy data for '{class_name}'.", sources

    # ── 14. Change impact analysis ────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def get_change_impact(symbol_name: str) -> tuple[str, list[_S]]:
        """Analyze the blast radius of changing a function, class, or variable.
        Use before modifying a symbol to understand what would break or be affected.
        Returns: impact level (low/medium/high), callers, and usages.
        """
        callers_result = query_service.find_callers(symbol_name, repo_id=repo_id)
        callers = callers_result.get("callers") or []
        impact = "high" if len(callers) > 5 else "medium" if callers else "low"
        lines = [
            f"Symbol: {symbol_name}",
            f"Impact level: {impact.upper()}  ({len(callers)} callers)",
        ]
        sources: list[_S] = []
        if callers:
            lines.append("\nCallers:")
            for c in callers[:10]:
                lines.append(f"  • {c.get('name')}  →  {c.get('path')}")
                if c.get("path"):
                    sources.append(_src(c["path"], c.get("line_number"), c.get("name")))
        return "\n".join(lines), sources

    # ── 15. Cyclomatic complexity ─────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def get_complexity(function_name: str, path: str | None = None) -> tuple[str, list[_S]]:
        """Get the cyclomatic complexity score for a specific function.
        Score guide: 1-5 simple, 6-10 moderate, 11-20 complex, 20+ high risk.
        Args:
            function_name: Exact function name.
            path:          Optional file path to disambiguate if the name is not unique.
        Returns: function name, file, complexity score, and risk level.
        """
        result = query_service.get_cyclomatic_complexity(function_name, path, repo_id=repo_id)
        if not result:
            return f"Function '{function_name}' not found.", []
        score = result.get("cyclomatic_complexity", 1)
        risk = (
            "simple"
            if score <= 5
            else "moderate"
            if score <= 10
            else "complex"
            if score <= 20
            else "high risk"
        )
        content = (
            f"Function: {result.get('name')}\n"
            f"File:     {result.get('path')}:{result.get('line_number')}\n"
            f"Score:    {score}  ({risk})"
        )
        sources = (
            [_src(result["path"], result.get("line_number"), result.get("name"), "function")]
            if result.get("path")
            else []
        )
        return content, sources

    @tool(response_format="content_and_artifact")
    def get_language_stats(language: str | None = None) -> tuple[str, list[_S]]:
        """Get the breakdown of programming languages in the codebase.
        Args:
            language: Optional — filter to a single language (e.g. 'Python').
        Returns: per-language file count, function count, class count.
        """
        result = query_service.get_language_stats(language, repo_id=repo_id)
        if not result:
            return "No language stats available.", []
        rows = result.get("languages", result) if isinstance(result, dict) else result
        lines = [
            (
                f"  {r.get('language', '?'):<15} "
                f"files={r.get('file_count', 0):>4}  "
                f"functions={r.get('function_count', 0):>5}  "
                f"classes={r.get('class_count', 0):>4}"
            )
            for r in (rows if isinstance(rows, list) else [])
        ]
        return "Language breakdown:\n" + "\n".join(lines), []

    # ── 19. Graph / repo stats ────────────────────────────────────────────────

    @tool(response_format="content_and_artifact")
    def get_repo_stats() -> tuple[str, list[_S]]:
        """Get overall statistics for the selected repository (or entire graph if no repo selected).
        Returns: counts of files, functions, classes, variables, imports.
        """
        stats = (
            query_service.get_repository_stats(repo_id)
            if repo_id
            else query_service.get_graph_stats()
        )
        if not stats:
            return "No stats available.", []
        return json.dumps(stats, indent=2), []

    return [
        search_code,
        peek_code,
        semantic_search,
        find_function,
        find_class,
        find_variable,
        find_by_content,
        find_module,
        find_imports,
        find_method_usages,
        find_callers,
    ]
