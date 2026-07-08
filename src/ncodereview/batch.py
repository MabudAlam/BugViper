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
    """Check if a file is low-signal (config, docs, tests) and should be deprioritized.

    Low-signal files still get reviewed but are placed in the last batch.
    """
    path_lower = file_path.lower()
    # Fast path — check substrings first
    for sub in LOW_SIGNAL_SUBSTRINGS:
        if sub in path_lower:
            return True
    # Check full patterns with glob-like matching
    for pattern in LOW_SIGNAL_PATTERNS:
        if pattern in LOW_SIGNAL_SUBSTRINGS:
            continue  # already checked above
        pattern_lower = pattern.lower()
        if pattern_lower.endswith("/"):
            # Directory prefix match
            if path_lower.startswith(pattern_lower) or f"/{pattern_lower}" in path_lower:
                return True
        elif pattern_lower.startswith("*") and pattern_lower.endswith("*"):
            # Contains substring (e.g. *.spec.*)
            core = pattern_lower.strip("*")
            if core in path_lower:
                return True
        elif pattern_lower.startswith("*"):
            # Suffix match (e.g. *.test.py)
            suffix = pattern_lower.lstrip("*")
            if path_lower.endswith(suffix):
                return True
        elif pattern_lower.endswith("*"):
            # Prefix match (e.g. test/)
            prefix = pattern_lower.rstrip("*")
            if path_lower.startswith(prefix):
                return True
        else:
            # Exact match or ends-with match
            if path_lower == pattern_lower:
                return True
            if path_lower.endswith(pattern_lower):
                return True
            if path_lower.endswith(f"/{pattern_lower}"):
                return True
    return False


def _score_file(file_path: str, call_graph: dict) -> float:
    """Score a file's review priority based on call graph connectivity.

    Files with more incoming calls (callers) are higher priority since
    changes to them have wider blast radius. Internal calls and outgoing
    calls are weighted lower.
    """
    per_file = call_graph.get("per_file", {})
    data = per_file.get(file_path, {})
    incoming = len(data.get("incoming_calls", []))  # who calls this file
    internal = len(data.get("internal_calls", []))  # calls within this file
    outgoing = len(data.get("outgoing_calls", []))  # what this file calls
    imports = len(data.get("imports", []))
    return incoming * 3.0 + internal * 2.0 + outgoing * 1.0 + imports * 0.5


def _assign_tier(score: float, max_score: float) -> str:
    """Assign a priority tier based on relative score."""
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
    """Estimate token consumption per file from diff patch size.

    Used to cap batch sizes so we don't exceed the model's context window.
    Rough estimate: 4 chars ≈ 1 token.
    """
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
    """Split PR files into review batches ordered by priority and call-graph locality.

    Files are:
    1. Separated into high-signal (code) and low-signal (config/docs/tests)
    2. Scored by call-graph connectivity (incoming calls = highest weight)
    3. Tiered into critical → warm → optional → low-signal
    4. Clustered by call-graph edges (files that call each other stay together)
    5. Packed into token-capped batches

    Returns a single batch if ≤ 10 files.
    """
    if len(pr_files) <= BATCH_THRESHOLD:
        return [pr_files]

    # Split into high/low signal
    high_signal: list[str] = []
    low_signal: list[str] = []
    for f in pr_files:
        (low_signal if _is_low_signal(f) else high_signal).append(f)

    # Score and tier high-signal files
    scored = [(f, _score_file(f, call_graph)) for f in high_signal]
    max_score = max((s for _, s in scored), default=0)
    tiered: dict[str, list[str]] = {"critical": [], "warm": [], "optional": []}
    for f, s in scored:
        tiered[_assign_tier(s, max_score)].append(f)

    # Concatenate tiers: critical first, low-signal last
    sorted_files = tiered["critical"] + tiered["warm"] + tiered["optional"] + low_signal
    token_budget = _estimate_token_budget(diff_text, sorted_files) if diff_text else {}

    # Group related files together, then pack into token-limited batches
    clusters = _build_file_clusters(call_graph, sorted_files)
    return _pack_clusters_into_batches(clusters, token_budget)


def _build_file_clusters(call_graph: dict, pr_files: list[str]) -> list[set[str]]:
    """Group files that call each other into clusters using call-graph edges.

    Files within the same cluster are kept together in the same batch so the
    reviewer can trace cross-file call chains without switching batches.
    """
    G = nx.Graph()
    for pf in pr_files:
        G.add_node(pf)

    # Connect files that have internal call edges between them
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
    """Pack file clusters into batches capped by MAX_BATCH_TOKENS.

    Each cluster is kept intact (files that call each other stay together).
    If a single cluster exceeds the cap, it still goes in one batch — the
    estimate is approximate and reviewers can handle oversize batches.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for cluster in clusters:
        cluster_list = sorted(cluster)
        cluster_tokens = sum(token_budget.get(f, 5000) for f in cluster_list)

        # Start a new batch if this cluster would push us over the limit
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
    """Filter blast radius markdown to only include sections for the given files.

    The blast radius document has sections starting with '## <file_path>'.
    This extracts only the sections relevant to files in this batch so each
    batch only sees its own impact analysis.
    """
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
