"""Diff analysis utilities for code review."""

from __future__ import annotations

import re

from common.diff_parser import split_diff_by_file


def get_changed_files(diff_text: str) -> list[str]:
    return list(split_diff_by_file(diff_text).keys())


def get_changed_line_ranges(diff_text: str) -> dict[str, list[dict[str, int]]]:
    ranges: dict[str, list[dict[str, int]]] = {}
    for file_path, patch in split_diff_by_file(diff_text).items():
        merged = extract_added_line_ranges(patch) + extract_hunk_ranges(patch)
        ranges[file_path] = [{"start": s, "end": e} for s, e in merged]
    return ranges


def extract_added_line_ranges(patch: str | None) -> list[tuple[int, int]]:
    if not patch:
        return []
    hunk_header = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    ranges: list[tuple[int, int]] = []
    right_line = 0
    current_start: int | None = None
    current_end: int | None = None

    def flush() -> None:
        nonlocal current_start, current_end
        if current_start is not None and current_end is not None:
            ranges.append((current_start, current_end))
        current_start = None
        current_end = None

    for line in patch.splitlines():
        match = hunk_header.match(line)
        if match:
            flush()
            right_line = int(match.group(1))
            continue
        if right_line == 0 or line.startswith("\\"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_start is None:
                current_start = right_line
            current_end = right_line
            right_line += 1
            continue
        flush()
        if not line.startswith("-"):
            right_line += 1
    flush()
    return ranges


def extract_hunk_ranges(patch: str | None) -> list[tuple[int, int]]:
    if not patch:
        return []
    hunk_header = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    ranges: list[tuple[int, int]] = []
    for line in patch.splitlines():
        match = hunk_header.match(line)
        if not match:
            continue
        new_start = int(match.group(1))
        new_len = int(match.group(2)) if match.group(2) else 1
        ranges.append((new_start, new_start + max(new_len - 1, 0)))
    return ranges


def overlaps_added_lines(
    line_start: int | None,
    line_end: int | None,
    ranges: list[dict[str, int]],
) -> bool:
    if line_start is None or not ranges:
        return False
    end = line_end or line_start
    for item in ranges:
        if line_start <= item["end"] and end >= item["start"]:
            return True
    return False


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and a_end >= b_start
