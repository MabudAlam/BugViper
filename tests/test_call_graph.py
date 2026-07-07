"""Tests for the call graph builder."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_parser.call_graph import analyze_pr_call_graph, render_callgraph_markdown


def _make_ast(files):
    """Build a minimal ast_data dict from a list of file specs."""
    return {
        "files": files,
        "statistics": {"files_parsed": len(files)},
    }


def _file(
    path, language="typescript", functions=None, classes=None, imports_=None, function_calls=None
):
    return {
        "path": path,
        "language": language,
        "functions": functions or [],
        "classes": classes or [],
        "imports": imports_ or [],
        "function_calls": function_calls or [],
    }


def _fn(name, line, end_line=None, class_context=None):
    return {
        "name": name,
        "line_number": line,
        "end_line": end_line,
        "class_context": class_context,
        "args": [],
        "lang": "typescript",
    }


def _cls(name, line):
    return {
        "name": name,
        "line_number": line,
        "bases": [],
        "decorators": [],
        "lang": "typescript",
    }


def _imp(name, source, alias=None):
    return {
        "name": name,
        "source": source,
        "alias": alias,
        "line_number": 1,
        "lang": "typescript",
    }


def _call(name, full_name, line, ctx=None, args=None):
    return {
        "name": name,
        "full_name": full_name,
        "line_number": line,
        "args": args or [],
        "inferred_obj_type": None,
        "context": ctx or [None, None, None],
        "class_context": [None, None],
        "lang": "typescript",
        "is_dependency": False,
    }


def test_local_function_call():
    """Function calling another function in same file."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[
                    _fn("foo", 1, end_line=10),
                    _fn("bar", 5, end_line=8),
                ],
                function_calls=[
                    _call("bar", "bar()", 3, ctx=["foo", "function_declaration", 1]),
                ],
            )
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 1
    edge = g["per_file"]["a.ts"]["internal_calls"][0]
    assert edge["callee"] == "bar"
    assert edge["callee_file"] == "a.ts"
    assert edge["resolution"] == "local"
    print("PASS: test_local_function_call")


def test_imported_function_call():
    """Function calling another from imported module."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=10)],
                imports_=[_imp("fooFn", "./b")],
                function_calls=[
                    _call("fooFn", "fooFn()", 3, ctx=["foo", "function_declaration", 1]),
                ],
            ),
            _file("b.ts", functions=[_fn("fooFn", 1, end_line=10)]),
        ]
    )
    # Both files in PR so the cross-file call is internal
    g = analyze_pr_call_graph(ast, ["a.ts", "b.ts"])
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 1
    edge = g["per_file"]["a.ts"]["internal_calls"][0]
    # fooFn may resolve via import (imported) or via global (pr_global)
    assert edge["callee_file"] == "b.ts"
    assert edge["callee_line"] == 1
    assert edge["resolution"] in ("imported", "pr_global")
    print("PASS: test_imported_function_call")


def test_incoming_call():
    """External file calling PR file's function via import."""
    ast = _make_ast(
        [
            _file("a.ts", functions=[_fn("helper", 1, end_line=10)]),
            _file(
                "b.ts",
                functions=[_fn("foo", 1, end_line=10)],
                imports_=[_imp("helper", "./a")],
                function_calls=[
                    _call("helper", "helper()", 3, ctx=["foo", "function_declaration", 1]),
                ],
            ),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    # b.ts is NOT in PR, a.ts IS in PR, so helper call from b -> a is incoming
    assert len(g["per_file"]["a.ts"]["incoming_calls"]) == 1
    edge = g["per_file"]["a.ts"]["incoming_calls"][0]
    assert edge["caller_file"] == "b.ts"
    assert edge["callee_file"] == "a.ts"
    assert edge["callee"] == "helper"
    assert edge["resolution"] == "imported"
    print("PASS: test_incoming_call")


def test_method_call_resolution():
    """Method call on imported object should resolve to import source."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=10)],
                imports_=[_imp("store", "./store")],
                function_calls=[
                    _call("update", "store.update()", 3, ctx=["foo", "function_declaration", 1]),
                ],
            ),
            _file(
                "store.ts",
                functions=[
                    _fn("update", 1, end_line=10),
                    _fn("set", 5, end_line=15),
                ],
            ),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts", "store.ts"])
    edge = g["per_file"]["a.ts"]["internal_calls"][0]
    assert edge["callee_file"] == "store.ts"
    assert edge["callee"] == "update"
    print("PASS: test_method_call_resolution")


def test_builtin_call_not_resolved():
    """Built-in calls should not be resolved to PR files."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=10)],
                function_calls=[
                    _call("log", "console.log()", 3, ctx=["foo", "function_declaration", 1]),
                    _call("parseInt", "parseInt('1')", 5, ctx=["foo", "function_declaration", 1]),
                    _call(
                        "getItem",
                        "localStorage.getItem('x')",
                        7,
                        ctx=["foo", "function_declaration", 1],
                    ),
                ],
            ),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    # None should be classified as internal (these are all builtins)
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 0
    print("PASS: test_builtin_call_not_resolved")


def test_frequency_aggregation():
    """Multiple calls to same function should aggregate by frequency."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[
                    _fn("foo", 1, end_line=20),
                    _fn("helper", 10, end_line=15),
                ],
                function_calls=[
                    _call("helper", "helper()", 3, ctx=["foo", "function_declaration", 1]),
                    _call("helper", "helper()", 5, ctx=["foo", "function_declaration", 1]),
                    _call("helper", "helper()", 7, ctx=["foo", "function_declaration", 1]),
                ],
            )
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    edge = g["per_file"]["a.ts"]["internal_calls"][0]
    assert edge["frequency"] == 3
    assert len(edge["call_sites"]) <= 5
    print("PASS: test_frequency_aggregation")


def test_no_false_positive_for_chained_call():
    """createHash().update() should not resolve to a PR-defined update()."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=20), _fn("update", 10, end_line=15)],
                function_calls=[
                    _call(
                        "update",
                        'createHash("sha256").update(key)',
                        5,
                        ctx=["foo", "function_declaration", 1],
                    ),
                ],
            )
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    # Should NOT map update to the local update function
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 0
    print("PASS: test_no_false_positive_for_chained_call")


def test_side_effect_import_does_not_resolve_bare_calls():
    """Side-effect imports (`import 'x'`) don't bind any local symbol.

    A bare call to a function that lives in the side-effect-imported
    module cannot be linked without a real local binding - so it is
    dropped from the call graph.
    """
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=20)],
                imports_=[_imp("@/lib/utils", "@/lib/utils")],
                function_calls=[
                    _call("addOne", "addOne(1)", 3, ctx=["foo", "function_declaration", 1]),
                ],
            ),
            _file("lib/utils.ts", functions=[_fn("addOne", 1, end_line=10)]),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts", "lib/utils.ts"])
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 0
    print("PASS: test_side_effect_import_does_not_resolve_bare_calls")


def test_drops_unresolved_cross_file_bare_call():
    """Bare cross-file call without an import is dropped.

    The caller has no import for `helper` and no receiver on the call -
    this is the kind of name-collision guess we can't trust.
    """
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("main", 1, end_line=20)],
                function_calls=[
                    _call("helper", "helper()", 3, ctx=["main", "function_declaration", 1]),
                ],
            ),
            _file("b.ts", functions=[_fn("helper", 1, end_line=10)]),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts", "b.ts"])
    # No import in a.ts for helper, no receiver, no proof of linkage -
    # the bare-name fallback (pr_global) is filtered out.
    assert len(g["per_file"]["a.ts"]["internal_calls"]) == 0
    print("PASS: test_drops_unresolved_cross_file_bare_call")


def test_no_pr_files_returns_empty():
    """No PR files should produce empty per_file."""
    ast = _make_ast(
        [
            _file("a.ts", functions=[_fn("foo", 1, end_line=10)]),
        ]
    )
    g = analyze_pr_call_graph(ast, [])
    assert g["total_pr_files"] == 0
    assert g["per_file"] == {}
    print("PASS: test_no_pr_files_returns_empty")


def test_render_callgraph_markdown_basic():
    """Each method lists its outgoing calls with frequency and file path."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[_fn("foo", 1, end_line=10), _fn("bar", 5, end_line=8)],
                function_calls=[
                    _call("bar", "bar(x)", 3, ctx=["foo", "function_declaration", 1], args=["x"]),
                    _call("bar", "bar(x)", 4, ctx=["foo", "function_declaration", 1], args=["x"]),
                ],
            ),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    md = render_callgraph_markdown(g)
    assert "## File: a.ts" in md
    assert "File has 1 function/method" in md
    assert "with 1 internal call edge" in md
    assert "### `foo` at a.ts:1" in md
    assert "`bar(x)`" in md
    assert "at `a.ts:5`" in md
    assert "(x2)" in md
    print("PASS: test_render_callgraph_markdown_basic")


def test_render_callgraph_markdown_incoming():
    """Incoming edges appear under the callee with their callers listed."""
    ast = _make_ast(
        [
            _file("a.ts", functions=[_fn("helper", 1, end_line=10)]),
            _file(
                "b.ts",
                functions=[_fn("foo", 1, end_line=10)],
                imports_=[_imp("helper", "./a")],
                function_calls=[
                    _call("helper", "helper()", 3, ctx=["foo", "function_declaration", 1]),
                ],
            ),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    md = render_callgraph_markdown(g)
    assert "## File: a.ts" in md
    assert "1 incoming call edge from outside the PR" in md
    assert "### `helper` at a.ts:1" in md
    assert "Called by:" in md
    assert "`foo` at `b.ts:1`" in md
    print("PASS: test_render_callgraph_markdown_incoming")


def test_render_callgraph_markdown_module_label():
    """<module> gets humanized to 'module top-level'."""
    # Use a file with NO enclosing functions so the call stays at <module>.
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[],
                imports_=[_imp("bar", "./b")],
                function_calls=[
                    _call("bar", "bar()", 1, ctx=[None, None, None]),
                ],
            ),
            _file("b.ts", functions=[_fn("bar", 1, end_line=10)]),
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts", "b.ts"])
    md = render_callgraph_markdown(g)
    assert "module top-level" in md
    assert "<module>" not in md
    print("PASS: test_render_callgraph_markdown_module_label")


def test_class_method_resolution_via_this():
    """Method called on `this` should resolve to current class's method."""
    ast = _make_ast(
        [
            _file(
                "a.ts",
                functions=[
                    _fn("helper", 5, end_line=10, class_context="MyClass"),
                    _fn("run", 12, end_line=20, class_context="MyClass"),
                ],
                classes=[_cls("MyClass", 1)],
                function_calls=[
                    _call("helper", "this.helper()", 15, ctx=["run", "method_definition", 12]),
                ],
            )
        ]
    )
    g = analyze_pr_call_graph(ast, ["a.ts"])
    edge = g["per_file"]["a.ts"]["internal_calls"][0]
    assert edge["callee"] == "helper"
    assert edge["callee_qualified"] == "MyClass.helper"
    assert edge["callee_file"] == "a.ts"
    assert edge["resolution"] in ("this_method", "local_method", "local")
    print("PASS: test_class_method_resolution_via_this")


if __name__ == "__main__":
    test_local_function_call()
    test_imported_function_call()
    test_incoming_call()
    test_method_call_resolution()
    test_builtin_call_not_resolved()
    test_frequency_aggregation()
    test_no_false_positive_for_chained_call()
    test_side_effect_import_does_not_resolve_bare_calls()
    test_drops_unresolved_cross_file_bare_call()
    test_no_pr_files_returns_empty()
    test_class_method_resolution_via_this()
    test_render_callgraph_markdown_basic()
    test_render_callgraph_markdown_incoming()
    test_render_callgraph_markdown_module_label()
    print("\nAll tests passed!")
