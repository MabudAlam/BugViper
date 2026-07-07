"""
Robust call graph builder from AST.

Reads ast.json (produced by the language parsers) and emits a per-PR-file
call graph with receiver-aware resolution, proper scope chains, and
frequency tracking.

The algorithm operates in five passes:

  1. Index every file's definitions (functions, classes, methods) keyed
     by fully-qualified name and by short name (for fallback).
  2. Resolve imports to target file paths via path matching and aliasing.
  3. Extract each call site and resolve the callee:
       - For bare names, look up the local file first, then imports.
       - For receiver.method(...) calls, resolve the receiver to its
         defining file (via imports / module map) and find the method.
       - For chained calls (a.b().c()), follow the chain one step at a
         time using the receiver type of each step.
  4. Classify each edge relative to the PR file set:
       - internal  : PR -> PR (and same-file)
       - incoming  : external -> PR
  5. Aggregate duplicates by (caller, callee_qualified) and emit JSON.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _strip_quotes(s: str) -> str:
    return s.strip("'\"").strip()


def _normalize_rel(path: str) -> str:
    return path.lstrip("./")


def _normalize_path_segments(path: str) -> str:
    """Resolve `.` and `..` segments in a posix path."""
    parts = path.split("/")
    stack: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if stack and stack[-1] != "..":
                stack.pop()
            else:
                stack.append(p)
        else:
            stack.append(p)
    return "/".join(stack)


def _candidate_module_paths(source: str, current_file: str) -> list[str]:
    """
    Generate candidate file paths for a module import source.

    Handles relative imports, alias-prefixed imports (@/...), and bare
    module specifiers. Returns an ordered list of likely file paths.
    """
    if not source:
        return []

    candidates: list[str] = []

    # @ alias (Next.js / Vite convention) - map to repo root
    if source.startswith("@/"):
        candidates.append(source[2:])
    elif source.startswith("@"):
        # namespaced alias like @scope/pkg - treat as external for now
        return []

    # Relative path
    if source.startswith("./") or source.startswith("../"):
        base = Path(current_file).parent.as_posix()
        resolved_raw = (Path(base) / source).as_posix()
        resolved = _normalize_path_segments(resolved_raw)
        candidates.append(resolved)
    elif not source.startswith("@"):
        candidates.append(_normalize_rel(source))

    # Build expanded list with extension variants
    expanded: list[str] = []
    for c in candidates:
        expanded.append(c)
        # If already has an extension, also try swapping it
        known_exts = (".ts", ".tsx", ".js", ".jsx", ".py")
        has_ext = any(c.endswith(e) for e in known_exts)
        if has_ext:
            base_no_ext = c.rsplit(".", 1)[0]
            for ext in known_exts:
                if not c.endswith(ext):
                    expanded.append(base_no_ext + ext)
        else:
            # No extension - try all known extensions
            for ext in known_exts:
                expanded.append(c + ext)
        # Add /index variants
        for ext in ("/index.ts", "/index.tsx", "/index.js", "/index.jsx", "/__init__.py"):
            expanded.append(c + ext)

    return expanded


def _module_to_file(source: str, current_file: str, all_files: set[str]) -> str | None:
    for cand in _candidate_module_paths(source, current_file):
        if cand in all_files:
            return cand
    return None


def _index_functions(file_info: dict) -> dict[str, list[dict]]:
    """
    Index a single file's function definitions.

    Returns a dict keyed by short name -> list of function info dicts.
    Each entry includes fully-qualified name and class context.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    file_path = file_info["path"]
    for fn in file_info.get("functions", []):
        short = fn.get("name")
        if not short:
            continue
        class_ctx = fn.get("class_context")
        if isinstance(class_ctx, str) and class_ctx:
            qualified = f"{class_ctx}.{short}"
        else:
            qualified = short
        out[short].append(
            {
                "name": short,
                "qualified": qualified,
                "file": file_path,
                "line": fn.get("line_number", 0),
                "end_line": fn.get("end_line"),
                "class_context": class_ctx,
            }
        )
    return out


def _index_classes(file_info: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cls in file_info.get("classes", []):
        name = cls.get("name")
        if not name:
            continue
        out[name] = {
            "name": name,
            "file": file_info["path"],
            "line": cls.get("line_number", 0),
        }
    return out


def _method_index(file_info: dict) -> dict[tuple[str, str], list[dict]]:
    """
    Build (class_name, method_name) -> list of method info.
    """
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for fn in file_info.get("functions", []):
        cls = fn.get("class_context")
        if isinstance(cls, str) and cls:
            out[(cls, fn["name"])].append(
                {
                    "name": fn["name"],
                    "qualified": f"{cls}.{fn['name']}",
                    "file": file_info["path"],
                    "line": fn.get("line_number", 0),
                }
            )
    return out


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

_RECEIVER_METHOD_RE = re.compile(r"^([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)$")
_CHAINED_METHOD_RE = re.compile(r"^([\w$.]+)\.([A-Za-z_$][\w$]*)$")

# Resolutions that prove the callee points at a real top-level definition
# in the repo's own source files. Anything outside this set (library
# calls, runtime builtins, name-collision guesses) is dropped.
_DEFINITION_RESOLUTIONS = frozenset(
    {
        "local",
        "imported",
        "imported_method",
        "this_method",
        "local_method",
        "local_constructor",
    }
)

# Common JS/TS built-in function names we don't want to resolve
_BUILTIN_NAMES = frozenset(
    {
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "requestAnimationFrame",
        "cancelAnimationFrame",
        "fetch",
        "alert",
        "confirm",
        "prompt",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURIComponent",
        "decodeURIComponent",
        "Array",
        "Object",
        "String",
        "Number",
        "Boolean",
        "Symbol",
        "Promise",
        "Date",
        "Math",
        "JSON",
        "RegExp",
        "Error",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "require",
        "define",
        "console",  # when called as bare function (rare)
    }
)

# Built-in module prefixes whose methods should be marked external
_BUILTIN_PREFIXES = (
    "window.",
    "document.",
    "console.",
    "Math.",
    "Object.",
    "Array.",
    "JSON.",
    "Date.",
    "Promise.",
    "Map.",
    "Set.",
    "WeakMap.",
    "WeakSet.",
    "Symbol.",
    "Buffer.",
    "process.",
    "crypto.",
    "fs.",
    "path.",
    "google.",
    "SpreadsheetApp.",
    "localStorage.",
    "sessionStorage.",
    "URL.",
    "URLSearchParams.",
    "createHash(",
    "createReadStream(",
    "createWriteStream(",
    "http.",
    "https.",
    "os.",
)


def _resolve_receiver(
    receiver_name: str,
    caller_file: str,
    imports_by_local: dict[str, tuple[str, str]],
    module_to_file: dict[str, str],
    all_files: set[str] | None = None,
) -> str | None:
    """
    Resolve a receiver name (e.g. `wizard`) to the file that defines it.

    Returns file path or None if unresolved.
    """
    # 1. Local import binding
    if receiver_name in imports_by_local:
        _, source = imports_by_local[receiver_name]
        target = module_to_file.get(source)
        if target:
            return target
        # Source may be a relative path that didn't normalize into the map
        if all_files is not None:
            return _module_to_file(source, caller_file, all_files)
        return None

    # 2. Try a same-module export (e.g. the file declares and uses the var)
    #    Caller-side fallback handled below.
    return None


def _is_simple_identifier(s: str) -> bool:
    """True if `s` is a single identifier with no call/argument syntax."""
    return bool(s) and re.match(r"^[A-Za-z_$][\w$]*$", s) is not None


def _resolve_call(
    call: dict,
    caller_file: str,
    caller_func: str | None,
    file_funcs: dict[str, list[dict]],
    file_methods: dict[tuple[str, str], list[dict]],
    file_classes: dict[str, dict],
    imports_by_local: dict[str, tuple[str, str]],
    module_to_file: dict[str, str],
    global_funcs: dict[str, list[dict]],
    global_methods: dict[tuple[str, str], list[dict]],
    pr_files_set: set[str] | None = None,
    all_files: set[str] | None = None,
) -> dict[str, Any]:
    """
    Resolve a single call site to its target file, qualified name, and line.

    Returns a dict with:
        callee, callee_qualified, callee_file, callee_line,
        resolution, receiver, is_method
    """
    name = call.get("name") or ""
    full = call.get("full_name") or name
    call_line = call.get("line_number", 0)

    # Extract receiver (for method calls).
    # Only treat as a method call if the receiver is a simple identifier -
    # otherwise it's a chained call whose first link we can't resolve.
    receiver: str | None = None
    head = full.split("(")[0]
    m = _CHAINED_METHOD_RE.match(head)
    if m and m.group(1) != name and _is_simple_identifier(m.group(1)):
        receiver = m.group(1)

    resolution = "unknown"
    callee_file: str | None = None
    callee_line = 0
    callee_qualified = name
    is_method = False

    # Case A: method call (obj.method)
    if receiver:
        is_method = True
        # Try to resolve receiver to a defining file
        target_file = _resolve_receiver(
            receiver,
            caller_file,
            imports_by_local,
            module_to_file,
            all_files=all_files,
        )

        # Case A1: receiver is `this` -> look inside current class
        if receiver == "this" and caller_func:
            # caller_func qualified gives us the class.method
            cls_name = caller_func.rsplit(".", 1)[0] if "." in caller_func else None
            if cls_name:
                candidates = file_methods.get((cls_name, name), [])
                if candidates:
                    callee_file = candidates[0]["file"]
                    callee_line = candidates[0]["line"]
                    callee_qualified = candidates[0]["qualified"]
                    resolution = "this_method"

        # Case A2: receiver resolved to a file via imports
        if not callee_file and target_file:
            # Look up the method/name in target file. Could be a class method
            # OR a top-level function (e.g. exported from a module).
            candidates = []
            for (cls, m_name), defs in global_methods.items():
                for d in defs:
                    if d["file"] == target_file and m_name == name:
                        candidates.append(d)
            # Also check top-level functions in that file
            if not candidates:
                for d in global_funcs.get(name, []):
                    if d["file"] == target_file:
                        candidates.append(d)
            if candidates:
                callee_file = candidates[0]["file"]
                callee_line = candidates[0]["line"]
                callee_qualified = candidates[0]["qualified"]
                # If qualified name has a class prefix, it's a method
                cross_file = target_file != caller_file
                if "." in candidates[0]["qualified"]:
                    resolution = "imported_method" if cross_file else "local_method"
                else:
                    resolution = "imported" if cross_file else "local"

        # Case A3: receiver is a local in same file (heuristic - look for class name match)
        if not callee_file:
            # If receiver matches a class name declared in caller file
            if receiver in file_classes:
                candidates = file_methods.get((receiver, name), [])
                if candidates:
                    callee_file = candidates[0]["file"]
                    callee_line = candidates[0]["line"]
                    callee_qualified = candidates[0]["qualified"]
                    resolution = "local_constructor"

        # Case A4: bare - same file lookup
        if not callee_file:
            # Check same-file definitions
            for fn in file_funcs.get(name, []):
                if fn["file"] == caller_file:
                    callee_file = fn["file"]
                    callee_line = fn["line"]
                    callee_qualified = fn["qualified"]
                    resolution = "local"
                    break

    # Case B: bare call (function call, no receiver)
    else:
        # B1: local function in same file
        local = [fn for fn in file_funcs.get(name, []) if fn["file"] == caller_file]
        if local:
            callee_file = local[0]["file"]
            callee_line = local[0]["line"]
            callee_qualified = local[0]["qualified"]
            resolution = "local"
        # B2: imported
        elif name in imports_by_local:
            _, source = imports_by_local[name]
            target = module_to_file.get(source) or _module_to_file(
                source, caller_file, set(module_to_file.values())
            )
            if target:
                callee_file = target
                # Find that file's definition
                defs = [fn for fn in global_funcs.get(name, []) if fn["file"] == target]
                if defs:
                    callee_line = defs[0]["line"]
                    callee_qualified = defs[0]["qualified"]
                resolution = "imported"
        # B3: same-file lookup only. Cross-file bare calls without an
        # import binding can't be linked reliably - we drop them.
        elif name in global_funcs:
            same = [d for d in global_funcs[name] if d["file"] == caller_file]
            if same:
                callee_file = same[0]["file"]
                callee_line = same[0]["line"]
                callee_qualified = same[0]["qualified"]
                resolution = "local"

    # Case C: unresolved -> mark as external (e.g. built-ins like window, document)
    if not callee_file:
        resolution = "external_or_builtin"
        for prefix in _BUILTIN_PREFIXES:
            if full.startswith(prefix):
                resolution = "builtin"
                break

    # Final guard: if the full expression's receiver chain starts with a
    # known builtin prefix, override resolution to builtin even if we
    # made a heuristic match.
    if callee_file is not None and not is_method:
        for prefix in _BUILTIN_PREFIXES:
            if full.startswith(prefix):
                callee_file = None
                callee_line = 0
                callee_qualified = name
                resolution = "builtin"
                break

    return {
        "callee": name,
        "callee_qualified": callee_qualified,
        "callee_file": callee_file,
        "callee_line": callee_line,
        "resolution": resolution,
        "receiver": receiver,
        "is_method": is_method,
        "call_line": call_line,
        "call_full": full,
    }


def _class_for_file(file_path: str, global_classes: dict | None = None) -> str | None:
    return None  # placeholder - we use the method_index directly


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def analyze_pr_call_graph(ast_data: dict, pr_files: list[str]) -> dict:
    pr_files_set = {f for f in pr_files if f}
    all_files = {f["path"] for f in ast_data["files"]}

    # Pass 1: build indexes
    file_funcs: dict[str, dict[str, list[dict]]] = {}
    file_classes: dict[str, dict[str, dict]] = {}
    file_methods: dict[str, dict[tuple[str, str], list[dict]]] = {}

    global_funcs: dict[str, list[dict]] = defaultdict(list)
    global_methods: dict[tuple[str, str], list[dict]] = defaultdict(list)
    global_classes: dict[str, dict] = {}

    for f in ast_data["files"]:
        path = f["path"]
        funcs = _index_functions(f)
        classes = _index_classes(f)
        methods = _method_index(f)

        file_funcs[path] = funcs
        file_classes[path] = classes
        file_methods[path] = methods

        for short, defs in funcs.items():
            for d in defs:
                global_funcs[short].append(d)
        for (cls, m), defs in methods.items():
            for d in defs:
                global_methods[(cls, m)].append(d)
        for name, info in classes.items():
            global_classes[name] = info

    # Build module -> file map. Each file's path-without-extension is the
    # canonical module key; we also register cross-extension aliases so
    # `import "../api/client.js"` resolves to `.../client.ts`.
    module_to_file: dict[str, str] = {}
    KNOWN_EXTS = (".ts", ".tsx", ".js", ".jsx", ".py")
    for f in ast_data["files"]:
        path = f["path"]
        for ext in KNOWN_EXTS:
            if path.endswith(ext):
                stem = path[: -len(ext)]
                module_to_file[stem] = path
                # Cross-extension: e.g. client.ts should also be reachable as
                # client.js (TS/JS interop) and client.tsx etc.
                for alt in KNOWN_EXTS:
                    if alt != ext:
                        module_to_file[stem + alt] = path
                break
    # Add /index variants
    for f in ast_data["files"]:
        path = f["path"]
        if "/index." in path:
            base = path.split("/index.")[0]
            module_to_file[f"{base}/index"] = path
            module_to_file[base] = path
            for ext in KNOWN_EXTS:
                module_to_file[f"{base}/index{ext}"] = path

    # Pass 2: per-file resolved imports.
    # Only NAMED imports count as proof of linkage. Side-effect imports
    # (`import 'x'`) don't bind any local symbol so they can't be used
    # to resolve bare calls.
    file_imports: dict[str, dict[str, tuple[str, str]]] = {}
    file_imports_raw: dict[str, list[dict]] = {}
    for f in ast_data["files"]:
        path = f["path"]
        local_map: dict[str, tuple[str, str]] = {}
        raw: list[dict] = []
        for imp in f.get("imports", []):
            source = imp.get("source")
            if not source:
                continue
            original = imp.get("name") or "default"
            alias = imp.get("alias") or original
            if alias and alias != source:
                local_map[alias] = (original, source)
            raw.append(
                {
                    "name": original,
                    "alias": alias if alias != source else None,
                    "source": source,
                    "line_number": imp.get("line_number"),
                }
            )
        file_imports[path] = local_map
        file_imports_raw[path] = raw

    # Build import->file map for incoming call validation
    # Maps callee_file -> set of files that import it
    callee_to_importers: dict[str, set[str]] = defaultdict(set)
    for importer_file, local_map in file_imports.items():
        for local_name, (original, source) in local_map.items():
            resolved = _module_to_file(source, importer_file, all_files)
            if resolved:
                callee_to_importers[resolved].add(importer_file)

    # Pass 3 & 4: extract and classify
    per_file: dict[str, dict] = {}
    for pr in pr_files_set:
        per_file[pr] = {
            "imports": [],
            "internal_calls": [],
            "incoming_calls": [],
            "outgoing_calls": [],
        }

    # Track all call edges for aggregation
    # Key: (caller_file, caller_scope, callee_qualified, callee_file)
    edge_map_internal: dict[tuple, dict] = {}
    edge_map_incoming: dict[tuple, dict] = {}
    edge_map_outgoing: dict[tuple, dict] = {}

    for f in ast_data["files"]:
        caller_file = f["path"]
        caller_in_pr = caller_file in pr_files_set

        ff = file_funcs.get(caller_file, {})
        fc = file_classes.get(caller_file, {})
        fm = file_methods.get(caller_file, {})
        imps = file_imports.get(caller_file, {})

        for call in f.get("function_calls", []):
            if not call or not call.get("name"):
                continue
            # Skip obvious built-ins noise
            full = call.get("full_name") or ""
            if full.startswith(("new ", "typeof ", "void ")):
                continue

            # Determine caller scope: prefer AST context, fall back to
            # line-range matching against the file's functions.
            ctx = call.get("context") or []
            call_line = call.get("line_number", 0)
            if isinstance(ctx, (list, tuple)) and ctx and ctx[0]:
                caller_func_name = ctx[0]
                caller_func_line = ctx[2] if len(ctx) > 2 else 0
                caller_scope = caller_func_name
            else:
                # Line-range fallback: find the innermost function whose
                # line range contains this call.
                enclosing = None
                for fn in f.get("functions", []):
                    start = fn.get("line_number", 0)
                    end = fn.get("end_line") or start
                    if start <= call_line <= end:
                        # Pick the smallest (innermost) range
                        if enclosing is None or (end - start) < (
                            (enclosing.get("end_line") or enclosing.get("line_number", 0))
                            - enclosing.get("line_number", 0)
                        ):
                            enclosing = fn
                if enclosing:
                    name = enclosing.get("name") or "<anonymous>"
                    cls = enclosing.get("class_context")
                    if isinstance(cls, str) and cls:
                        caller_func_name = f"{cls}.{name}"
                    else:
                        caller_func_name = name
                    caller_func_line = enclosing.get("line_number", 0)
                    caller_scope = caller_func_name
                else:
                    caller_func_name = "<module>"
                    caller_func_line = 0
                    caller_scope = "<module>"

            # Resolve callee
            resolved = _resolve_call(
                call,
                caller_file,
                caller_func_name,
                ff,
                fm,
                fc,
                imps,
                module_to_file,
                global_funcs,
                global_methods,
                pr_files_set,
                all_files,
            )

            callee_file = resolved["callee_file"]
            callee_in_pr = callee_file in pr_files_set if callee_file else False

            # For untracked calls, try fallback name-based resolution
            if not callee_file:
                call_name = call.get("name", "")
                if call_name in global_funcs:
                    # Use first definition found as fallback
                    fallback_defs = global_funcs[call_name]
                    if fallback_defs:
                        fallback = fallback_defs[0]
                        callee_file = fallback["file"]
                        callee_in_pr = callee_file in pr_files_set
                        resolved["callee_qualified"] = fallback["qualified"]
                        resolved["callee_line"] = fallback["line"]
                        resolved["resolution"] = "name_match"

            # Skip if we still can't determine the callee location
            if not callee_file:
                continue

            # Classify and track based on caller/callee PR membership
            if caller_in_pr and callee_in_pr:
                # Internal (same file or cross-file within PR)
                # Require strict resolution for internal calls
                if not resolved["callee_line"]:
                    continue
                if resolved["resolution"] not in _DEFINITION_RESOLUTIONS:
                    continue
                key = (
                    caller_file,
                    caller_scope,
                    resolved["callee_qualified"],
                    callee_file,
                )
                target_map = edge_map_internal
            elif not caller_in_pr and callee_in_pr:
                # Incoming (external -> PR)
                # Strict: require import binding to prove the call is real
                if not resolved["callee_line"]:
                    continue
                # Check if caller_file actually imports callee_file
                if caller_file not in callee_to_importers.get(callee_file, set()):
                    continue
                key = (
                    caller_file,
                    caller_scope,
                    resolved["callee_qualified"],
                    callee_file,
                )
                target_map = edge_map_incoming
            elif caller_in_pr and not callee_in_pr:
                # Outgoing (PR -> external)
                # Strict: require import binding (resolution must be in DEFINITION_RESOLUTIONS)
                if not resolved["callee_line"]:
                    continue
                if resolved["resolution"] not in _DEFINITION_RESOLUTIONS:
                    continue
                key = (
                    caller_file,
                    caller_scope,
                    resolved["callee_qualified"],
                    callee_file,
                )
                target_map = edge_map_outgoing
            else:
                # External->external: skip
                continue

            if key not in target_map:
                target_map[key] = {
                    "caller_file": caller_file,
                    "caller_function": caller_func_name,
                    "caller_scope": caller_scope,
                    "caller_line": caller_func_line,
                    "callee": resolved["callee"],
                    "callee_qualified": resolved["callee_qualified"],
                    "callee_file": callee_file,
                    "callee_line": resolved["callee_line"],
                    "resolution": resolved["resolution"],
                    "receiver": resolved["receiver"],
                    "is_method": resolved["is_method"],
                    "call_sites": [],
                }

            target_map[key]["call_sites"].append(
                {
                    "line": resolved["call_line"],
                    "call": resolved["call_full"][:200],
                    "args": (call.get("args") or [])[:5],
                }
            )

    # Bucket internal edges into per-file buckets (by caller_file)
    for edge in edge_map_internal.values():
        caller_file = edge["caller_file"]
        if caller_file in per_file:
            sites = edge["call_sites"]
            edge["frequency"] = len(sites)
            edge["call_sites"] = sites[:5]
            per_file[caller_file]["internal_calls"].append(edge)

    # Bucket incoming edges into per-file buckets (by callee_file)
    for edge in edge_map_incoming.values():
        callee_file = edge["callee_file"]
        if callee_file in per_file:
            sites = edge["call_sites"]
            edge["frequency"] = len(sites)
            edge["call_sites"] = sites[:5]
            per_file[callee_file]["incoming_calls"].append(edge)

    # Bucket outgoing edges into per-file buckets (by caller_file)
    for edge in edge_map_outgoing.values():
        caller_file = edge["caller_file"]
        if caller_file in per_file:
            sites = edge["call_sites"]
            edge["frequency"] = len(sites)
            edge["call_sites"] = sites[:5]
            per_file[caller_file]["outgoing_calls"].append(edge)

    # Add resolved imports for PR files
    for pr_file in pr_files_set:
        raw = file_imports_raw.get(pr_file, [])
        resolved_imports = []
        for imp in raw:
            source = imp["source"]
            # Use the path-aware resolver so relative imports work
            target = _module_to_file(source, pr_file, all_files)
            if target is None:
                target = module_to_file.get(source)
            resolved_imports.append(
                {
                    "local_name": imp["alias"] or imp["name"],
                    "original_name": imp["name"],
                    "source": source,
                    "resolved_file": target,
                    "resolved": target is not None,
                    "line_number": imp.get("line_number"),
                }
            )
        per_file[pr_file]["imports"] = resolved_imports

    # Sort internal/incoming/outgoing lists
    for pr_file in per_file:
        per_file[pr_file]["internal_calls"].sort(
            key=lambda x: (x["caller_scope"], x["callee_qualified"])
        )
        per_file[pr_file]["incoming_calls"].sort(
            key=lambda x: (x["caller_file"], x["caller_scope"], x["callee_qualified"])
        )
        per_file[pr_file]["outgoing_calls"].sort(
            key=lambda x: (x["caller_scope"], x["callee_qualified"])
        )

    # Summary
    total_internal = sum(len(v["internal_calls"]) for v in per_file.values())
    total_incoming = sum(len(v["incoming_calls"]) for v in per_file.values())
    total_outgoing = sum(len(v["outgoing_calls"]) for v in per_file.values())
    total_calls = sum(len(fi.get("function_calls", [])) for fi in ast_data["files"])

    return {
        "pr_files": sorted(pr_files_set),
        "total_pr_files": len(pr_files_set),
        "per_file": per_file,
        "summary": {
            "internal_calls": total_internal,
            "incoming_calls": total_incoming,
            "outgoing_calls": total_outgoing,
            "total_call_sites_in_repo": total_calls,
            "total_files_in_repo": len(all_files),
            "resolution_breakdown": _count_resolutions(
                edge_map_internal, edge_map_incoming, edge_map_outgoing
            ),
        },
    }


def _count_resolutions(internal: dict, incoming: dict, outgoing: dict) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for edge in list(internal.values()) + list(incoming.values()) + list(outgoing.values()):
        counts[edge["resolution"]] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _humanize_scope(scope: str) -> str:
    """Replace placeholder scope names with human-friendly labels."""
    if scope == "<module>":
        return "module top-level"
    return scope


def _args_repr(args: list[str]) -> str:
    """Render an arg list, normalized to one line and truncated."""
    if not args:
        return "()"
    cleaned: list[str] = []
    for a in args:
        a = a.strip()
        if not a:
            continue
        # Collapse multi-line args to a single line
        a = re.sub(r"\s+", " ", a)
        cleaned.append(a)
    if not cleaned:
        return "()"
    rendered = ", ".join(cleaned)
    if len(rendered) > 60:
        rendered = rendered[:57].rstrip() + "..."
    return f"({rendered})"


def _callee_with_args(edge: dict) -> str:
    """Render callee as `name(args)` using the first sample call site."""
    name = edge["callee_qualified"]
    sites = edge.get("call_sites") or []
    if sites:
        sample = sites[0]
        args = sample.get("args") or []
        if args:
            return f"{name}{_args_repr(args)}"
    return f"{name}()"


def _caller_with_args(edge: dict) -> str:
    """Render caller as `name(args)` for the incoming-call display."""
    name = edge["caller_scope"]
    return f"{name}()"


def render_callgraph_markdown(graph: dict) -> str:
    """
    Render the call graph as markdown for an agent.

    Layout:
      ## File: <path>
      File has N functions/methods.

        ### `name()` at file:line
        - `caller()` -> `callee(args)` at `file:line`  (xN)

      The `caller` is humanized (e.g. "module top-level" instead of
      "<module>"), and each call shows the arguments from the first
      sample site so the agent can see what is being passed.
    """
    lines: list[str] = ["# Call Graph", ""]

    per_file = graph.get("per_file", {})
    pr_files = graph.get("pr_files", [])

    summary = graph.get("summary", {})
    lines.append(
        f"PR files: {len(pr_files)} | "
        f"Internal edges: {summary.get('internal_calls', 0)} | "
        f"Incoming edges: {summary.get('incoming_calls', 0)} | "
        f"Outgoing edges: {summary.get('outgoing_calls', 0)}"
    )
    lines.append("")

    for pr_file in sorted(per_file):
        entry = per_file[pr_file]
        calls_out = entry.get("internal_calls", [])
        calls_in = entry.get("incoming_calls", [])
        calls_external = entry.get("outgoing_calls", [])

        if not calls_out and not calls_in and not calls_external:
            continue

        # Count distinct caller scopes for the file's outbound edges
        unique_callers = {e["caller_scope"] for e in calls_out}

        lines.append(f"## File: {pr_file}")
        lines.append("")
        n_funcs = len(unique_callers)
        n_out = len(calls_out)
        func_word = "function/method" if n_funcs == 1 else "functions/methods"
        edge_word = "edge" if n_out == 1 else "edges"
        lines.append(f"File has {n_funcs} {func_word} with {n_out} internal call {edge_word}.")
        if calls_in:
            n_in = len(calls_in)
            in_word = "edge" if n_in == 1 else "edges"
            lines.append(f"{n_in} incoming call {in_word} from outside the PR.")
        if calls_external:
            n_ext = len(calls_external)
            ext_word = "edge" if n_ext == 1 else "edges"
            lines.append(f"{n_ext} outgoing call {ext_word} to external files.")
        lines.append("")

        # Outbound: group by caller scope, one ### section per method
        out_by_caller: dict[str, list[dict]] = {}
        for e in calls_out:
            out_by_caller.setdefault(e["caller_scope"], []).append(e)

        for caller_scope in sorted(out_by_caller):
            edges = out_by_caller[caller_scope]
            first = edges[0]
            display = _humanize_scope(caller_scope)
            caller_line = first.get("caller_line") or 0
            lines.append(f"### `{display}` at {pr_file}:{caller_line}")
            lines.append("")
            lines.append("Calls:")
            for e in sorted(edges, key=lambda x: x["callee_qualified"]):
                target = _callee_with_args(e)
                target_loc = f"`{e['callee_file']}:{e['callee_line']}`"
                arrow = f"- `{target}` at {target_loc}"
                freq = e.get("frequency", 1)
                if freq > 1:
                    arrow += f"  (x{freq})"
                lines.append(arrow)
            lines.append("")

        # Inbound: group by callee, show "Called by"
        in_by_callee: dict[str, list[dict]] = {}
        for e in calls_in:
            in_by_callee.setdefault(e["callee_qualified"], []).append(e)

        for callee_q in sorted(in_by_callee):
            edges = in_by_callee[callee_q]
            first = edges[0]
            lines.append(f"### `{callee_q}` at {first['callee_file']}:{first['callee_line']}")
            lines.append("")
            lines.append("Called by:")
            seen: set[tuple[str, int]] = set()
            for e in edges:
                key = (e["caller_file"], e["caller_line"])
                if key in seen:
                    continue
                seen.add(key)
                caller_disp = _humanize_scope(e["caller_scope"])
                lines.append(f"- `{caller_disp}` at `{e['caller_file']}:{e['caller_line']}`")
            lines.append("")

        # Outbound to external: group by caller scope
        out_ext_by_caller: dict[str, list[dict]] = {}
        for e in calls_external:
            out_ext_by_caller.setdefault(e["caller_scope"], []).append(e)

        for caller_scope in sorted(out_ext_by_caller):
            edges = out_ext_by_caller[caller_scope]
            first = edges[0]
            display = _humanize_scope(caller_scope)
            caller_line = first.get("caller_line") or 0
            lines.append(f"### `{display}` at {pr_file}:{caller_line} (external)")
            lines.append("")
            lines.append("Calls external:")
            for e in sorted(edges, key=lambda x: x["callee_qualified"]):
                target = _callee_with_args(e)
                target_loc = f"`{e['callee_file']}:{e['callee_line']}`"
                arrow = f"- `{target}` at {target_loc}"
                freq = e.get("frequency", 1)
                if freq > 1:
                    arrow += f"  (x{freq})"
                lines.append(arrow)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_blast_radius_markdown(graph: dict) -> str:
    """
    Render a focused blast-radius summary for code review agents.

    Per changed file, shows:
    - Functions defined and their call relationships
    - Impact analysis (incoming from external/internal)
    - Dependencies (outgoing to external/internal)
    """
    lines: list[str] = ["# Call Graph - Blast Radius Summary", ""]

    per_file = graph.get("per_file", {})
    pr_files = graph.get("pr_files", [])
    summary = graph.get("summary", {})

    lines.append(f"**PR files:** {len(pr_files)} | **Total internal edges:** {summary.get('internal_calls', 0)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for pr_file in sorted(per_file):
        entry = per_file[pr_file]
        internal = entry.get("internal_calls", [])
        incoming = entry.get("incoming_calls", [])
        outgoing = entry.get("outgoing_calls", [])

        if not internal and not incoming and not outgoing:
            continue

        lines.append(f"## {pr_file}")
        lines.append("")

        # Build function-level summary
        func_data: dict[str, dict] = {}

        for edge in internal:
            fn = edge["caller_scope"]
            if fn not in func_data:
                func_data[fn] = {
                    "line": edge.get("caller_line", 0),
                    "internal_callees": [],
                    "internal_callers": [],
                    "external_callees": [],
                    "external_callers": [],
                }
            func_data[fn]["internal_callees"].append({
                "name": edge["callee_qualified"],
                "file": edge["callee_file"],
                "line": edge["callee_line"],
            })

        for edge in incoming:
            fn = edge["callee_qualified"]
            if fn not in func_data:
                func_data[fn] = {
                    "line": edge.get("callee_line", 0),
                    "internal_callees": [],
                    "internal_callers": [],
                    "external_callees": [],
                    "external_callers": [],
                }
            func_data[fn]["internal_callers"].append({
                "name": edge["caller_scope"],
                "file": edge["caller_file"],
                "line": edge["caller_line"],
            })

        for edge in outgoing:
            fn = edge["caller_scope"]
            if fn not in func_data:
                func_data[fn] = {
                    "line": edge.get("caller_line", 0),
                    "internal_callees": [],
                    "internal_callers": [],
                    "external_callees": [],
                    "external_callers": [],
                }
            func_data[fn]["external_callees"].append({
                "name": edge["callee_qualified"],
                "file": edge["callee_file"],
                "line": edge["callee_line"],
            })

        if not func_data:
            lines.append("*No function-level call data available.*")
            lines.append("")
            continue

        # Summary counts
        total_incoming = sum(len(v["internal_callers"]) for v in func_data.values())
        total_outgoing = sum(len(v["internal_callees"]) + len(v["external_callees"]) for v in func_data.values())

        lines.append(f"**{len(func_data)} functions** | Incoming: {total_incoming} | Outgoing: {total_outgoing}")
        lines.append("")

        # Per-function details
        lines.append("### Functions")
        lines.append("")

        for fn_name in sorted(func_data.keys(), key=lambda x: func_data[x]["line"]):
            fd = func_data[fn_name]
            callers = fd["internal_callers"]
            callees_internal = fd["internal_callees"]
            callees_external = fd["external_callees"]

            impact = len(callers)
            impact_label = "HIGH" if impact >= 3 else "MEDIUM" if impact >= 1 else "LOW"

            lines.append(f"**`{fn_name}`** at line {fd['line']} | Impact: {impact_label} ({impact} callers)")
            lines.append("")

            if callers:
                lines.append("  Called by:")
                for caller in sorted(callers, key=lambda x: x["line"]):
                    lines.append(f"  - `{caller['name']}` in `{caller['file']}`:{caller['line']}")
                lines.append("")

            if callees_internal:
                lines.append("  Calls internally:")
                for callee in sorted(callees_internal, key=lambda x: x["line"]):
                    lines.append(f"  - `{callee['name']}` at `{callee['file']}`:{callee['line']}")
                lines.append("")

            if callees_external:
                lines.append("  Calls external:")
                for callee in sorted(callees_external, key=lambda x: x["line"]):
                    lines.append(f"  - `{callee['name']}` in `{callee['file']}`:{callee['line']}")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    ast_path: str = "output/ast.json",
    pr_files_path: str | None = None,
    output_path: str = "output/call_graph.json",
) -> dict:
    ast_data = json.loads(Path(ast_path).read_text())

    if pr_files_path:
        pr_files = json.loads(Path(pr_files_path).read_text())
    else:
        # Default: all files (used for whole-repo graphs)
        pr_files = [f["path"] for f in ast_data["files"]]

    graph = analyze_pr_call_graph(ast_data, pr_files)
    Path(output_path).write_text(json.dumps(graph, indent=2))
    return graph


if __name__ == "__main__":
    import sys

    ast_path = sys.argv[1] if len(sys.argv) > 1 else "output/ast.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/call_graph.json"
    g = main(ast_path, output_path=output_path)
    s = g["summary"]
    print(f"PR files: {g['total_pr_files']}")
    print(f"Internal calls: {s['internal_calls']}")
    print(f"Incoming calls: {s['incoming_calls']}")
    print(f"Outgoing calls: {s['outgoing_calls']}")
    print(f"Resolution breakdown: {s['resolution_breakdown']}")
