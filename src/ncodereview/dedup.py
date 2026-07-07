from __future__ import annotations

import logging
import os
import re

from ncodereview.llm import load_chat_model

logger = logging.getLogger(__name__)

DEDUP_CONTENT_THRESHOLD = 0.3

DEDUP_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "description": (
                "Groups of duplicate findings. Each group has a "
                "representative and its duplicates."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "keep": {
                        "type": "integer",
                        "description": ("Index of the best finding to keep as representative"),
                    },
                    "duplicates": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Indices of duplicate findings "
                            "(same bug, same or different locations)"
                        ),
                    },
                },
                "required": ["keep", "duplicates"],
                "additionalProperties": False,
            },
        },
        "unique": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Indices of findings that have no duplicates",
        },
    },
    "required": ["groups", "unique"],
    "additionalProperties": False,
}


def _content_words(issue: dict) -> set[str]:
    text = " ".join(
        str(issue.get(k, "")) for k in ("title", "description", "suggestion", "code_snippet")
    )
    return set(re.findall(r"[a-z_][a-z0-9_]{2,}", text.lower()))


def content_similarity(a: dict, b: dict) -> float:
    A = _content_words(a)
    B = _content_words(b)
    if not A or not B:
        return 0.0
    inter = sum(1 for w in A if w in B)
    return inter / (len(A) + len(B) - inter)


def _build_dedup_summaries(issues: list[dict]) -> str:
    lines: list[str] = []
    for i, issue in enumerate(issues):
        file = issue.get("file", "?")
        ls = issue.get("line_start", "?")
        le = issue.get("line_end", ls)
        cat = issue.get("category", "?")
        sev = issue.get("severity", "?")
        title = (issue.get("title") or "")[:120]
        desc = (issue.get("description") or "")[:200]
        snippet = (issue.get("code_snippet") or "")[:100]
        lines.append(
            f"[{i}] {file}:{ls}-{le} [{cat}/{sev}]: {title}\n"
            f"    desc: {desc}\n"
            f"    code: {snippet}"
        )
    return "\n\n".join(lines)


def _build_dedup_prompt(issues: list[dict]) -> str:
    summaries = _build_dedup_summaries(issues)
    prompt = (
        f"You have {len(issues)} code review findings from a PR. "
        "Identify duplicates and group them.\n\n"
    )
    prompt += (
        "BE CONSERVATIVE — when in doubt, do NOT group. "
        "Only group when you are highly confident they describe "
        "the exact same bug.\n\n"
    )
    prompt += "There are TWO types of duplicates:\n\n"
    prompt += (
        "1. **EXACT DUPLICATES** (same bug, same location): "
        "Multiple findings pointing to the same file and overlapping lines "
        "describing the same issue. Keep the one with the most detail, "
        "discard the rest.\n\n"
    )
    prompt += (
        "2. **CROSS-LOCATION DUPLICATES** (same bug pattern, different "
        "locations): Findings describing the EXACT SAME code pattern/bug "
        'but in different files (e.g., "async callback in forEach" found '
        "in 3 files). These should be GROUPED — keep the best one, "
        "list the others as duplicates.\n\n"
    )
    prompt += "NOT duplicates (keep both):\n"
    prompt += "- Different bugs in the same file or nearby lines\n"
    prompt += "- Different root causes even if they sound similar\n"
    prompt += "- Findings about different code even if the description " "sounds similar\n\n"
    prompt += (
        "IGNORE the category label (bug/security/performance) when "
        "deciding - two reviewers can independently find the same issue.\n"
    )
    prompt += (
        "Prefer keeping the finding with the most detail or "
        "clearest description as the representative.\n\n"
    )
    prompt += f"{summaries}\n\n"
    prompt += (
        'Return a JSON object with two keys: "groups" '
        "(array of {keep: int, duplicates: [int]}) "
        'and "unique" (array of ints).'
    )
    return prompt


def deduplicate_issues(
    issues: list[dict],
    model_id: str | None = None,
) -> list[dict]:
    if len(issues) <= 1:
        return issues

    model_id = model_id or os.getenv("DEDUP_MODEL", "openai/gpt-4o-mini")
    model = load_chat_model(model_id)
    structured = model.with_structured_output(DEDUP_SCHEMA, method="json_mode", include_raw=False)

    prompt = _build_dedup_prompt(issues)
    try:
        result = structured.invoke(prompt)
    except Exception as exc:
        logger.warning(
            "Dedup LLM call failed, keeping all %d issues: %s",
            len(issues),
            exc,
        )
        return issues

    if isinstance(result, dict):
        groups = result.get("groups", [])
        unique = result.get("unique", [])
    else:
        groups = getattr(result, "groups", [])
        unique = getattr(result, "unique", [])

    if not groups and not unique:
        logger.warning("Dedup returned empty, keeping all %d issues", len(issues))
        return issues

    result_list: list[dict] = []
    added_indices: set[int] = set()
    classified_indices: set[int] = set()

    for idx in unique:
        if 0 <= idx < len(issues):
            result_list.append(issues[idx])
            added_indices.add(idx)
            classified_indices.add(idx)

    for group in groups:
        keep_idx = group.get("keep")
        dup_indices = group.get("duplicates", [])

        if not isinstance(keep_idx, int) or keep_idx < 0 or keep_idx >= len(issues):
            for dup_idx in dup_indices:
                classified_indices.add(dup_idx)
                if (
                    isinstance(dup_idx, int)
                    and 0 <= dup_idx < len(issues)
                    and dup_idx not in added_indices
                ):
                    added_indices.add(dup_idx)
                    result_list.append(issues[dup_idx])
            continue

        if keep_idx in added_indices:
            for dup_idx in dup_indices:
                classified_indices.add(dup_idx)
                other = issues[dup_idx] if 0 <= dup_idx < len(issues) else {}
                other_loc = f"{other.get('file', '')}:{other.get('line_start', '')}"
                my_loc = (
                    f"{issues[keep_idx].get('file', '')}"
                    f":{issues[keep_idx].get('line_start', '')}"
                )
                if other_loc != my_loc and other_loc != ":":
                    pos = list(added_indices).index(keep_idx)
                    existing = result_list[pos]
                    existing["description"] = (
                        existing.get("description", "") + f"\n\n**Also found in:** `{other_loc}`"
                    )
            continue

        kept = dict(issues[keep_idx])
        added_indices.add(keep_idx)
        classified_indices.add(keep_idx)
        other_locations: list[str] = []

        for dup_idx in dup_indices:
            if not isinstance(dup_idx, int) or dup_idx < 0 or dup_idx >= len(issues):
                continue

            if content_similarity(issues[dup_idx], issues[keep_idx]) < DEDUP_CONTENT_THRESHOLD:
                classified_indices.add(dup_idx)
                if dup_idx not in added_indices:
                    added_indices.add(dup_idx)
                    result_list.append(issues[dup_idx])
                continue

            classified_indices.add(dup_idx)
            dup = issues[dup_idx]
            loc = f"{dup.get('file', '')}:{dup.get('line_start', '')}"
            my_loc = f"{kept.get('file', '')}:{kept.get('line_start', '')}"
            if loc != my_loc and loc != ":":
                other_locations.append(loc)

        if other_locations:
            loc_items = "\n".join(f"- `{loc}`" for loc in other_locations)
            kept["description"] = (
                kept.get("description", "") + f"\n\n**Also found in:**\n{loc_items}"
            )

        result_list.append(kept)

    for i in range(len(issues)):
        if i not in classified_indices:
            result_list.append(issues[i])

    removed = len(issues) - len(result_list)
    if removed:
        logger.info(
            "Dedup: %d → %d (removed %d)",
            len(issues),
            len(result_list),
            removed,
        )

    return result_list
