"""Unified-diff utilities — per-file patches, valid line ranges, snapping.

Ported from kodus-ai's approach (libs/code-review/pipeline/stages/agent-review.stage.ts).
Operates on a SINGLE file's patch at a time, not the full multi-file diff — this
is simpler and matches how the agent/review flow naturally splits work by file.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")


def split_diff_by_file(diff_text: str) -> Dict[str, str]:
    """Split a full unified diff into per-file patches keyed by file path.

    Each value is the raw per-file patch (including the ``diff --git`` header
    and all hunks for that file) — ready to pass to ``extract_valid_diff_lines``
    without further preprocessing.
    """
    files: Dict[str, List[str]] = {}
    current_file: str | None = None
    current_lines: List[str] = []

    for line in diff_text.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            if current_file is not None and current_lines:
                files[current_file] = "\n".join(current_lines)
            current_file = m.group(1)
            current_lines = [line]
            continue
        if current_file is not None:
            current_lines.append(line)

    if current_file is not None and current_lines:
        files[current_file] = "\n".join(current_lines)

    return files


def extract_valid_diff_lines(patch: str | None) -> List[Tuple[int, int]]:
    """Return ranges of file line numbers valid for inline comments.

    Walks the patch hunk-by-hunk and tracks the new-file (RIGHT) line number
    for context (`` ``) and added (``+``) lines.  Deleted (``-``) lines do not
    exist on the right side of the diff, so they are excluded.  Each
    consecutive run of valid lines within a hunk becomes a ``(start, end)``
    tuple; gaps (e.g. between two hunks, or across a deleted block) split
    the range.

    Returns an empty list if the patch is empty or has no valid hunks.
    """
    if not patch:
        return []

    ranges: List[Tuple[int, int]] = []
    right_line = 0
    hunk_start = 0

    for line in patch.splitlines():
        m = _HUNK_HEADER.match(line)
        if m:
            if hunk_start > 0 and right_line > hunk_start:
                ranges.append((hunk_start, right_line - 1))
            right_line = int(m.group(1))
            hunk_start = right_line
            continue

        if hunk_start == 0:
            continue

        if not line:
            continue
        if line.startswith("-"):
            continue
        if line.startswith("\\"):
            continue

        right_line += 1

    if hunk_start > 0 and right_line > hunk_start:
        ranges.append((hunk_start, right_line - 1))

    return ranges


def snap_lines_to_diff(
    start: int | None,
    end: int | None,
    valid_ranges: List[Tuple[int, int]],
) -> Tuple[int, int] | None:
    """Snap a (start, end) line range to the nearest valid diff range.

    Strategy:
    1. If the suggestion overlaps any valid range, return the overlap
       (clipped to that range).
    2. If it doesn't overlap, find the closest range and clamp into it.
    3. If there are no valid ranges at all, return None.

    Never drops the suggestion — always returns something that can be
    commented on, given a non-empty ``valid_ranges``.
    """
    if not valid_ranges:
        return None

    if start is None or start < 1:
        rs, re = valid_ranges[0]
        return rs, min(re, rs + 5)

    end = end if end is not None else start

    best_overlap: Tuple[int, int] | None = None
    best_overlap_size = 0
    for rs, re in valid_ranges:
        if start <= re and end >= rs:
            overlap_start = max(start, rs)
            overlap_end = min(end, re)
            overlap_size = overlap_end - overlap_start
            if overlap_size > best_overlap_size:
                best_overlap_size = overlap_size
                best_overlap = (overlap_start, overlap_end)

    if best_overlap is not None:
        return best_overlap

    closest_range = valid_ranges[0]
    closest_dist: int | float = float("inf")
    for rs, re in valid_ranges:
        dist = min(abs(start - rs), abs(start - re))
        if dist < closest_dist:
            closest_dist = dist
            closest_range = (rs, re)

    rs, re = closest_range
    clamped_start = max(rs, min(start, re))
    clamped_end = min(re, max(clamped_start, end))
    return clamped_start, clamped_end


def calculate_comment_line(start: int, end: int | None, max_range: int = 15) -> int:
    """Return the end line for a GitHub inline comment.

    Caps the range at ``max_range`` lines (GitHub rejects larger multi-line
    ranges).  Collapses to start when ``start == end`` or when the range
    would exceed the cap.
    """
    if end is None or start == end:
        return start
    return end if start + max_range > end else start
