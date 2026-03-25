"""Diff line mapper for GitHub inline comments.

GitHub's inline comment API requires precise line numbers that exist in the diff.
This module creates a proper mapping from file line numbers to valid comment positions.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass
class DiffHunk:
    """Represents a single hunk in a unified diff."""

    file_path: str
    old_start: int  # Line number in old file
    old_count: int  # Number of lines in old file
    new_start: int  # Line number in new file
    new_count: int  # Number of lines in new file

    # Computed: lines that can be commented on (new file line numbers)
    commentable_lines: Set[int] = field(default_factory=set)


def parse_diff_positions(diff_text: str) -> Dict[str, List[DiffHunk]]:
    """Parse a unified diff and extract valid comment positions.

    For each hunk, compute which line numbers in the NEW file can receive
    inline comments. GitHub allows comments on:
    - Added lines (+)
    - Context lines (lines unchanged but shown in diff)

    NOT on:
    - Deleted lines (-)

    Returns:
        Dict mapping file_path -> list of DiffHunks with commentable_lines populated
    """
    result: Dict[str, List[DiffHunk]] = {}
    current_file = None
    current_hunk: DiffHunk | None = None
    current_new_line = 0  # Current line number in new file

    for line in diff_text.splitlines():
        # File header: +++ b/path/to/file.py
        file_match = re.match(r"^\+\+\+ b/(.+)$", line)
        if file_match:
            # Save any previous hunk before switching files
            if current_hunk and current_file:
                if current_file not in result:
                    result[current_file] = []
                result[current_file].append(current_hunk)
                current_hunk = None

            current_file = file_match.group(1)
            if current_file not in result:
                result[current_file] = []
            continue

        # Hunk header: @@ -a,b +c,d @@
        hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match and current_file:
            # Save previous hunk
            if current_hunk:
                result[current_file].append(current_hunk)

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            current_hunk = DiffHunk(
                file_path=current_file,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                commentable_lines=set(),
            )
            current_new_line = new_start
            continue

        # Skip if no active hunk
        if not current_hunk or not current_file:
            continue

        # Diff content lines
        if line.startswith("+"):
            # Added line - CAN comment on this
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1
        elif line.startswith("-"):
            # Deleted line - CANNOT comment (it's not in the new file)
            pass
        elif line.startswith(" "):
            # Context line - CAN comment (it's in both old and new)
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1
        elif line.startswith("\\"):
            # No newline marker - skip
            pass
        else:
            # Unexpected line in diff - might be a continuation
            # Treat as context (conservative)
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1

    # Save last hunk
    if current_hunk and current_file:
        result[current_file].append(current_hunk)

    return result


def get_valid_comment_lines(diff_text: str) -> Dict[str, Set[int]]:
    """Get all valid line numbers for inline comments per file.

    Args:
        diff_text: Full unified diff string

    Returns:
        Dict mapping file_path -> set of valid line numbers (new file)
    """
    hunks_by_file = parse_diff_positions(diff_text)
    return {
        file_path: {ln for hunk in hunks for ln in hunk.commentable_lines}
        for file_path, hunks in hunks_by_file.items()
    }


def find_nearest_valid_line(
    file_path: str,
    line: int,
    valid_lines: Dict[str, Set[int]],
    max_distance: int = 5,
) -> int | None:
    """Find the nearest valid comment line for a reported line number.

    If the LLM reports a line that's not in the diff, try to find
    a nearby line that IS in the diff.

    Args:
        file_path: File path to check
        line: Reported line number (may or may not be valid)
        valid_lines: Dict from get_valid_comment_lines()
        max_distance: Maximum distance to search for nearby valid line

    Returns:
        Valid line number for comment, or None if no valid line found nearby
    """
    if file_path not in valid_lines:
        return None

    file_valid_lines = valid_lines[file_path]

    # Check if exact line is valid
    if line in file_valid_lines:
        return line

    # Search for nearest valid line (prefer higher line numbers for added lines)
    for offset in range(1, max_distance + 1):
        if line + offset in file_valid_lines:
            return line + offset
        if line - offset in file_valid_lines:
            return line - offset

    return None


def validate_issue_line(
    file_path: str,
    line_start: int,
    line_end: int | None,
    valid_lines: Dict[str, Set[int]],
) -> Tuple[int | None, int | None]:
    """Validate and adjust issue line numbers for inline comments.

    Args:
        file_path: File path from issue
        line_start: Starting line number from issue
        line_end: Ending line number from issue (optional)
        valid_lines: Dict from get_valid_comment_lines()

    Returns:
        Tuple of (valid_start, valid_end) or (None, None) if no valid position
    """
    valid_start = find_nearest_valid_line(file_path, line_start, valid_lines)
    if valid_start is None:
        return None, None

    if line_end is None or line_end == line_start:
        return valid_start, valid_start

    # For line ranges, find a valid ending line near the reported end
    valid_end = find_nearest_valid_line(file_path, line_end, valid_lines)
    if valid_end is None:
        valid_end = valid_start

    # Ensure start <= end
    if valid_start > valid_end:
        valid_start, valid_end = valid_end, valid_start

    return valid_start, valid_end


def build_hunk_summary_for_prompt(diff_text: str) -> str:
    """Build a human-readable summary of valid comment lines for the LLM prompt.

    This tells the LLM exactly which line numbers can receive inline comments.
    """
    hunks_by_file = parse_diff_positions(diff_text)

    lines = ["## Valid Comment Lines", ""]
    lines.append("You can ONLY report issues on these exact line numbers:")
    lines.append("")

    for file_path, hunks in sorted(hunks_by_file.items()):
        if not hunks:
            continue

        # Merge all commentable lines for this file
        all_valid = sorted(set(ln for h in hunks for ln in h.commentable_lines))

        # Group into ranges for readability
        ranges = []
        if all_valid:
            start = all_valid[0]
            end = start
            for ln in all_valid[1:]:
                if ln == end + 1:
                    end = ln
                else:
                    ranges.append((start, end))
                    start = ln
                    end = ln
            ranges.append((start, end))

        # Format ranges
        range_strs = []
        for s, e in ranges:
            if s == e:
                range_strs.append(str(s))
            else:
                range_strs.append(f"{s}-{e}")

        # Limit to prevent context explosion
        if len(range_strs) > 10:
            total_lines = len(all_valid)
            lines.append(f"- `{file_path}`: {total_lines} lines ({', '.join(range_strs[:5])}...)")
        else:
            lines.append(f"- `{file_path}`: {', '.join(range_strs)}")

    return "\n".join(lines)
