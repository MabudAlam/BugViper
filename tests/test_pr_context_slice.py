from knowledge_parser.knowledge_runner import build_call_graph, build_functions_detail, parse_project, slice_for_files


def test_pr_slice_includes_incoming_outgoing_and_unresolved_context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "handler.py").write_text(
        """
from service import validate_user, save_user


def handle(payload):
    user = validate_user(payload)
    dynamic_dispatch(user)
    return save_user(user)
""".lstrip()
    )
    (repo / "service.py").write_text(
        """
def validate_user(payload):
    return payload


def save_user(user):
    return user
""".lstrip()
    )
    (repo / "caller.py").write_text(
        """
from handler import handle


def route(request):
    return handle(request)
""".lstrip()
    )

    ast = parse_project(str(repo), "acme", "demo")
    graph = build_call_graph(ast)
    functions = build_functions_detail(ast, graph)
    pr = slice_for_files(graph, functions, ["handler.py"])

    assert pr["schema_version"] == 2
    assert pr["summary"]["directly_affected_functions"] == 1
    assert pr["summary"]["incoming_callers"] >= 1
    assert pr["summary"]["outgoing_callees"] >= 2
    assert pr["summary"]["unresolved_calls_from_changed"] >= 1

    incoming = pr["relationships"]["incoming_callers"]
    assert any(edge["from_file"] == "caller.py" and edge["from_function"] == "route" for edge in incoming)
    assert any(edge.get("caller", {}).get("name") == "route" for edge in incoming)

    outgoing = pr["relationships"]["outgoing_callees"]
    assert any(edge["to_file"] == "service.py" and edge["to_name"] == "validate_user" for edge in outgoing)
    assert any(edge["to_file"] == "service.py" and edge["to_name"] == "save_user" for edge in outgoing)

    unresolved = pr["relationships"]["unresolved_calls_from_changed"]
    assert any(call["to_name"] == "dynamic_dispatch" for call in unresolved)

    hints = " ".join(pr["review_hints"])
    assert "incoming_callers" in hints
    assert "unresolved_calls_from_changed" in hints
