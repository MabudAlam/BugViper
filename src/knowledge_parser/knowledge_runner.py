#!/usr/bin/env python3
"""
Knowledge parser runner - single entry point for AST generation.

Edit REPO_URL, PR_NUMBER, OUTPUT_DIR at the top, then run:
    python -m knowledge_parser.knowledge_runner
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

REPO_URL = "https://github.com/MabudAlam/bigset/"
PR_NUMBER = 2
OUTPUT_DIR = "./output"

sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_parser.call_graph import (
    analyze_pr_call_graph as _analyze_pr_call_graph,
)
from knowledge_parser.call_graph import render_callgraph_markdown
from knowledge_parser.parser import TreeSitterParser
from knowledge_parser.registry import EXT_TO_LANG

MAX_DIRECT_ENTITIES = 120
MAX_RELATIONSHIP_ENTRIES = 160
MAX_NEIGHBOR_FILES = 80
MAX_UNRESOLVED_CALLS = 80

STDLIB_PREFIXES = frozenset(
    {
        "fmt.",
        "strings.",
        "os.",
        "net.",
        "io.",
        "http.",
        "json.",
        "bytes.",
        "time.",
        "sync.",
        "math.",
        "regexp.",
        "context.",
        "errors.",
        "log.",
        "bufio.",
        "path.",
        "filepath.",
        "url.",
        "ioutil.",
        "unicode.",
        "strconv.",
        "sort.",
        "container/list.",
        "container/ring.",
        "crypto.",
        "hash.",
        "encoding.",
        "reflect.",
        "runtime.",
        "unsafe.",
        "syscall.",
        "errors.New",
        "fmt.Errorf",
        "fmt.Sprintf",
        "fmt.Printf",
        "fmt.Print",
    }
)

GENERIC_BUILTINS = frozenset(
    {
        "make",
        "len",
        "append",
        "delete",
        "copy",
        "close",
        "open",
        "read",
        "write",
        "seek",
        "flush",
    }
)


def _is_noise_call(full_name: str, callee_file: str | None) -> bool:
    if callee_file is not None:
        return False
    if any(full_name.startswith(p) for p in STDLIB_PREFIXES):
        return True
    base_name = full_name.split(".")[-1] if "." in full_name else full_name
    if base_name.lower() in GENERIC_BUILTINS:
        return True
    return False


def changed_files_from_diff(diff_text: str) -> list[str]:
    files = []
    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" b/")
            if len(parts) == 2:
                files.append(parts[1])
    return files


def clone_with_token(token: str, url: str, sha: str, dest: Path) -> None:
    clone_url = f"https://x-access-token:{token}@github.com/{url}"
    proc = subprocess.run(
        ["git", "clone", "--depth", "100", clone_url, str(dest)],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Clone failed: {proc.stderr}")

    checkout = subprocess.run(
        ["git", "-C", str(dest), "checkout", sha],
        capture_output=True,
        text=True,
    )
    if checkout.returncode != 0:
        fetch = subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth", "100", "origin", sha],
            capture_output=True,
            text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(f"Fetch failed: {fetch.stderr}")
        checkout = subprocess.run(
            ["git", "-C", str(dest), "checkout", sha],
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            raise RuntimeError(f"Checkout failed: {checkout.stderr}")


def parse_github_url(url: str) -> tuple[str, str]:
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    return parts[-2], parts[-1]


def parse_project(project_path: str, owner: str, repo_name: str) -> dict:
    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        raise ValueError(f"Project path does not exist: {project_path}")

    repo_identifier = f"{owner}/{repo_name}"
    print(f"Parsing project: {repo_identifier}")

    _lang_instances = {lang: TreeSitterParser(lang) for lang in set(EXT_TO_LANG.values())}
    parsers = {ext: _lang_instances[lang] for ext, lang in EXT_TO_LANG.items()}

    files = []
    for ext in parsers.keys():
        found_files = list(project_path_obj.glob(f"**/*{ext}"))
        files.extend([f for f in found_files if f.is_file()])

    print(f"Found {len(files)} files to parse")

    functions_count = 0
    classes_count = 0
    imports_count = 0
    calls_count = 0

    ast = {
        "repo": repo_identifier,
        "owner": owner,
        "repo_name": repo_name,
        "files": [],
        "statistics": {
            "files_parsed": 0,
            "files_skipped": 0,
            "functions_found": 0,
            "classes_found": 0,
            "imports_found": 0,
            "calls_found": 0,
        },
        "errors": [],
    }

    for file_path in files:
        try:
            ext = file_path.suffix
            if ext not in parsers:
                ast["statistics"]["files_skipped"] += 1
                continue

            parser = parsers[ext]
            relative_path = str(file_path.relative_to(project_path_obj))

            file_data = parser.parse(file_path, is_dependency=False)
            if "error" in file_data:
                ast["errors"].append({"file": relative_path, "error": file_data["error"]})
                continue

            file_info = {
                "path": relative_path,
                "language": file_data.get("lang", "unknown"),
                "functions": file_data.get("functions", []),
                "classes": file_data.get("classes", []),
                "imports": file_data.get("imports", []),
                "function_calls": file_data.get("function_calls", []),
            }
            ast["files"].append(file_info)

            functions_count += len(file_data.get("functions", []))
            classes_count += len(file_data.get("classes", []))
            imports_count += len(file_data.get("imports", []))
            calls_count += len(file_data.get("function_calls", []))
            ast["statistics"]["files_parsed"] += 1

        except Exception as e:
            ast["errors"].append({"file": str(file_path), "error": str(e)})
            ast["statistics"]["files_skipped"] += 1

    ast["statistics"]["functions_found"] = functions_count
    ast["statistics"]["classes_found"] = classes_count
    ast["statistics"]["imports_found"] = imports_count
    ast["statistics"]["calls_found"] = calls_count

    print("Parsing complete!")
    print(f"  Files: {ast['statistics']['files_parsed']}")
    print(f"  Functions: {ast['statistics']['functions_found']}")
    print(f"  Classes: {ast['statistics']['classes_found']}")
    print(f"  Imports: {ast['statistics']['imports_found']}")
    print(f"  Calls: {ast['statistics']['calls_found']}")

    return ast


def build_call_graph(ast_data: dict) -> dict:
    print("\nBuilding call graph...")

    func_by_name_file: dict[str, list] = {}
    class_by_name_file: dict[str, list] = {}
    file_functions: dict[str, set] = {}
    file_classes: dict[str, set] = {}

    for file_info in ast_data["files"]:
        file_path = file_info["path"]
        for func in file_info["functions"]:
            if func["name"] not in func_by_name_file:
                func_by_name_file[func["name"]] = []
            func_by_name_file[func["name"]].append({"file": file_path, **func})

            if file_path not in file_functions:
                file_functions[file_path] = set()
            file_functions[file_path].add(func["name"])

        for cls in file_info["classes"]:
            if cls["name"] not in class_by_name_file:
                class_by_name_file[cls["name"]] = []
            class_by_name_file[cls["name"]].append({"file": file_path, **cls})

            if file_path not in file_classes:
                file_classes[file_path] = set()
            file_classes[file_path].add(cls["name"])

    imports_map: dict[str, list] = {}
    for file_info in ast_data["files"]:
        for imp in file_info["imports"]:
            name = imp.get("name") or imp.get("source", "")
            if name not in imports_map:
                imports_map[name] = []
            imports_map[name].append(file_info["path"])

    resolved_calls = []
    unresolved_calls = 0

    for file_info in ast_data["files"]:
        file_path = file_info["path"]
        for call in file_info["function_calls"]:
            if not call or not call.get("name"):
                continue
            context = call.get("context")
            if isinstance(context, (list, tuple)):
                caller_name = context[0]
            elif isinstance(context, str):
                caller_name = context
            else:
                caller_name = call.get("context_name")
            call_name = call["name"]
            line_number = call.get("line_number", 0)

            resolved = None

            if call_name in file_functions.get(file_path, set()):
                resolved = {
                    "type": "local_function",
                    "from_file": file_path,
                    "from_function": caller_name or "<anonymous>",
                    "from_line": line_number,
                    "to_file": file_path,
                    "to_name": call_name,
                    "to_type": "function",
                }

            elif call_name in file_classes.get(file_path, set()):
                resolved = {
                    "type": "local_class",
                    "from_file": file_path,
                    "from_function": caller_name or "<anonymous>",
                    "from_line": line_number,
                    "to_file": file_path,
                    "to_name": call_name,
                    "to_type": "class",
                }

            elif call_name in func_by_name_file:
                targets = func_by_name_file[call_name]
                if len(targets) == 1:
                    resolved = {
                        "type": "resolved_function",
                        "from_file": file_path,
                        "from_function": caller_name or "<anonymous>",
                        "from_line": line_number,
                        "to_file": targets[0]["file"],
                        "to_name": call_name,
                        "to_type": "function",
                    }
                else:
                    unresolved_calls += 1
                    resolved = {
                        "type": "unresolved",
                        "from_file": file_path,
                        "from_function": caller_name or "<anonymous>",
                        "from_line": line_number,
                        "to_name": call_name,
                        "to_type": "ambiguous",
                    }

            elif call_name in class_by_name_file:
                targets = class_by_name_file[call_name]
                if len(targets) == 1:
                    resolved = {
                        "type": "resolved_class",
                        "from_file": file_path,
                        "from_function": caller_name or "<anonymous>",
                        "from_line": line_number,
                        "to_file": targets[0]["file"],
                        "to_name": call_name,
                        "to_type": "class",
                    }
                else:
                    unresolved_calls += 1
                    resolved = {
                        "type": "unresolved",
                        "from_file": file_path,
                        "from_function": caller_name or "<anonymous>",
                        "from_line": line_number,
                        "to_name": call_name,
                        "to_type": "ambiguous",
                    }

            elif call_name in imports_map:
                resolved = {
                    "type": "external",
                    "from_file": file_path,
                    "from_function": caller_name or "<anonymous>",
                    "from_line": line_number,
                    "to_name": call_name,
                    "to_type": "import",
                    "source_file": imports_map[call_name][0],
                }

            else:
                unresolved_calls += 1
                resolved = {
                    "type": "unresolved",
                    "from_file": file_path,
                    "from_function": caller_name or "<anonymous>",
                    "from_line": line_number,
                    "to_name": call_name,
                    "to_type": "unknown",
                }

            resolved_calls.append(resolved)

    print("Call graph complete!")
    print(f"  Resolved: {len(resolved_calls) - unresolved_calls}")
    print(f"  Unresolved: {unresolved_calls}")

    inheritance_edges = []
    for file_info in ast_data["files"]:
        for cls in file_info["classes"]:
            if cls.get("bases"):
                for base in cls["bases"]:
                    if base != "object":
                        inheritance_edges.append(
                            {
                                "type": "INHERITS",
                                "from_file": file_info["path"],
                                "from_class": cls["name"],
                                "to_name": base,
                            }
                        )

    import_edges = []
    for file_info in ast_data["files"]:
        for imp in file_info["imports"]:
            import_edges.append(
                {
                    "type": "IMPORTS",
                    "from_file": file_info["path"],
                    "import_name": imp.get("name") or imp.get("source", ""),
                }
            )

        graph_functions = []
        for file_info in ast_data["files"]:
            for f in file_info["functions"]:
                f_copy = f.copy()
                f_copy["file"] = file_info["path"]
                graph_functions.append(f_copy)

        graph_classes = []
        for file_info in ast_data["files"]:
            for c in file_info["classes"]:
                c_copy = c.copy()
                c_copy["file"] = file_info["path"]
                graph_classes.append(c_copy)

        graph = {
            "repo": ast_data["repo"],
            "owner": ast_data["owner"],
            "repo_name": ast_data["repo_name"],
            "nodes": {
                "functions": graph_functions,
                "classes": graph_classes,
            },
            "edges": {
                "calls": resolved_calls,
                "inheritance": inheritance_edges,
                "imports": import_edges,
            },
            "statistics": {
                "files": ast_data["statistics"]["files_parsed"],
                "functions": ast_data["statistics"]["functions_found"],
                "classes": ast_data["statistics"]["classes_found"],
                "imports": ast_data["statistics"]["imports_found"],
                "calls": len(resolved_calls),
                "resolved_calls": len(resolved_calls) - unresolved_calls,
                "unresolved_calls": unresolved_calls,
            },
        }

    return graph


def build_functions_detail(ast_data: dict, graph_data: dict) -> dict:
    print("\nBuilding functions detail...")

    func_by_key: dict[str, dict] = {}
    for file_info in ast_data["files"]:
        for func in file_info["functions"]:
            key = f"{file_info['path']}:{func['name']}"
            func_by_key[key] = {
                "name": func["name"],
                "file": file_info["path"],
                "line": func.get("line_number", 0),
                "calls": [],
                "uses": [],
                "creates": [],
            }

    class_by_key: dict[str, dict] = {}
    for file_info in ast_data["files"]:
        for cls in file_info["classes"]:
            key = f"{file_info['path']}:{cls['name']}"
            class_by_key[key] = {
                "name": cls["name"],
                "file": file_info["path"],
                "line": cls.get("line_number", 0),
                "used_in": [],
            }

    for call in graph_data["edges"]["calls"]:
        if not call:
            continue
        caller_file = call.get("from_file")
        caller_name = call.get("from_function")
        call_name = call.get("to_name")
        to_file = call.get("to_file")
        from_line = call.get("from_line", 0)

        if not caller_file or not call_name:
            continue

        if caller_name and caller_name != "<anonymous>":
            caller_key = f"{caller_file}:{caller_name}"
            if caller_key in func_by_key:
                resolved_call = {"to": call_name, "line": from_line}
                if to_file:
                    resolved_call["file"] = to_file
                if resolved_call not in func_by_key[caller_key]["calls"]:
                    func_by_key[caller_key]["calls"].append(resolved_call)

    for file_info in ast_data["files"]:
        for func in file_info["functions"]:
            key = f"{file_info['path']}:{func['name']}"
            variables = func.get("variables", [])
            for var_info in variables:
                name = var_info.get("name")
                vtype = var_info.get("type")
                vfrom = var_info.get("from", "unknown")
                vline = var_info.get("line", func.get("line_number", 0))
                if not name:
                    continue
                entry = {"var": name, "line": vline}
                if vtype:
                    entry["type"] = vtype
                if vfrom and vfrom != "unknown":
                    entry["from"] = vfrom
                func_by_key[key]["uses"].append(entry)

            for var_info in func.get("returns", []):
                name = var_info.get("name") or var_info.get("type", "unknown")
                vtype = var_info.get("type", "unknown")
                vline = var_info.get("line", func.get("line_number", 0))
                func_by_key[key]["creates"].append(
                    {
                        "var": name,
                        "type": vtype if vtype != "unknown" else "unknown",
                        "line": vline,
                    }
                )

    for file_info in ast_data["files"]:
        for cls in file_info["classes"]:
            key = f"{file_info['path']}:{cls['name']}"
            for call in file_info["function_calls"]:
                if not call:
                    continue
                if call.get("type") == "local_class" and call.get("name") == cls["name"]:
                    caller_name = call.get("context_name")
                    if caller_name and caller_name != "<anonymous>":
                        class_by_key[key]["used_in"].append(
                            {
                                "file": file_info["path"],
                                "function": caller_name,
                                "line": call.get("line_number", 0),
                            }
                        )

    return {
        "repo": ast_data["repo"],
        "functions": list(func_by_key.values()),
        "data_models": list(class_by_key.values()),
    }


def _normalize_path(p: str) -> str:
    p = p.lstrip("./")
    while p.startswith("/"):
        p = p[1:]
    return p


def slice_for_files(
    graph_data: dict,
    functions_data: dict,
    target_files: list[str],
) -> dict:
    print("\nSlicing for PR...")
    targets = {_normalize_path(f) for f in target_files}

    functions = functions_data.get("functions", [])
    classes = functions_data.get("data_models", [])
    graph_functions = graph_data.get("nodes", {}).get("functions", [])
    graph_classes = graph_data.get("nodes", {}).get("classes", [])

    function_details = {
        (_normalize_path(fn.get("file", "")), fn.get("name", "")): fn for fn in functions
    }
    class_details = {
        (_normalize_path(cls.get("file", "")), cls.get("name", "")): cls for cls in classes
    }
    function_defs = {
        (_normalize_path(fn.get("file", "")), fn.get("name", "")): fn for fn in graph_functions
    }
    class_defs = {
        (_normalize_path(cls.get("file", "")), cls.get("name", "")): cls for cls in graph_classes
    }

    directly_affected_funcs = [
        _compact_function(fn, function_defs.get((_normalize_path(fn["file"]), fn["name"])))
        for fn in functions
        if _normalize_path(fn["file"]) in targets
    ]
    directly_affected_classes = [
        _compact_class(cls, class_defs.get((_normalize_path(cls["file"]), cls["name"])))
        for cls in classes
        if _normalize_path(cls["file"]) in targets
    ]

    incoming: dict[tuple[str, str, str, str], dict] = {}
    outgoing: dict[tuple[str, str, str, str], dict] = {}
    internal: dict[tuple[str, str, str, str], dict] = {}
    unresolved: list[dict] = []
    external: list[dict] = []
    neighbor_counts: dict[str, dict] = {}

    for call in graph_data.get("edges", {}).get("calls", []):
        if not call:
            continue
        from_file = _normalize_path(call.get("from_file", ""))
        to_file = _normalize_path(call.get("to_file", "")) if call.get("to_file") else None
        to_name = call.get("to_name")
        from_function = call.get("from_function")
        from_line = call.get("from_line", 0)

        if not from_file or not to_name or not from_function:
            continue
        if from_function == "<anonymous>" or not from_function:
            continue

        from_changed = from_file in targets
        to_changed = to_file in targets if to_file else False

        if from_changed and to_changed:
            key = (from_file, from_function, to_file or "", to_name)
            internal[key] = _compact_call_edge(call, include_callee=True)
        elif from_changed and to_file:
            key = (from_file, from_function, to_file, to_name)
            outgoing[key] = _compact_call_edge(call, include_callee=True)
            _bump_neighbor(neighbor_counts, to_file, "outgoing")
        elif to_changed:
            key = (from_file, from_function, to_file or "", to_name)
            incoming[key] = {
                **_compact_call_edge(call, include_callee=True),
                "caller": _lookup_function_summary(
                    function_details, function_defs, from_file, from_function
                ),
            }
            _bump_neighbor(neighbor_counts, from_file, "incoming")

        if from_changed and call.get("type") == "unresolved":
            unresolved.append(
                {
                    "from_file": from_file,
                    "from_function": from_function,
                    "line": from_line,
                    "to_name": to_name,
                    "to_type": call.get("to_type", "unknown"),
                }
            )
        elif from_changed and call.get("type") == "external":
            external.append(
                {
                    "from_file": from_file,
                    "from_function": from_function,
                    "line": from_line,
                    "to_name": to_name,
                    "source_file": call.get("source_file"),
                }
            )

    import_edges = []
    for imp in graph_data.get("edges", {}).get("imports", []):
        from_file = _normalize_path(imp.get("from_file", ""))
        if from_file in targets:
            import_edges.append(
                {
                    "from_file": from_file,
                    "import_name": imp.get("import_name", ""),
                }
            )

    inheritance_edges = []
    for edge in graph_data.get("edges", {}).get("inheritance", []):
        from_file = _normalize_path(edge.get("from_file", ""))
        if from_file in targets:
            inheritance_edges.append(
                {
                    "from_file": from_file,
                    "from_class": edge.get("from_class", ""),
                    "to_name": edge.get("to_name", ""),
                }
            )

    outgoing_files = {edge["to_file"] for edge in outgoing.values() if edge.get("to_file")}
    used_data_models = _find_used_data_models(outgoing.values(), class_details)
    review_hints = _build_review_hints(
        directly_affected_funcs,
        incoming.values(),
        outgoing.values(),
        unresolved,
        external,
    )

    return {
        "repo": functions_data.get("repo", ""),
        "schema_version": 2,
        "target_files": sorted(targets),
        "summary": {
            "directly_affected_functions": len(directly_affected_funcs),
            "directly_affected_classes": len(directly_affected_classes),
            "incoming_callers": len(incoming),
            "outgoing_callees": len(outgoing),
            "internal_changed_file_calls": len(internal),
            "outgoing_files": len(outgoing_files),
            "used_data_models": len(used_data_models),
            "unresolved_calls_from_changed": len(unresolved),
            "external_calls_from_changed": len(external),
            "neighbor_files": len(neighbor_counts),
        },
        "directly_affected": {
            "functions": directly_affected_funcs[:MAX_DIRECT_ENTITIES],
            "classes": directly_affected_classes[:MAX_DIRECT_ENTITIES],
        },
        "relationships": {
            "internal_changed_file_calls": _sorted_edges(internal.values())[
                :MAX_RELATIONSHIP_ENTRIES
            ],
            "incoming_callers": _sorted_edges(incoming.values())[:MAX_RELATIONSHIP_ENTRIES],
            "outgoing_callees": _sorted_edges(outgoing.values())[:MAX_RELATIONSHIP_ENTRIES],
            "external_calls_from_changed": _sort_call_refs(external)[:MAX_UNRESOLVED_CALLS],
            "unresolved_calls_from_changed": _sort_call_refs(unresolved)[:MAX_UNRESOLVED_CALLS],
            "imports_from_changed": sorted(
                import_edges, key=lambda x: (x["from_file"], x["import_name"])
            )[:MAX_RELATIONSHIP_ENTRIES],
            "inheritance_from_changed": sorted(
                inheritance_edges, key=lambda x: (x["from_file"], x["from_class"], x["to_name"])
            )[:MAX_RELATIONSHIP_ENTRIES],
        },
        "blast_radius": {
            "incoming_callers": _sorted_edges(incoming.values())[:MAX_RELATIONSHIP_ENTRIES],
            "outgoing_files": sorted(outgoing_files),
            "neighbor_files": _rank_neighbors(neighbor_counts)[:MAX_NEIGHBOR_FILES],
        },
        "used_data_models": used_data_models[:MAX_RELATIONSHIP_ENTRIES],
        "review_hints": review_hints,
        "limits": {
            "direct_entities": MAX_DIRECT_ENTITIES,
            "relationship_entries": MAX_RELATIONSHIP_ENTRIES,
            "neighbor_files": MAX_NEIGHBOR_FILES,
            "unresolved_calls": MAX_UNRESOLVED_CALLS,
        },
    }


def analyze_pr_call_graph(ast_data: dict, pr_files: list[str]) -> dict:
    """
    Analyze call relationships for PR files.

    Re-export from knowledge_parser.call_graph for backward compatibility.
    See call_graph.py for the full algorithm.
    """
    return _analyze_pr_call_graph(ast_data, pr_files)


def _compact_function(fn: dict, definition: dict | None = None) -> dict:
    out = {
        "name": fn.get("name", ""),
        "file": _normalize_path(fn.get("file", "")),
        "line": fn.get("line", fn.get("line_number", 0)),
        "calls": _sort_call_refs(fn.get("calls", [])),
        "uses": fn.get("uses", [])[:10],
        "creates": fn.get("creates", [])[:10],
    }
    if definition:
        if definition.get("end_line_number"):
            out["end_line"] = definition.get("end_line_number")
        elif definition.get("end_line"):
            out["end_line"] = definition.get("end_line")
        if definition.get("signature"):
            out["signature"] = definition.get("signature")
        if definition.get("docstring"):
            out["docstring"] = str(definition.get("docstring", ""))[:200]
    return out


def _compact_class(cls: dict, definition: dict | None = None) -> dict:
    out = {
        "name": cls.get("name", ""),
        "file": _normalize_path(cls.get("file", "")),
        "line": cls.get("line", cls.get("line_number", 0)),
        "used_in": cls.get("used_in", [])[:10],
    }
    if definition:
        if definition.get("end_line_number"):
            out["end_line"] = definition.get("end_line_number")
        elif definition.get("end_line"):
            out["end_line"] = definition.get("end_line")
        if definition.get("bases"):
            out["bases"] = definition.get("bases", [])
        if definition.get("methods"):
            out["methods"] = definition.get("methods", [])[:10]
    return out


def _compact_call_edge(call: dict, include_callee: bool = False) -> dict:
    edge = {
        "type": call.get("type", ""),
        "from_file": _normalize_path(call.get("from_file", "")),
        "from_function": call.get("from_function", ""),
        "from_line": call.get("from_line", 0),
        "to_file": _normalize_path(call.get("to_file", "")) if call.get("to_file") else None,
        "to_name": call.get("to_name", ""),
        "to_type": call.get("to_type", ""),
    }
    if not include_callee:
        return edge
    if call.get("to_file"):
        edge["callee"] = {
            "file": _normalize_path(call["to_file"]),
            "name": call.get("to_name", ""),
            "line": call.get("to_line", 0),
        }
    return edge


def _lookup_function_summary(
    function_details: dict[tuple[str, str], dict],
    function_defs: dict[tuple[str, str], dict],
    file_path: str,
    function_name: str,
) -> dict | None:
    detail = function_details.get((file_path, function_name))
    definition = function_defs.get((file_path, function_name))
    if not detail and not definition:
        return None
    summary: dict = {
        "name": function_name,
        "file": file_path,
        "line": (detail or {}).get("line", 0) or (definition or {}).get("line_number", 0),
    }
    if definition:
        if definition.get("signature"):
            summary["signature"] = definition["signature"]
        if definition.get("docstring"):
            summary["docstring"] = str(definition.get("docstring", ""))[:200]
    return summary


def _find_used_data_models(edges, class_details: dict[tuple[str, str], dict]) -> list[dict]:
    used: dict[tuple[str, str], dict] = {}
    for edge in edges:
        target_file = edge.get("to_file")
        target_name = edge.get("to_name")
        if not target_file or not target_name:
            continue
        cls = class_details.get((_normalize_path(target_file), target_name))
        if not cls:
            continue
        key = (_normalize_path(target_file), target_name)
        used[key] = {
            "name": cls.get("name", ""),
            "file": _normalize_path(cls.get("file", "")),
            "line": cls.get("line", 0),
            "used_by": edge.get("from_function", ""),
            "used_from_file": edge.get("from_file", ""),
        }
    return sorted(used.values(), key=lambda x: (x["file"], x["name"]))


def _bump_neighbor(neighbor_counts: dict[str, dict], file_path: str, direction: str) -> None:
    if not file_path:
        return
    entry = neighbor_counts.setdefault(
        file_path,
        {"file": file_path, "incoming": 0, "outgoing": 0, "total": 0},
    )
    entry[direction] += 1
    entry["total"] += 1


def _rank_neighbors(neighbor_counts: dict[str, dict]) -> list[dict]:
    return sorted(
        neighbor_counts.values(),
        key=lambda x: (-x["total"], x["file"]),
    )


def _sorted_edges(edges) -> list[dict]:
    return sorted(
        edges,
        key=lambda x: (
            x.get("from_file", ""),
            x.get("from_function", ""),
            x.get("from_line", 0),
            x.get("to_file") or "",
            x.get("to_name", ""),
        ),
    )


def _sort_call_refs(calls: list[dict]) -> list[dict]:
    return sorted(
        calls,
        key=lambda x: (
            x.get("file") or x.get("from_file", ""),
            x.get("line") or x.get("from_line", 0),
            x.get("to") or x.get("to_name", ""),
        ),
    )


def _build_review_hints(
    directly_affected_funcs: list[dict],
    incoming_edges,
    outgoing_edges,
    unresolved_calls: list[dict],
    external_calls: list[dict],
) -> list[str]:
    hints = []
    if list(incoming_edges):
        hints.append(
            "Trace incoming_callers before reporting regressions; "
            "these are blast-radius entry points."
        )
    if list(outgoing_edges):
        hints.append(
            "Check outgoing_callees for contract changes, error handling, and changed assumptions."
        )
    if unresolved_calls:
        hints.append(
            "Inspect unresolved_calls_from_changed manually; "
            "unresolved targets can hide dynamic dispatch or parser misses."
        )
    if external_calls:
        hints.append(
            "Review external_calls_from_changed for network, filesystem, "
            "database, auth, and security-sensitive sinks."
        )
    hot = [fn for fn in directly_affected_funcs if len(fn.get("calls", [])) >= 8]
    if hot:
        hot_spots = ", ".join(f"{fn['file']}:{fn['name']}" for fn in hot[:10])
        hints.append(
            "Functions with many outgoing calls may be orchestration hot spots: " + hot_spots
        )
    return hints


async def main():
    owner, repo = parse_github_url(REPO_URL)
    print(f"Fetching PR #{PR_NUMBER} for {owner}/{repo}...")

    from common.github_client import get_github_client

    gh = get_github_client()

    token = await gh._get_installation_token(owner, repo)

    diff_text, pr_info, head_sha, base_sha = await asyncio.gather(
        gh.get_pr_diff(owner, repo, PR_NUMBER),
        gh.get_pr_info(owner, repo, PR_NUMBER),
        gh.get_pr_head_ref(owner, repo, PR_NUMBER),
        gh.get_pr_base_sha(owner, repo, PR_NUMBER),
    )
    changed_files = changed_files_from_diff(diff_text)
    print(f"PR head SHA: {head_sha[:7]}, changed files: {len(changed_files)}")

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "diff.patch").write_text(diff_text)
    print("Wrote diff.patch")

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        print(f"Cloning {owner}/{repo}...")
        clone_with_token(token, f"{owner}/{repo}", head_sha, clone_path)

        print("Parsing project...")
        ast_data = parse_project(str(clone_path), owner, repo)

    (output_dir / "ast.json").write_text(json.dumps(ast_data, indent=2))
    print("Wrote ast.json")

    print("Analyzing PR call graph...")
    call_graph = analyze_pr_call_graph(ast_data, changed_files)
    (output_dir / "call_graph.json").write_text(json.dumps(call_graph, indent=2))
    print("Wrote call_graph.json")

    callgraph_md = render_callgraph_markdown(call_graph)
    (output_dir / "callgraph.md").write_text(callgraph_md)
    print("Wrote callgraph.md")

    print(f"\nOutputs written to {output_dir}:")
    print("  diff.patch - PR diff")
    print("  ast.json - Raw AST")
    print("  call_graph.json - PR call relationships")
    print("  callgraph.md - compact call graph for the review agent")

    print("\nAST Summary:")
    print(f"  Files parsed: {ast_data['statistics']['files_parsed']}")
    print(f"  Functions: {ast_data['statistics']['functions_found']}")
    print(f"  Classes: {ast_data['statistics']['classes_found']}")
    print(f"  Imports: {ast_data['statistics']['imports_found']}")
    print(f"  Calls: {ast_data['statistics']['calls_found']}")


if __name__ == "__main__":
    asyncio.run(main())
