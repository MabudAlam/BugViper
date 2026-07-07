from __future__ import annotations

import networkx as nx

from common.diff_parser import split_diff_by_file

BATCH_THRESHOLD = 10
MAX_BATCH_TOKENS = 60000

LOW_SIGNAL_PATTERNS = (
    ".css",
    ".scss",
    ".less",
    ".md",
    ".mdx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".config.js",
    ".config.ts",
    "package-lock.json",
    "package.json",
    "tsconfig.json",
    ".eslintrc",
    ".prettierrc",
    ".gitignore",
    ".dockerignore",
    ".claspignore",
    ".env",
    ".env.example",
    "Dockerfile",
    "docker-compose",
    "LICENSE",
    "CHANGELOG",
    "*.spec.*",
    "*.test.*",
    "*.e2e.*",
    "__tests__",
    "test/",
    "tests/",
    ".test.",
    ".spec.",
    ".e2e.",
)

LOW_SIGNAL_SUBSTRINGS = (
    "__tests__",
    "/test/",
    "/tests/",
    ".test.",
    ".spec.",
    ".e2e.",
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


def _estimate_token_budget(
    diff_text: str,
    file_list: list[str],
) -> dict[str, int]:
    patches = split_diff_by_file(diff_text)
    budget: dict[str, int] = {}
    for f in file_list:
        patch = patches.get(f, "")
        budget[f] = len(patch) // 4
    return budget


def batch_pr_files(
    call_graph: dict,
    pr_files: list[str],
    diff_text: str = "",
) -> list[list[str]]:
    if len(pr_files) <= BATCH_THRESHOLD:
        return [pr_files]

    high_signal: list[str] = []
    low_signal: list[str] = []
    for f in pr_files:
        (low_signal if _is_low_signal(f) else high_signal).append(f)

    scored = [(f, _score_file(f, call_graph)) for f in high_signal]
    max_score = max((s for _, s in scored), default=0)
    tiered: dict[str, list[str]] = {"critical": [], "warm": [], "optional": []}
    for f, s in scored:
        tiered[_assign_tier(s, max_score)].append(f)

    sorted_files = tiered["critical"] + tiered["warm"] + tiered["optional"] + low_signal
    token_budget = _estimate_token_budget(diff_text, sorted_files) if diff_text else {}

    clusters = _build_file_clusters(call_graph, sorted_files)
    return _pack_clusters_into_batches(clusters, token_budget)


def _build_file_clusters(call_graph: dict, pr_files: list[str]) -> list[set[str]]:
    G = nx.Graph()
    for pf in pr_files:
        G.add_node(pf)

    per_file = call_graph.get("per_file", {})
    for file_path, data in per_file.items():
        for edge in data.get("internal_calls", []):
            callee = edge.get("callee_file")
            if callee in pr_files:
                G.add_edge(file_path, callee)

    clusters = list(nx.connected_components(G))
    # Sort by position of the first file in pr_files (preserves priority order)
    file_index = {f: i for i, f in enumerate(pr_files)}
    return sorted(clusters, key=lambda c: min(file_index.get(f, 0) for f in c))


def _pack_clusters_into_batches(
    clusters: list[set[str]],
    token_budget: dict[str, int],
) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for cluster in clusters:
        cluster_list = sorted(cluster)
        cluster_tokens = sum(token_budget.get(f, 5000) for f in cluster_list)

        if current_tokens + cluster_tokens > MAX_BATCH_TOKENS and current:
            batches.append(current)
            current = []
            current_tokens = 0

        current.extend(cluster_list)
        current_tokens += cluster_tokens

    if current:
        batches.append(current)

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
