"""File batching: Louvain community detection on weighted call graph,
then pack communities into batches of 15-20 files."""

from __future__ import annotations

import networkx as nx
from networkx.algorithms.community import louvain_communities

MIN_BATCH_FILES = 15
MAX_BATCH_FILES = 8

LOW_SIGNAL_PATTERNS = (
    ".css", ".scss", ".less", ".md", ".mdx",
    ".json", ".yaml", ".yml", ".toml",
    ".config.js", ".config.ts",
    "package-lock.json", "package.json", "tsconfig.json",
    ".eslintrc", ".prettierrc",
    ".gitignore", ".dockerignore", ".claspignore",
    ".env", ".env.example",
    "Dockerfile", "docker-compose",
    "LICENSE", "CHANGELOG",
    "*.spec.*", "*.test.*", "*.e2e.*",
    "__tests__", "test/", "tests/",
    ".test.", ".spec.", ".e2e.",
)

LOW_SIGNAL_SUBSTRINGS = (
    "__tests__", "/test/", "/tests/",
    ".test.", ".spec.", ".e2e.",
)


def _is_low_signal(file_path: str) -> bool:
    path_lower = file_path.lower()
    for sub in LOW_SIGNAL_SUBSTRINGS:
        if sub in path_lower:
            return True
    for pattern in LOW_SIGNAL_PATTERNS:
        if pattern in LOW_SIGNAL_SUBSTRINGS:
            continue
        pattern_lower = pattern.lower()
        if pattern_lower.endswith("/"):
            if path_lower.startswith(pattern_lower) or f"/{pattern_lower}" in path_lower:
                return True
        elif pattern_lower.startswith("*") and pattern_lower.endswith("*"):
            core = pattern_lower.strip("*")
            if core in path_lower:
                return True
        elif pattern_lower.startswith("*"):
            suffix = pattern_lower.lstrip("*")
            if path_lower.endswith(suffix):
                return True
        elif pattern_lower.endswith("*"):
            prefix = pattern_lower.rstrip("*")
            if path_lower.startswith(prefix):
                return True
        else:
            if path_lower == pattern_lower:
                return True
            if path_lower.endswith(pattern_lower):
                return True
            if path_lower.endswith(f"/{pattern_lower}"):
                return True
    return False


def _score_file(file_path: str, call_graph: dict) -> float:
    per_file = call_graph.get("per_file", {})
    data = per_file.get(file_path, {})
    incoming = len(data.get("incoming_calls", []))
    internal = len(data.get("internal_calls", []))
    outgoing = len(data.get("outgoing_calls", []))
    imports = len(data.get("imports", []))
    return incoming * 3.0 + internal * 2.0 + outgoing * 1.0 + imports * 0.5


def _assign_tier(score: float, max_score: float) -> str:
    if max_score == 0:
        return "warm"
    ratio = score / max_score
    if ratio >= 0.6:
        return "critical"
    elif ratio >= 0.3:
        return "warm"
    return "optional"


MAX_BATCH_FILES_DEEP = 20


def batch_pr_files(call_graph: dict, pr_files: list[str], review_mode: str = 'normal') -> list[list[str]]:
    max_files = MAX_BATCH_FILES_DEEP if review_mode == 'deep' else MAX_BATCH_FILES

    if len(pr_files) <= max_files:
        return [pr_files]

    scores = {f: _score_file(f, call_graph) for f in pr_files}
    low_signal = [f for f in pr_files if _is_low_signal(f)]
    high_signal = [f for f in pr_files if not _is_low_signal(f)]

    if not high_signal:
        return [pr_files]

    # Build weighted call graph (edges = internal calls, weight = frequency)
    G = nx.Graph()
    G.add_nodes_from(high_signal)
    per_file = call_graph.get("per_file", {})
    for f in high_signal:
        for edge in per_file.get(f, {}).get("internal_calls", []):
            callee = edge.get("callee_file")
            if callee in high_signal:
                w = edge.get("frequency", 1)
                if G.has_edge(f, callee):
                    G[f][callee]["weight"] += w
                else:
                    G.add_edge(f, callee, weight=w)

    # Louvain communities on each connected component
    communities: list[set[str]] = []
    for comp in nx.connected_components(G):
        sub = G.subgraph(comp)
        try:
            comms = louvain_communities(sub, weight="weight", resolution=0.9, seed=42)
            communities.extend(comms)
        except Exception:
            communities.append(comp)

    # Sort communities by best score descending
    def community_score(c: set[str]) -> float:
        return max(scores.get(f, 0) for f in c)

    communities.sort(key=community_score, reverse=True)

    # Pack communities into batches of max_files
    batches: list[list[str]] = []
    current: list[str] = []

    for comm in communities:
        clist = sorted(comm)

        # Oversize community: split by score
        if len(clist) > max_files:
            clist.sort(key=lambda f: scores.get(f, 0), reverse=True)
            for i in range(0, len(clist), max_files):
                chunk = clist[i:i + max_files]
                if current:
                    batches.append(current)
                    current = []
                batches.append(chunk)
            continue

        # Normal case: pack into current batch if it fits
        if current and len(current) + len(clist) > max_files:
            batches.append(current)
            current = []

        current.extend(clist)

    if current:
        batches.append(current)

    # Distribute low-signal files across batches
    if low_signal:
        idx = 0
        for f in low_signal:
            while idx < len(batches) and len(batches[idx]) >= max_files:
                idx += 1
            if idx >= len(batches):
                batches.append([])
            batches[idx].append(f)

    return batches


def filter_blast_radius_for_files(blast_radius_md: str, batch_files: list[str]) -> str:
    batch_set = set(batch_files)
    lines = blast_radius_md.split("\n")
    filtered_lines: list[str] = []
    current_file: str | None = None
    in_relevant_section = False
    for line in lines:
        if line.startswith("## "):
            current_file = line[3:].strip()
            in_relevant_section = current_file in batch_set
        if in_relevant_section or current_file is None:
            filtered_lines.append(line)
    return "\n".join(filtered_lines)


def filter_call_graph_for_files(call_graph_json: str, batch_files: list[str]) -> str:
    import json
    cg = json.loads(call_graph_json)
    batch_set = set(batch_files)
    pr_files = cg.get("per_file", {})
    filtered = {k: v for k, v in pr_files.items() if k in batch_set}
    cg["per_file"] = filtered
    cg["pr_files"] = sorted(batch_set)
    cg["total_pr_files"] = len(batch_set)
    return json.dumps(cg, indent=2)
