from collections import defaultdict
from typing import Dict, List


def _make_file_id(path: str) -> str:
    return "file_" + path.replace('/', '_').replace('.', '_').replace('-', '_')


def _short_label(label: str, max_len: int = 24) -> str:
    short = label.split('/')[-1] if '/' in label else label
    return short[:max_len] + '…' if len(short) > max_len else short


def build_graph(repo_path: str, files: list, parsed: list) -> dict:
    nodes: Dict[str, dict] = {}
    edges: List[dict] = []
    edge_set: set = set()

    def add_node(node_id: str, node_type: str, label: str, **kwargs):
        nodes[node_id] = {
            'id': node_id,
            'type': node_type,
            'label': label,
            'shortLabel': _short_label(label),
            **kwargs,
        }

    def add_edge(from_id: str, to_id: str, edge_type: str):
        if from_id not in nodes or to_id not in nodes:
            return
        if from_id == to_id:
            return
        key = (from_id, to_id, edge_type)
        if key in edge_set:
            return
        edge_set.add(key)
        edges.append({
            'id': f"e_{from_id[:20]}_{to_id[:20]}_{edge_type}",
            'source': from_id,
            'target': to_id,
            'type': edge_type,
        })

    class_by_name: Dict[str, str] = {}
    module_ids: Dict[str, str] = {}

    for file_info in files:
        fid = _make_file_id(file_info['path'])
        add_node(fid, 'File', file_info['path'],
                 language=file_info['language'],
                 lines=file_info['lines'])

    for file_info, parse_result in zip(files, parsed):
        fid = _make_file_id(file_info['path'])
        for cls in parse_result['classes']:
            cid = f"cls_{cls['name']}_{fid}"[:80]
            if cid not in nodes:
                add_node(cid, 'Class', cls['name'],
                         file=file_info['path'],
                         language=file_info['language'],
                         lineStart=cls['line_start'],
                         lineEnd=cls['line_end'])
                class_by_name[cls['name']] = cid
            add_edge(cid, fid, 'DEFINED_IN')

    for file_info, parse_result in zip(files, parsed):
        for cls in parse_result['classes']:
            fid = _make_file_id(file_info['path'])
            cid = f"cls_{cls['name']}_{fid}"[:80]
            for parent in cls.get('parents', []):
                if parent in class_by_name:
                    add_edge(cid, class_by_name[parent], 'INHERITS_FROM')

    # Key functions by (file_path, name) to avoid name collisions
    # Also build a reverse map: qualified_call -> list of matching fn_ids
    func_by_file_name: dict[tuple[str, str], str] = {}
    func_by_simple_name: dict[str, list[str]] = defaultdict(list)

    for file_info, parse_result in zip(files, parsed):
        fid = _make_file_id(file_info['path'])
        for fn in parse_result['functions']:
            fn_id = f"fn_{fn['name']}_{fid}"[:80]
            if fn_id not in nodes:
                add_node(fn_id, 'Function', fn['name'],
                         file=file_info['path'],
                         language=file_info['language'],
                         lineStart=fn['line_start'],
                         lineEnd=fn['line_end'],
                         sourcePreview=fn.get('preview', ''))
                func_by_file_name[(file_info['path'], fn['name'])] = fn_id
                func_by_simple_name[fn['name']].append(fn_id)
            add_edge(fn_id, fid, 'DEFINED_IN')
            if fn.get('parent_class') and fn['parent_class'] in class_by_name:
                add_edge(fn_id, class_by_name[fn['parent_class']], 'BELONGS_TO')

    all_imports: set = set()
    for parse_result in parsed:
        all_imports.update(i for i in parse_result.get('imports', []) if i)

    for mod_name in all_imports:
        mid = f"mod_{mod_name[:40]}"
        if mid not in nodes:
            add_node(mid, 'Module', mod_name, purpose='external dependency')
        module_ids[mod_name] = mid

    # Build CALLS edges: resolve each call to its target function node
    for file_info, parse_result in zip(files, parsed):
        fid = _make_file_id(file_info['path'])
        for fn in parse_result['functions']:
            fn_id = f"fn_{fn['name']}_{fid}"[:80]
            for call in fn.get('calls', []):
                target_ids = _resolve_call_target(call, file_info['path'], func_by_file_name, func_by_simple_name)
                for tid in target_ids:
                    add_edge(fn_id, tid, 'CALLS')

    for file_info, parse_result in zip(files, parsed):
        fid = _make_file_id(file_info['path'])
        for imp in parse_result.get('imports', []):
            if imp and imp in module_ids:
                add_edge(fid, module_ids[imp], 'DEPENDS_ON')

    return {
        'nodes': list(nodes.values()),
        'edges': edges,
    }


def _resolve_call_target(
    call: str,
    caller_file: str,
    func_by_file_name: dict[tuple[str, str], str],
    func_by_simple_name: dict[str, list[str]],
) -> list[str]:
    parts = call.split('.')
    name = parts[-1]

    # Exact match in same file (bare function or last segment)
    key = (caller_file, call)
    if key in func_by_file_name:
        return [func_by_file_name[key]]

    if len(parts) > 1:
        # Qualified call like obj.method -> look for 'method' in same file
        key = (caller_file, name)
        if key in func_by_file_name:
            return [func_by_file_name[key]]
        # Try as a bare name lookup (naked method name)
        if name in func_by_simple_name:
            return func_by_simple_name[name]

    # Bare function name
    if call in func_by_simple_name:
        return func_by_simple_name[call]

    # Fallback: try the last segment as the function name
    if name in func_by_simple_name:
        return func_by_simple_name[name]

    return []
