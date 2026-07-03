from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from langchain_core.language_models import BaseChatModel

from ncodereview.llm import load_chat_model
from ncodereview.schemas import SubagentReviewIssue

logger = logging.getLogger(__name__)


Classification = Literal["valid", "nitpick", "outside-diff", "false"]


JUDGE_READ_RADIUS = int(os.getenv("JUDGE_READ_RADIUS", "15"))
JUDGE_READ_MAX = int(os.getenv("JUDGE_READ_MAX", "60"))
NITPICK_CONFIDENCE_FLOOR = int(os.getenv("NITPICK_CONFIDENCE_FLOOR", "7"))
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "MiniMax-M2.7")


JUDGE_PROMPT = """You are a code review judge. Verify the subagent finding against actual code.


## Subagent's Claim
File: {file}
Lines: {line_start}-{line_end}
Severity: {severity} | Confidence: {confidence}/10 | Category: {category}
Title: {title}
Description: {description}
Suggestion: {suggestion}
Impact: {impact}

## Actual Code (window around cited lines, 1-indexed)
```
{code_window}
```

## Task
Output JSON only with this exact shape:
{{
  "classification": "valid" | "nitpick" | "outside-diff" | "false",
  "resolved_line_start": <int or {line_start}>,
  "resolved_line_end": <int or {line_end}>,
  "drop_reason": "<one short sentence or empty>"
}}

Classify as:
- **valid** — cited code matches the description; issue is real and worth a fix.
- **nitpick** — cited code matches, but the issue is cosmetic or trivial.
- **outside-diff** — cited lines don't exist or are unrelated to the claim.
- **false** — the claim directly contradicts the actual code shown.

Only set `drop_reason` when classification is `false`.
Only adjust resolved_line_* when the cited line number is off; otherwise echo the input.
"""


def bounded_read(
    content: str,
    center_line: int,
    radius: int | None = None,
    max_lines: int | None = None,
) -> str:
    """Return a numbered code window centered on `center_line` (1-indexed).

    Capped at `max_lines` (default 60). If the natural window exceeds the cap,
    take the last `max_lines` — recent context is usually more relevant.
    """
    radius = radius if radius is not None else JUDGE_READ_RADIUS
    max_lines = max_lines if max_lines is not None else JUDGE_READ_MAX

    lines = content.splitlines()
    if center_line < 1:
        center_line = 1

    start_idx = max(0, center_line - radius - 1)
    end_idx = min(len(lines), center_line + radius)
    window_lines = lines[start_idx:end_idx]

    if len(window_lines) > max_lines:
        half = max_lines // 2
        center_offset = (center_line - 1) - start_idx
        start = max(0, center_offset - half)
        window_lines = window_lines[start : start + max_lines]

    return "\n".join(f"{start_idx + i + 1:>4}: {line}" for i, line in enumerate(window_lines))


def _parse_json_block(text: str) -> dict:
    """Extract first JSON object from a model response. Tolerates fences."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _to_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


async def _ask_judge(
    raw: SubagentReviewIssue,
    window: str,
    model: BaseChatModel,
) -> dict:
    """Call judge model for one finding. Returns dict with classification + lines."""
    line_end = raw.line_end if raw.line_end is not None else raw.line_start
    prompt = JUDGE_PROMPT.format(
        file=raw.file,
        line_start=raw.line_start,
        line_end=line_end,
        severity=raw.severity,
        confidence=raw.confidence,
        category=raw.category,
        title=raw.title,
        description=raw.description,
        suggestion=raw.suggestion,
        impact=raw.impact,
        code_window=window or "(empty file or window out of range)",
    )

    response = await model.ainvoke(prompt)
    content = getattr(response, "content", None) or str(response)
    parsed = _parse_json_block(content)

    classification = parsed.get("classification", "valid")
    if classification not in ("valid", "nitpick", "outside-diff", "false"):
        logger.warning(
            "Judge returned unknown classification %r — keeping as 'valid'", classification
        )
        classification = "valid"

    return {
        "classification": classification,
        "resolved_line_start": _to_int(parsed.get("resolved_line_start")) or raw.line_start,
        "resolved_line_end": _to_int(parsed.get("resolved_line_end")) or line_end,
        "drop_reason": parsed.get("drop_reason") or None,
    }


def _make_annotated(
    raw: SubagentReviewIssue,
    *,
    classification: Classification = "valid",
    resolved_line_start: int | None = None,
    resolved_line_end: int | None = None,
    drop_reason: str | None = None,
) -> dict:
    return {
        "file": raw.file,
        "line_start": raw.line_start,
        "line_end": raw.line_end,
        "issue_type": raw.issue_type,
        "category": raw.category,
        "severity": raw.severity,
        "title": raw.title,
        "description": raw.description,
        "suggestion": raw.suggestion,
        "impact": raw.impact,
        "code_snippet": raw.code_snippet,
        "confidence": raw.confidence,
        "classification": classification,
        "drop_reason": drop_reason,
        "resolved_line_start": resolved_line_start or raw.line_start,
        "resolved_line_end": resolved_line_end or (raw.line_end or raw.line_start),
    }


async def judge_findings(
    raw_findings: list[SubagentReviewIssue],
    pr_files: dict[str, str],
    model: BaseChatModel | None = None,
) -> list[dict]:
    """Verify + classify each finding. Same length as input; never silently drops.

    `pr_files`: path → file content at PR HEAD (post-diff).
    Findings whose file is missing are flagged `outside-diff` so the summary
    can still mention them rather than disappearing.
    """
    if model is None:
        model = load_chat_model(JUDGE_MODEL)

    annotated: list[dict] = []
    for raw in raw_findings:
        file_content = pr_files.get(raw.file)
        if not file_content:
            logger.info("Judge: file %s not in pr_files (rendered as outside-diff)", raw.file)
            annotated.append(
                _make_annotated(
                    raw,
                    classification="outside-diff",
                    drop_reason="file not in PR files",
                )
            )
            continue

        window = bounded_read(file_content, raw.line_start)

        try:
            verdict = await _ask_judge(raw, window, model)
        except Exception as exc:
            # ponytail: fail-open at the LLM level — judge down is not a
            # reason to drop a real finding. A rule-based pass downstream
            # still filters it. Worst case: noise reaches the composer.
            logger.warning(
                "Judge LLM failed on %s:%d — keeping as valid: %s",
                raw.file,
                raw.line_start,
                exc,
            )
            verdict = {
                "classification": "valid",
                "resolved_line_start": raw.line_start,
                "resolved_line_end": raw.line_end or raw.line_start,
                "drop_reason": None,
            }

        annotated.append(_make_annotated(raw, **verdict))  # ty:ignore[invalid-argument-type]

    return annotated


def summarize_judgment(annotated: list[dict]) -> dict[str, int]:
    """Bucket counts for the PR header."""
    counts = {"valid": 0, "nitpick": 0, "outside-diff": 0, "false": 0}
    for f in annotated:
        counts[f.get("classification", "valid")] += 1
    return counts


def partition_findings(annotated: list[dict]) -> dict[str, list[dict]]:
    """Return {valid: [...], nitpick: [...], outside-diff: [...], false: [...]}."""
    out: dict[str, list[dict]] = {"valid": [], "nitpick": [], "outside-diff": [], "false": []}
    for f in annotated:
        out[f.get("classification", "valid")].append(f)
    return out


__all__ = [
    "Classification",
    "bounded_read",
    "judge_findings",
    "partition_findings",
    "summarize_judgment",
]


if __name__ == "__main__":
    sample = """
1: package middleware
2:
3: import (
4:     "net/http"
5:     "os"
6:     "strings"
7: )
8:
9: var allowedOrigins = map[string]bool{
10:    "http://localhost":     true,
11:    "http://localhost:3000": true,
12: }
13:
14: func init() {
15:    if v := os.Getenv("ALLOWED_ORIGINS"); v != "" {
16:        for _, origin := range strings.Split(v, ",") {
17:            allowedOrigins[strings.TrimSpace(origin)] = true
18:        }
19:    }
20: }
21:
22: func CORSMiddlewareGin() gin.HandlerFunc {
23:    return func(c *gin.Context) {
24:        origin := c.GetHeader("Origin")
25:        if origin != "" && allowedOrigins[origin] {
26:            c.Header("Access-Control-Allow-Origin", origin)
27:            c.Header("Access-Control-Allow-Credentials", "true")
28:        } else if origin == "" {
29:            c.Header("Access-Control-Allow-Origin", "*")
30:        }
31:        c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
32:    }
33: }
""".strip()

    print(bounded_read(sample, center_line=24, radius=3, max_lines=20))
