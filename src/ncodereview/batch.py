"""File batching logic: context-aware approach with adaptive-fit profile.

Adds:
- Context window resolution + adaptive-fit profile selection
- Prompt token estimation with overhead
- Chunk budget computation (budget = window * ratio - overhead)
- Low-signal filtering, call-graph scoring, priority tiering
- Call-graph clustering + token-capped batch packing
"""

from __future__ import annotations

import networkx as nx

from common.diff_parser import split_diff_by_file

# Constants for context-aware batching
PROMPT_BUDGET_RATIO = 0.8  # use 80% of context window for prompts
OVERHEAD_ESTIMATE_TOKENS = 4000  # static overhead: system prompt + tool schemas

# Context window thresholds for adaptive-fit
CONTEXT_WINDOW_FULL = 64000   # >=64K → full profile
CONTEXT_WINDOW_COMPACT = 32000  # 32-64K → compact profile

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


def resolve_adaptive_profile(context_window: int) -> dict:
    """Resolve adaptive-fit profile based on context window.

    Returns a dict with keys:
      kind: 'full' | 'compact' | 'minimal'
      dropCallGraph: bool — skip call graph when context is tight
      skipHeavyPasses: bool — skip synthesis/recall passes
      compactPrompt: bool — use compact prompt variants
      allOptional: bool — render hunk-headers only (minimal)
      maxDiffChars: int | None — cap per-file diff characters
      lowSignalFilterUnconditional: bool — always filter low-signal files
    """
    if context_window >= CONTEXT_WINDOW_FULL:
        return {
            'kind': 'full',
            'dropCallGraph': False,
            'skipHeavyPasses': False,
            'compactPrompt': False,
            'allOptional': False,
            'maxDiffChars': None,
            'lowSignalFilterUnconditional': False,
        }

    if context_window >= CONTEXT_WINDOW_COMPACT:
        return {
            'kind': 'compact',
            'dropCallGraph': True,
            'skipHeavyPasses': True,
            'compactPrompt': True,
            'allOptional': False,
            'maxDiffChars': 8000,
            'lowSignalFilterUnconditional': True,
        }

    # minimal profile (< 32K)
    return {
        'kind': 'minimal',
        'dropCallGraph': True,
        'skipHeavyPasses': True,
        'compactPrompt': True,
        'allOptional': True,
        'maxDiffChars': 4000,
        'lowSignalFilterUnconditional': True,
    }


def estimate_non_diff_overhead_tokens(files_count: int = 1) -> int:
    """Estimate static prompt overhead (system prompt + tool schemas + non-diff parts)."""
    return OVERHEAD_ESTIMATE_TOKENS + files_count * 50  # ~50 tokens per file listing


def estimate_diff_tokens(diff_text: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    return len(diff_text) // 4


def estimate_prompt_tokens(diff_text: str, files_count: int) -> int:
    """Estimate total prompt tokens = diff tokens + overhead."""
    return estimate_diff_tokens(diff_text) + estimate_non_diff_overhead_tokens(files_count)


def compute_chunk_budget(context_window: int, overhead_tokens: int) -> int:
    """Compute per-chunk diff budget.

    budget = context_window * PROMPT_BUDGET_RATIO - overhead
    floored at 30% of context window.
    """
    prompt_budget = int(context_window * PROMPT_BUDGET_RATIO)
    chunk_budget = max(prompt_budget - overhead_tokens, int(context_window * 0.3))
    return chunk_budget


def _is_low_signal(file_path: str) -> bool:
    """Check if a file is low-signal (config, docs, tests)."""
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
    """Score a file's review priority based on call graph connectivity.

    Weights: incoming calls = 3x, internal = 2x, outgoing = 1x, imports = 0.5x
    """
    per_file = call_graph.get("per_file", {})
    data = per_file.get(file_path, {})
    incoming = len(data.get("incoming_calls", []))
    internal = len(data.get("internal_calls", []))
    outgoing = len(data.get("outgoing_calls", []))
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


def apply_large_pr_aggressive_filter(pr_files: list[str]) -> list[str]:
    """Drop low-signal files when context window is tight."""
    return [f for f in pr_files if not _is_low_signal(f)]


def batch_pr_files(
    call_graph: dict,
    pr_files: list[str],
    diff_text: str = "",
    context_window: int = 128000,
    review_mode: str = 'normal',
) -> list[list[str]]:
    """Split PR files into review batches with context-window awareness.

    Adds context-window awareness, adaptive-fit profile, and overhead-aware chunk budget.

    1. Low-signal filter (when profile says so)
    2. Score + tier by call-graph connectivity
    3. Cluster by call-graph edges (files that call each other stay together)
    4. Pack into token-capped batches using computed chunk budget
    """
    if len(pr_files) <= BATCH_THRESHOLD:
        return [pr_files]

    profile = resolve_adaptive_profile(context_window)

    # Apply aggressive filter for tight windows
    if profile['lowSignalFilterUnconditional']:
        pr_files = apply_large_pr_aggressive_filter(pr_files)
        if len(pr_files) <= BATCH_THRESHOLD:
            return [pr_files]

    # Split into high/low signal for tiering
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

    # Compute chunk budget from context window
    overhead = estimate_non_diff_overhead_tokens(len(sorted_files))
    chunk_diff_budget = compute_chunk_budget(context_window, overhead)

    # Group related files together, then pack into token-limited batches
    clusters = _build_file_clusters(call_graph, sorted_files)
    return _pack_clusters_into_batches(clusters, token_budget, max_budget=chunk_diff_budget)


def _estimate_token_budget(diff_text: str, file_list: list[str]) -> dict[str, int]:
    """Estimate token consumption per file from diff patch size. 4 chars ≈ 1 token."""
    patches = split_diff_by_file(diff_text)
    budget: dict[str, int] = {}
    for f in file_list:
        patch = patches.get(f, "")
        budget[f] = len(patch) // 4
    return budget


def _build_file_clusters(call_graph: dict, pr_files: list[str]) -> list[set[str]]:
    """Group files that call each other into clusters using call-graph edges."""
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
    file_index = {f: i for i, f in enumerate(pr_files)}
    return sorted(clusters, key=lambda c: min(file_index.get(f, 0) for f in c))


def _pack_clusters_into_batches(
    clusters: list[set[str]],
    token_budget: dict[str, int],
    max_budget: int = 60000,
    max_cluster_budget: int = 120000,
) -> list[list[str]]:
    """Pack file clusters into batches capped by token budget.

    Each cluster is kept intact (files that call each other stay together).
    If a single cluster exceeds the cap, it still goes in one batch.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    effective_max = min(max_budget, max_cluster_budget)

    for cluster in clusters:
        cluster_list = sorted(cluster)
        cluster_tokens = sum(token_budget.get(f, 5000) for f in cluster_list)

        if current_tokens + cluster_tokens > effective_max and current:
            batches.append(current)
            current = []
            current_tokens = 0

        current.extend(cluster_list)
        current_tokens += cluster_tokens

    if current:
        batches.append(current)

    return batches


def filter_blast_radius_for_files(blast_radius_md: str, batch_files: list[str]) -> str:
    """Filter blast radius markdown to only include sections for the given files."""
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
