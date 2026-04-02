from langchain_core.tools import tool

from db.code_serarch_layer import CodeSearchService

_S = dict


def _src(path: str, line: int | None = None, name: str | None = None, typ: str | None = None) -> _S:
    return {"path": path, "line_number": line, "name": name, "type": typ}


def get_tools(query_service: CodeSearchService, repo_id: str | None = None) -> list:
    """Get simplified tool list for code review."""

    @tool(response_format="content_and_artifact")
    def verify_finding(file_path: str, line_number: int, window: int = 15) -> tuple[str, list[_S]]:
        """Read source code around a specific line to verify a potential issue.

        Use this when you spot something suspicious in the diff and need to see
        the actual code to confirm if it's a real bug.

        Args:
            file_path: Path to the file (e.g. 'api/app.py')
            line_number: Line number to inspect (1-indexed)
            window: Lines of context above and below (default 15, max 50)
        Returns: annotated code snippet with line numbers showing the full context.
        """
        result = query_service.peek_file_lines(
            file_path, line_number, min(window, 50), min(window, 50)
        )
        if "error" in result:
            return f"Error: {result['error']}", []
        header = (
            f"File: {result['path']}  "
            f"(anchor: line {result['anchor_line']}, total: {result['total_lines']} lines)"
        )
        sep = "-" * 60
        body = ""
        for entry in result["window"]:
            prefix = "→" if entry["is_anchor"] else " "
            body += (
                f"{prefix} {entry['line_number']:>4} │ {entry['content'].replace(chr(9), '    ')}\n"
            )
        return f"{header}\n{sep}\n{body}", [_src(file_path, line_number, typ="file")]

    @tool(response_format="content_and_artifact")
    def get_function_callers(function_name: str) -> tuple[str, list[_S]]:
        """Find all functions that call a given function.

        Use this to assess the impact of a bug — if many callers depend on
        the function, the bug could have wide blast radius.

        Args:
            function_name: Exact name of the function to find callers for.
        Returns: list of caller functions with their file paths and line numbers.
        """
        result = query_service.find_callers(function_name, repo_id=repo_id)
        if not result:
            return f"No callers found for: '{function_name}'", []
        lines = []
        sources: list[_S] = []
        definitions = result.get("definitions") or []
        if definitions:
            for defn in definitions:
                lines.append(
                    f"Definition: [{defn.get('type')}] {defn.get('name')} "
                    f"→ {defn.get('path')}:{defn.get('line_number')}"
                )
                if defn.get("path"):
                    sources.append(
                        _src(
                            defn["path"],
                            defn.get("line_number"),
                            defn.get("name"),
                            defn.get("type"),
                        )
                    )
        callers = result.get("callers") or []
        if callers:
            lines.append(f"\nCallers ({len(callers)}):")
            for c in callers[:10]:
                lines.append(f"  • {c.get('name')} → {c.get('path')}:{c.get('line_number')}")
                if c.get("path"):
                    sources.append(_src(c["path"], c.get("line_number"), c.get("name")))
        else:
            lines.append("No callers found.")
        return "\n".join(lines), sources

    @tool(response_format="content_and_artifact")
    def get_function_info(function_name: str, file_path: str | None = None) -> tuple[str, list[_S]]:
        """Get the source code and complexity score for a function.

        Use this to understand what a function does and assess its risk level.

        Args:
            function_name: Exact name of the function.
            file_path: Optional file path to disambiguate if the name is not unique.
        Returns: function name, file, line number, source snippet, and complexity score.
        """
        result = query_service.get_cyclomatic_complexity(function_name, file_path, repo_id=repo_id)
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
        source = (result.get("source_code") or result.get("source") or "")[:500]
        content = (
            f"Function: {result.get('name')}\n"
            f"File:     {result.get('path')}:{result.get('line_number')}\n"
            f"Complexity: {score} ({risk})\n"
            f"Source:\n{source}"
        )
        sources = (
            [_src(result["path"], result.get("line_number"), result.get("name"), "function")]
            if result.get("path")
            else []
        )
        return content, sources

    @tool(response_format="content_and_artifact")
    def find_function_in_file(function_name: str, file_path: str) -> tuple[str, list[_S]]:
        """Find a function definition within a specific file.

        Use this when you know both the function name and file and need to
        see its exact location and implementation.

        Args:
            function_name: Name of the function to find.
            file_path: Path to the file to search in.
        Returns: function location, docstring, and source snippet.
        """
        results = query_service.find_by_function_name(
            function_name, fuzzy_search=False, repo_id=repo_id
        )
        if not results:
            return f"Function '{function_name}' not found in graph.", []
        matches = [r for r in results if file_path in (r.get("path") or "")]
        if not matches and results:
            matches = results[:1]
        if not matches:
            return f"Function '{function_name}' not found in {file_path}.", []
        lines = []
        sources: list[_S] = []
        for r in matches:
            lines.append(f"[function] {r.get('name')} → {r.get('path')}:{r.get('line_number')}")
            if r.get("docstring"):
                lines.append(f"  docstring: {r['docstring'][:200]}")
            if r.get("source") or r.get("source_code"):
                src = (r.get("source_code") or r.get("source") or "")[:400]
                lines.append(f"  source:\n    {src}")
            if r.get("path"):
                sources.append(_src(r["path"], r.get("line_number"), r.get("name"), "function"))
        return "\n".join(lines), sources

    return [
        verify_finding,
        get_function_callers,
        get_function_info,
        find_function_in_file,
    ]
