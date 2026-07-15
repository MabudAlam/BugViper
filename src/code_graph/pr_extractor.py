from collections import defaultdict


def extract_pr_call_graph(graph: dict, pr_files: list[str]) -> dict:
    pr_set = set(pr_files)
    nodes = graph.get('nodes', [])
    edges = graph.get('edges', [])

    file_nodes = {n['id']: n for n in nodes if n['type'] == 'File'}
    func_nodes = {n['id']: n for n in nodes if n['type'] == 'Function'}
    class_nodes = {n['id']: n for n in nodes if n['type'] == 'Class'}
    module_nodes = {n['id']: n for n in nodes if n['type'] == 'Module'}

    func_to_file: dict[str, str] = {}
    for e in edges:
        if e['type'] == 'DEFINED_IN' and e['source'] in func_nodes:
            target_file = file_nodes.get(e['target'], {}).get('label', '')
            func_to_file[e['source']] = target_file

    class_to_file: dict[str, str] = {}
    for e in edges:
        if e['type'] == 'DEFINED_IN' and e['source'] in class_nodes:
            target_file = file_nodes.get(e['target'], {}).get('label', '')
            class_to_file[e['source']] = target_file

    file_to_funcs: dict[str, list[dict]] = defaultdict(list)
    for fn_id, fn in func_nodes.items():
        f = func_to_file.get(fn_id, '')
        file_to_funcs[f].append(fn)

    file_to_classes: dict[str, list[dict]] = defaultdict(list)
    for cls_id, cls in class_nodes.items():
        f = class_to_file.get(cls_id, '')
        file_to_classes[f].append(cls)

    file_imports: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        if e['type'] == 'DEPENDS_ON' and e['source'] in file_nodes:
            src_label = file_nodes[e['source']]['label']
            tgt_label = module_nodes.get(e['target'], {}).get('label', '')
            if tgt_label:
                file_imports[src_label].append({
                    'local_name': tgt_label,
                    'original_name': tgt_label,
                    'source': tgt_label,
                    'resolved_file': None,
                    'resolved': False,
                })

    per_file: dict[str, dict] = {}
    for pf in pr_files:
        per_file[pf] = {
            'imports': file_imports.get(pf, []),
            'internal_calls': [],
            'incoming_calls': [],
            'outgoing_calls': [],
        }

    edge_map_internal: dict = {}
    edge_map_incoming: dict = {}
    edge_map_outgoing: dict = {}

    for e in edges:
        if e['type'] != 'CALLS':
            continue
        src_fn_id = e['source']
        tgt_fn_id = e['target']

        src_file = func_to_file.get(src_fn_id, '')
        tgt_file = func_to_file.get(tgt_fn_id, '')

        if not src_file or not tgt_file:
            continue

        src_fn = func_nodes.get(src_fn_id, {})
        tgt_fn = func_nodes.get(tgt_fn_id, {})

        src_in_pr = src_file in pr_set
        tgt_in_pr = tgt_file in pr_set

        if src_in_pr and tgt_in_pr:
            key = (src_file, src_fn.get('label', ''), tgt_fn.get('label', ''), tgt_file)
            if key not in edge_map_internal:
                edge_map_internal[key] = {
                    'caller_file': src_file,
                    'caller_function': src_fn.get('label', ''),
                    'caller_scope': src_fn.get('label', ''),
                    'caller_line': src_fn.get('lineStart', 0),
                    'callee': tgt_fn.get('label', ''),
                    'callee_qualified': tgt_fn.get('label', ''),
                    'callee_file': tgt_file,
                    'callee_line': tgt_fn.get('lineStart', 0),
                    'resolution': 'graph_edge',
                    'call_sites': [{'line': tgt_fn.get('lineStart', 0), 'call': f"{src_fn.get('label', '')} -> {tgt_fn.get('label', '')}"}],
                    'frequency': 1,
                }

        elif not src_in_pr and tgt_in_pr:
            key = (src_file, src_fn.get('label', ''), tgt_fn.get('label', ''), tgt_file)
            if key not in edge_map_incoming:
                edge_map_incoming[key] = {
                    'caller_file': src_file,
                    'caller_function': src_fn.get('label', ''),
                    'caller_scope': src_fn.get('label', ''),
                    'caller_line': src_fn.get('lineStart', 0),
                    'callee': tgt_fn.get('label', ''),
                    'callee_qualified': tgt_fn.get('label', ''),
                    'callee_file': tgt_file,
                    'callee_line': tgt_fn.get('lineStart', 0),
                    'resolution': 'graph_edge',
                    'call_sites': [{'line': tgt_fn.get('lineStart', 0), 'call': f"{src_fn.get('label', '')} -> {tgt_fn.get('label', '')}"}],
                    'frequency': 1,
                }

        elif src_in_pr and not tgt_in_pr:
            key = (src_file, src_fn.get('label', ''), tgt_fn.get('label', ''), tgt_file)
            if key not in edge_map_outgoing:
                edge_map_outgoing[key] = {
                    'caller_file': src_file,
                    'caller_function': src_fn.get('label', ''),
                    'caller_scope': src_fn.get('label', ''),
                    'caller_line': src_fn.get('lineStart', 0),
                    'callee': tgt_fn.get('label', ''),
                    'callee_qualified': tgt_fn.get('label', ''),
                    'callee_file': tgt_file,
                    'callee_line': tgt_fn.get('lineStart', 0),
                    'resolution': 'graph_edge',
                    'call_sites': [{'line': tgt_fn.get('lineStart', 0), 'call': f"{src_fn.get('label', '')} -> {tgt_fn.get('label', '')}"}],
                    'frequency': 1,
                }

    for edge in edge_map_internal.values():
        cf = edge['caller_file']
        if cf in per_file:
            per_file[cf]['internal_calls'].append(edge)

    for edge in edge_map_incoming.values():
        cf = edge['callee_file']
        if cf in per_file:
            per_file[cf]['incoming_calls'].append(edge)

    for edge in edge_map_outgoing.values():
        cf = edge['caller_file']
        if cf in per_file:
            per_file[cf]['outgoing_calls'].append(edge)

    total_internal = sum(len(v['internal_calls']) for v in per_file.values())
    total_incoming = sum(len(v['incoming_calls']) for v in per_file.values())
    total_outgoing = sum(len(v['outgoing_calls']) for v in per_file.values())

    return {
        'pr_files': sorted(pr_set),
        'total_pr_files': len(pr_set),
        'per_file': per_file,
        'summary': {
            'internal_calls': total_internal,
            'incoming_calls': total_incoming,
            'outgoing_calls': total_outgoing,
        },
    }
