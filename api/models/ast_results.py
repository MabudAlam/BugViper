from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CallSite:
    """A function/method call site within a file."""

    name: str
    full_name: str
    line_number: int
    args: List[str] = field(default_factory=list)
    context: str = ""  # Parent function name
    context_type: str = ""  # "function_definition" | "class_definition" | None
    class_context: str = ""  # Class name if called from a method


@dataclass
class FunctionDef:
    """A function or method definition."""

    name: str
    line_number: int
    end_line: int
    args: List[str] = field(default_factory=list)
    cyclomatic_complexity: int = 1
    context: str = ""  # Parent function name (if nested)
    context_type: str = ""  # "function_definition" | "class_definition" | None
    class_context: str = ""  # Class name if this is a method
    decorators: List[str] = field(default_factory=list)
    docstring: str | None = None
    source: str = ""
    is_method: bool = False


@dataclass
class ClassDef:
    """A class definition."""

    name: str
    line_number: int
    end_line: int
    bases: List[str] = field(default_factory=list)
    context: str = ""  # Parent context (if nested)
    decorators: List[str] = field(default_factory=list)
    docstring: str | None = None
    source: str = ""


@dataclass
class Import:
    """An import statement."""

    name: str
    full_import_name: str
    line_number: int
    alias: str | None = None


@dataclass
class ParsedFile:
    """Result of parsing a single file with full AST extraction."""

    path: str
    language: str
    functions: List[FunctionDef] = field(default_factory=list)
    classes: List[ClassDef] = field(default_factory=list)
    imports: List[Import] = field(default_factory=list)
    call_sites: List[CallSite] = field(default_factory=list)
    error: str | None = None


@dataclass
class ASTSummary:
    """Structured summary for the Explorer agent."""

    functions: List[Dict[str, Any]] = field(default_factory=list)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    imports: List[Dict[str, Any]] = field(default_factory=list)
    internal_call_graph: Dict[str, Dict] = field(default_factory=dict)
    external_calls: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


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
    # Map function names to their locations
    func_locations: Dict[str, List[tuple]] = {}
    for pf in parsed_files:
        for f in pf.functions:
            if f.name not in func_locations:
                func_locations[f.name] = []
            func_locations[f.name].append((pf.path, f.class_context, f.context))

    # Build call graph
    call_graph: Dict[str, Dict] = {}
    for pf in parsed_files:
        for f in pf.functions:
            key = f"{pf.path}:{f.name}"
            if key not in call_graph:
                call_graph[key] = {"calls": [], "file": pf.path, "name": f.name}

    # Track calls
    for pf in parsed_files:
        for call in pf.call_sites:
            # Find the function containing this call
            caller_name = call.context
            caller_class = call.class_context

            # Match caller function
            for f in pf.functions:
                if f.name == caller_name and f.class_context == caller_class:
                    caller_key = f"{pf.path}:{f.name}"
                    callee_name = call.name

                    # Only track internal calls
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
                    "source": f.source,  # Full source, truncation handled in review_service
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
                    "source": c.source,  # Full source, truncation handled in review_service
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

    # Group external calls by name with caller context
    external_calls_summary = []
    for name, calls in external_calls.items():
        caller_contexts = []
        for call in calls[:15]:  # Keep reasonable limit for caller contexts
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

    # Sort by call count (most-called external symbols first)
    external_calls_summary.sort(key=lambda x: x["call_count"], reverse=True)

    return ASTSummary(
        functions=all_functions,
        classes=all_classes,
        imports=all_imports,
        internal_call_graph=call_graph,
        external_calls=external_calls_summary[:100],  # Keep more, filter in review_service
        stats={
            "total_functions": len(all_functions),
            "total_classes": len(all_classes),
            "total_imports": len(all_imports),
            "total_external_calls": len(external_calls),
        },
    )
