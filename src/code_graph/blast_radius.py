def render_callgraph_markdown(graph: dict) -> str:
    lines = ["# Call Graph", ""]
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

        out_by_caller: dict[str, list[dict]] = {}
        for e in calls_out:
            out_by_caller.setdefault(e["caller_scope"], []).append(e)

        for caller_scope in sorted(out_by_caller):
            edges = out_by_caller[caller_scope]
            first = edges[0]
            caller_line = first.get("caller_line") or 0
            lines.append(f"### `{caller_scope}` at {pr_file}:{caller_line}")
            lines.append("")
            lines.append("Calls:")
            for e in sorted(edges, key=lambda x: x["callee_qualified"]):
                target = f"{e['callee_qualified']}()"
                target_loc = f"`{e['callee_file']}:{e['callee_line']}`"
                arrow = f"- `{target}` at {target_loc}"
                freq = e.get("frequency", 1)
                if freq > 1:
                    arrow += f"  (x{freq})"
                lines.append(arrow)
            lines.append("")

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
                lines.append(f"- `{e['caller_scope']}` at `{e['caller_file']}:{e['caller_line']}`")
            lines.append("")

        out_ext_by_caller: dict[str, list[dict]] = {}
        for e in calls_external:
            out_ext_by_caller.setdefault(e["caller_scope"], []).append(e)

        for caller_scope in sorted(out_ext_by_caller):
            edges = out_ext_by_caller[caller_scope]
            first = edges[0]
            caller_line = first.get("caller_line") or 0
            lines.append(f"### `{caller_scope}` at {pr_file}:{caller_line} (external)")
            lines.append("")
            lines.append("Calls external:")
            for e in sorted(edges, key=lambda x: x["callee_qualified"]):
                target = f"{e['callee_qualified']}()"
                target_loc = f"`{e['callee_file']}:{e['callee_line']}`"
                arrow = f"- `{target}` at {target_loc}"
                freq = e.get("frequency", 1)
                if freq > 1:
                    arrow += f"  (x{freq})"
                lines.append(arrow)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_blast_radius_markdown(graph: dict) -> str:
    lines = ["# Call Graph - Blast Radius Summary", ""]
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

        func_data: dict[str, dict] = {}
        for edge in internal:
            fn = edge["caller_scope"]
            func_data.setdefault(fn, {
                "line": edge.get("caller_line", 0),
                "internal_callees": [], "internal_callers": [],
                "external_callees": [], "external_callers": [],
            })
            func_data[fn]["internal_callees"].append({
                "name": edge["callee_qualified"],
                "file": edge["callee_file"],
                "line": edge["callee_line"],
            })

        for edge in incoming:
            fn = edge["callee_qualified"]
            func_data.setdefault(fn, {
                "line": edge.get("callee_line", 0),
                "internal_callees": [], "internal_callers": [],
                "external_callees": [], "external_callers": [],
            })
            func_data[fn]["internal_callers"].append({
                "name": edge["caller_scope"],
                "file": edge["caller_file"],
                "line": edge["caller_line"],
            })

        for edge in outgoing:
            fn = edge["caller_scope"]
            func_data.setdefault(fn, {
                "line": edge.get("caller_line", 0),
                "internal_callees": [], "internal_callers": [],
                "external_callees": [], "external_callers": [],
            })
            func_data[fn]["external_callees"].append({
                "name": edge["callee_qualified"],
                "file": edge["callee_file"],
                "line": edge["callee_line"],
            })

        if not func_data:
            lines.append("*No function-level call data available.*")
            lines.append("")
            continue

        total_incoming = sum(len(v["internal_callers"]) for v in func_data.values())
        total_outgoing = sum(len(v["internal_callees"]) + len(v["external_callees"]) for v in func_data.values())
        lines.append(f"**{len(func_data)} functions** | Incoming: {total_incoming} | Outgoing: {total_outgoing}")
        lines.append("")
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
