from typing import Any, Dict, List

from pydantic import BaseModel, Field


class CallSite(BaseModel):
    """A function/method call site within a file."""

    name: str
    full_name: str
    line_number: int
    args: List[str] = Field(default_factory=list)
    context: str = ""
    context_type: str = ""
    class_context: str = ""

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


class FunctionDef(BaseModel):
    """A function or method definition."""

    name: str
    line_number: int
    end_line: int
    args: List[str] = Field(default_factory=list)
    cyclomatic_complexity: int = 1
    context: str = ""
    context_type: str = ""
    class_context: str = ""
    decorators: List[str] = Field(default_factory=list)
    docstring: str | None = None
    source: str = ""
    is_method: bool = False

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


class ClassDef(BaseModel):
    """A class definition."""

    name: str
    line_number: int
    end_line: int
    bases: List[str] = Field(default_factory=list)
    context: str = ""
    decorators: List[str] = Field(default_factory=list)
    docstring: str | None = None
    source: str = ""

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


class Import(BaseModel):
    """An import statement."""

    name: str
    full_import_name: str
    line_number: int
    alias: str | None = None

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


class ParsedFile(BaseModel):
    """Result of parsing a single file with full AST extraction."""

    path: str
    language: str
    functions: List[FunctionDef] = Field(default_factory=list)
    classes: List[ClassDef] = Field(default_factory=list)
    imports: List[Import] = Field(default_factory=list)
    call_sites: List[CallSite] = Field(default_factory=list)
    error: str | None = None

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


class ASTSummary(BaseModel):
    """Structured summary for the Explorer agent."""

    functions: List[Dict[str, Any]] = Field(default_factory=list)
    classes: List[Dict[str, Any]] = Field(default_factory=list)
    imports: List[Dict[str, Any]] = Field(default_factory=list)
    internal_call_graph: Dict[str, Dict] = Field(default_factory=dict)
    external_calls: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, int] = Field(default_factory=dict)

    def toDict(self) -> Dict[str, Any]:
        return self.model_dump()


def compute_external_calls(parsed_files: List[ParsedFile]) -> Dict[str, List[CallSite]]:
    """Compute external calls: calls to symbols NOT defined in the PR files.

    Returns:
        Dict mapping symbol name -> list of CallSites calling it
    """
    defined_names: set[str] = set()
    for pf in parsed_files:
        defined_names.update(f.name for f in pf.functions)
        defined_names.update(c.name for c in pf.classes)

    external_calls: Dict[str, List[CallSite]] = {}
    for pf in parsed_files:
        for call in pf.call_sites:
            if call.name not in defined_names:
                if call.name not in external_calls:
                    external_calls[call.name] = []
                external_calls[call.name].append(call)

    return external_calls


def build_call_graph(parsed_files: List[ParsedFile]) -> Dict[str, Dict]:
    """Build a call graph from parsed files showing which functions call which.

    Only includes calls within the PR files (internal calls).

    Returns:
        Dict mapping function_key -> {"calls": [...], "file": str, "name": str}
    """
    func_locations: Dict[str, List[tuple]] = {}
    for pf in parsed_files:
        for f in pf.functions:
            if f.name not in func_locations:
                func_locations[f.name] = []
            func_locations[f.name].append((pf.path, f.class_context, f.context))

    call_graph: Dict[str, Dict] = {}
    for pf in parsed_files:
        for f in pf.functions:
            key = f"{pf.path}:{f.name}"
            if key not in call_graph:
                call_graph[key] = {"calls": [], "file": pf.path, "name": f.name}

    for pf in parsed_files:
        for call in pf.call_sites:
            caller_name = call.context
            caller_class = call.class_context

            for f in pf.functions:
                if f.name == caller_name and f.class_context == caller_class:
                    caller_key = f"{pf.path}:{f.name}"
                    callee_name = call.name

                    if callee_name in func_locations:
                        call_graph[caller_key]["calls"].append(
                            {
                                "name": callee_name,
                                "line": call.line_number,
                                "full_name": call.full_name,
                            }
                        )
                    break

    return call_graph


def summarize_for_explorer(parsed_files: List[ParsedFile]) -> ASTSummary:
    """Create a structured summary for the Explorer agent.

    This replaces the previous approach of dumping all external symbols blindly.
    Instead, we provide:
    - Changed symbols with their source/docstring
    - Internal call graph (which PR functions call which PR functions)
    - External calls with context (who's calling what external symbol)

    The Explorer can then intelligently query the Graph DB for callers
    of specific external symbols that are actually relevant.
    """
    all_functions: List[Dict] = []
    all_classes: List[Dict] = []
    all_imports: List[Dict] = []
    external_calls = compute_external_calls(parsed_files)
    call_graph = build_call_graph(parsed_files)

    for pf in parsed_files:
        for f in pf.functions:
            all_functions.append(
                {
                    "name": f.name,
                    "file": pf.path,
                    "line": f.line_number,
                    "end_line": f.end_line,
                    "args": f.args,
                    "complexity": f.cyclomatic_complexity,
                    "class_context": f.class_context,
                    "docstring": f.docstring,
                    "source": f.source,
                    "is_method": f.is_method,
                }
            )

        for c in pf.classes:
            all_classes.append(
                {
                    "name": c.name,
                    "file": pf.path,
                    "line": c.line_number,
                    "end_line": c.end_line,
                    "bases": c.bases,
                    "docstring": c.docstring,
                    "source": c.source,
                }
            )

        for imp in pf.imports:
            all_imports.append(
                {
                    "name": imp.name,
                    "full_name": imp.full_import_name,
                    "file": pf.path,
                    "line": imp.line_number,
                    "alias": imp.alias,
                }
            )

    external_calls_summary = []
    for name, calls in external_calls.items():
        caller_contexts = []
        for call in calls[:15]:
            caller_contexts.append(
                {
                    "caller": call.context,
                    "class": call.class_context,
                    "line": call.line_number,
                }
            )
        external_calls_summary.append(
            {
                "name": name,
                "call_count": len(calls),
                "callers": caller_contexts,
            }
        )

    external_calls_summary.sort(key=lambda x: x["call_count"], reverse=True)

    return ASTSummary(
        functions=all_functions,
        classes=all_classes,
        imports=all_imports,
        internal_call_graph=call_graph,
        external_calls=external_calls_summary[:100],
        stats={
            "total_functions": len(all_functions),
            "total_classes": len(all_classes),
            "total_imports": len(all_imports),
            "total_external_calls": len(external_calls),
        },
    )


class ASTParserResultForEachFile(BaseModel):
    """Structured result of AST parsing for each file, to be stored in DB."""

    class_defs: List[ClassDef] = Field(default_factory=list)
