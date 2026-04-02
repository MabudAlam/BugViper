"""Diff line mapper for GitHub inline comments.

GitHub's inline comment API requires precise line numbers that exist in the diff.
This module creates a proper mapping from file line numbers to valid comment positions.

Unified Diff Format:
    --- a/file.py    (old file)
    +++ b/file.py    (new file)
    @@ -1,3 +1,4 @@  (hunk header: old lines 1-3 -> new lines 1-4)
    -deleted line     (exists only in old file)
    +added line       (exists only in new file)
     context line     (unchanged, shown for reference)

GitHub allows inline comments ONLY on:
    - Added lines (+)
    - Context lines (unchanged lines shown in diff)

GitHub does NOT allow inline comments on:
    - Deleted lines (-)
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class DiffHunk:
    """Represents a single hunk (contiguous block of changes) in a unified diff.

    A hunk has:
    - old_start, old_count: where it starts in the OLD file and how many lines
    - new_start, new_count: where it starts in the NEW file and how many lines
    - commentable_lines: which NEW file line numbers can receive inline comments

    Example:
        @@ -10,3 +10,5 @@
        This hunk takes 3 lines from old file starting at line 10
        and produces 5 lines in new file starting at line 10.
        The new file lines 10, 11, 12, 13, 14 are in this hunk.
    """

    file_path: str  # Path to the file this hunk belongs to
    old_start: int  # Starting line number in OLD file
    old_count: int  # Number of lines from OLD file
    new_start: int  # Starting line number in NEW file
    new_count: int  # Number of lines in NEW file

    # Lines in the NEW file that can receive inline comments
    # (includes added lines and context lines, but NOT deleted lines)
    commentable_lines: Set[int] = field(default_factory=set)


# =============================================================================
# Parsing Functions
# =============================================================================


def parse_diff_positions(diff_text: str) -> Dict[str, List[DiffHunk]]:
    """Parse a unified diff and extract hunk information per file.

    This is the main parsing function. It walks through the diff line by line,
    tracking which file we're in, which hunk we're in, and which line numbers
    in the NEW file can receive inline comments.

    How it works:
        1. When we see "+++ b/path/to/file.py", we switch to that file
        2. When we see "@@ -a,b +c,d @@", we start a new hunk
        3. For each diff line (prefixed with +, -, or space):
           - "+" line: CAN comment (added in new file) → add new line number
           - "-" line: CANNOT comment (deleted from old file) → skip
           - " " line: CAN comment (context in both files) → add new line number

    Args:
        diff_text: The full unified diff as a string

    Returns:
        Dict mapping file_path -> list of DiffHunks for that file

    Example diff:
        +++ b/app.py
        @@ -1,3 +1,4 @@
        -old line
        +new line
         context line

    Example return:
        {
            "app.py": [
                DiffHunk(
                    file_path="app.py",
                    old_start=1, old_count=3,
                    new_start=1, new_count=4,
                    commentable_lines={1, 2, 3, 4}  # new line numbers
                )
            ]
        }
    """
    result: Dict[str, List[DiffHunk]] = {}  # Final output: file -> hunks
    current_file: str | None = None  # File we're currently parsing
    current_hunk: DiffHunk | None = None  # Hunk we're currently parsing
    current_new_line: int = 0  # Current line number in NEW file

    # Iterate through each line in the diff
    for line in diff_text.splitlines():
        # -------------------------------------------------------------------------
        # Step 1: Check for file header "+++ b/path/to/file.py"
        # -------------------------------------------------------------------------
        file_match = re.match(r"^\+\+\+ b/(.+)$", line)
        if file_match:
            # Save the previous hunk before switching to a new file
            if current_hunk and current_file:
                if current_file not in result:
                    result[current_file] = []
                result[current_file].append(current_hunk)

            # Start tracking the new file
            current_file = file_match.group(1)
            if current_file not in result:
                result[current_file] = []
            current_hunk = None  # New file means no active hunk yet
            continue  # Move to next line

        # -------------------------------------------------------------------------
        # Step 2: Check for hunk header "@@ -a,b +c,d @@"
        # -------------------------------------------------------------------------
        hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match and current_file:
            # Save the previous hunk before starting a new one
            if current_hunk:
                result[current_file].append(current_hunk)

            # Parse the hunk header numbers
            # Format: @@ -old_start,old_count +new_start,new_count @@
            # Groups:    1        2           3        4
            # Group 2 and 4 are optional (default to 1 if missing)
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            # Create a new DiffHunk object
            current_hunk = DiffHunk(
                file_path=current_file,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                commentable_lines=set(),
            )

            # Start counting NEW file lines from new_start
            current_new_line = new_start
            continue  # Move to next line

        # -------------------------------------------------------------------------
        # Step 3: Skip if we're not inside a hunk yet
        # -------------------------------------------------------------------------
        if not current_hunk or not current_file:
            continue  # No active hunk, skip this line

        # -------------------------------------------------------------------------
        # Step 4: Process the diff content line
        # -------------------------------------------------------------------------
        if line.startswith("+"):
            # ADDED line: exists in NEW file but not in OLD file
            # GitHub CAN comment on this → add to commentable_lines
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1  # Advance to next NEW file line

        elif line.startswith("-"):
            # DELETED line: exists in OLD file but not in NEW file
            # GitHub CANNOT comment on this (line doesn't exist in new file)
            pass  # Don't advance current_new_line

        elif line.startswith(" "):
            # CONTEXT line: unchanged, exists in both OLD and NEW files
            # GitHub CAN comment on this → add to commentable_lines
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1  # Advance to next NEW file line

        elif line.startswith("\\"):
            # "\ No newline at end of file" marker
            # Skip this, don't advance line counter
            pass

        else:
            # Unexpected format - treat conservatively as context
            current_hunk.commentable_lines.add(current_new_line)
            current_new_line += 1

    # -------------------------------------------------------------------------
    # Step 5: Save the last hunk when done
    # -------------------------------------------------------------------------
    if current_hunk and current_file:
        result[current_file].append(current_hunk)

    return result


# =============================================================================
# Query Functions
# =============================================================================


def get_valid_comment_lines(diff_text: str) -> Dict[str, Set[int]]:
    """Get all valid line numbers for inline comments, grouped by file.

    This is a convenience wrapper around parse_diff_positions().
    It returns a simpler structure: just file_path -> set of valid line numbers.

    Args:
        diff_text: Full unified diff string

    Returns:
        Dict mapping file_path -> set of valid NEW file line numbers

    Example:
        Input diff with one hunk covering new lines 10-15:
        Output: {"app.py": {10, 11, 12, 13, 14, 15}}
    """
    # Parse the diff to get hunk information
    hunks_by_file = parse_diff_positions(diff_text)

    # Convert to file_path -> set of commentable lines
    return {
        file_path: {ln for hunk in hunks for ln in hunk.commentable_lines}
        for file_path, hunks in hunks_by_file.items()
    }


def get_hunk_ranges(diff_text: str) -> Dict[str, List[Tuple[int, int]]]:
    """Extract the line ranges covered by each hunk, per file.

    A "hunk" covers a range of lines in the NEW file. This function returns
    those ranges as (start, end) tuples, one tuple per hunk.

    Args:
        diff_text: Full unified diff string

    Returns:
        Dict mapping file_path -> list of (start_line, end_line) tuples

    Example:
        For a diff with file.py having two hunks:
        - First hunk: new_start=10, new_count=5 → lines 10-14
        - Second hunk: new_start=20, new_count=3 → lines 20-22

        Output: {"file.py": [(10, 14), (20, 22)]}

    Note:
        Use this to determine if an issue's line number falls within a hunk:
        - Inside hunk range → inline comment possible
        - Outside hunk ranges → must use regular comment
    """
    # Parse diff to get hunk objects
    hunks_by_file = parse_diff_positions(diff_text)

    # Build output dict
    result: Dict[str, List[Tuple[int, int]]] = {}

    for file_path, hunks in hunks_by_file.items():
        ranges = []
        for hunk in hunks:
            # Calculate the end line: start + count - 1
            # Example: start=10, count=5 → lines 10, 11, 12, 13, 14 → end=14
            start = hunk.new_start
            end = hunk.new_start + hunk.new_count - 1
            ranges.append((start, end))
        result[file_path] = ranges

    return result


def is_line_in_hunk(line: int, hunk_ranges: List[Tuple[int, int]]) -> bool:
    """Check if a line number falls within ANY of the hunk ranges.

    Args:
        line: Line number to check (in NEW file)
        hunk_ranges: List of (start, end) tuples from get_hunk_ranges()

    Returns:
        True if line is inside at least one hunk range, False otherwise

    Example:
        hunk_ranges = [(10, 14), (20, 22)]

        is_line_in_hunk(12, hunk_ranges) → True  (inside first hunk)
        is_line_in_hunk(15, hunk_ranges) → False (between hunks)
        is_line_in_hunk(21, hunk_ranges) → True  (inside second hunk)
    """
    return any(start <= line <= end for start, end in hunk_ranges)


def find_nearest_valid_line(
    file_path: str,
    line: int,
    valid_lines: Dict[str, Set[int]],
    max_distance: int = 5,
) -> int | None:
    """Find the nearest valid comment line when LLM reports an invalid line.

    The LLM sometimes reports line numbers that don't exactly match the diff
    (e.g., off by 1, or reporting a deleted line). This function finds
    the closest line that IS valid for commenting.

    Args:
        file_path: Which file to check
        line: Line number the LLM reported (may not be valid)
        valid_lines: Dict from get_valid_comment_lines()
        max_distance: How far to search for a nearby valid line (default 5)

    Returns:
        The nearest valid line number, or None if nothing close exists

    Example:
        valid_lines = {"app.py": {10, 11, 12, 13, 14}}

        find_nearest_valid_line("app.py", 10, valid_lines) → 10 (exact match)
        find_nearest_valid_line("app.py", 11, valid_lines) → 11 (exact match)
        find_nearest_valid_line("app.py", 15, valid_lines) → 14 (offset -1)
        find_nearest_valid_line("app.py", 16, valid_lines) → None (too far)
    """
    # Check if file exists in our dict
    if file_path not in valid_lines:
        return None

    file_valid_lines = valid_lines[file_path]

    # First check: is the exact line valid?
    if line in file_valid_lines:
        return line

    # Second check: search nearby lines within max_distance
    # Search outward: line+1, line-1, line+2, line-2, ...
    for offset in range(1, max_distance + 1):
        # Prefer lines AFTER the reported line (added lines)
        if line + offset in file_valid_lines:
            return line + offset
        # Then try lines BEFORE (context lines)
        if line - offset in file_valid_lines:
            return line - offset

    # No nearby valid line found
    return None


def validate_issue_line(
    file_path: str,
    line_start: int,
    line_end: int | None,
    valid_lines: Dict[str, Set[int]],
) -> Tuple[int | None, int | None]:
    """Validate and adjust issue line numbers for posting as inline comments.

    Takes the line numbers from an LLM-reported issue and finds the closest
    valid line numbers that GitHub will accept for inline comments.

    Args:
        file_path: File path from the issue
        line_start: Starting line number from the issue
        line_end: Ending line number from the issue (optional for single-line)
        valid_lines: Dict from get_valid_comment_lines()

    Returns:
        Tuple of (valid_start, valid_end) that can be used for GitHub API,
        or (None, None) if no valid position exists

    Example:
        Issue reports: line_start=10, line_end=12
        Valid lines: {10, 11, 12, 13, 14}
        Output: (10, 12) → both valid, use as-is

        Issue reports: line_start=15, line_end=15
        Valid lines: {10, 11, 12}  (15 is not valid)
        Output: (12, 12) → nearest valid is 12
    """
    # Find valid start line
    valid_start = find_nearest_valid_line(file_path, line_start, valid_lines)
    if valid_start is None:
        # No valid line found at all
        return None, None

    # Single line issue or no end specified
    if line_end is None or line_end == line_start:
        return valid_start, valid_start

    # Multi-line issue: find valid end line
    valid_end = find_nearest_valid_line(file_path, line_end, valid_lines)
    if valid_end is None:
        # No valid end found, use start for both
        valid_end = valid_start

    # Ensure start <= end (swap if needed)
    if valid_start > valid_end:
        valid_start, valid_end = valid_end, valid_start

    return valid_start, valid_end


# =============================================================================
# Formatting Functions (for LLM prompts)
# =============================================================================


def format_file_with_line_numbers(content: str) -> str:
    """Format file content with line numbers for the agent prompt.

    Each line is prefixed with its 1-indexed line number and a
    visual separator. This makes it easy for the LLM to reference
    specific lines when reporting issues.

    Args:
        content: Full file content (post-PR version)

    Returns:
        String with each line numbered, like "   1 │ def foo():"

    Example:
        Input:
            def foo()
                pass

        Output:
              1 │ def foo()
              2 │     pass

    Note:
        Line numbers are right-aligned in 4 characters (1-9999)
        The "│" separator distinguishes line numbers from code
    """
    if not content:
        return ""

    lines = content.splitlines()  # Split into individual lines
    formatted = []

    # Enumerate with 1-indexed line numbers
    for i, line in enumerate(lines, start=1):
        # Format: right-aligned number + separator + code
        # {i:>4} means right-align i in 4 characters
        formatted.append(f"{i:>4} │ {line}")

    return "\n".join(formatted)


def build_hunk_summary_for_prompt(diff_text: str) -> str:
    """Build a human-readable summary of valid comment lines for the LLM prompt.

    This creates a markdown section that tells the LLM exactly which line
    numbers in the diff can receive inline comments. The LLM should use
    these line numbers when reporting issues.

    Args:
        diff_text: Full unified diff string

    Returns:
        Markdown string describing valid comment lines per file

    Example output:
        ## Valid Comment Lines

        You can ONLY report issues on these exact line numbers:

        - `app.py`: 10-14, 20-25
        - `utils.py`: 5-8
    """
    # Parse diff to get hunk information
    hunks_by_file = parse_diff_positions(diff_text)

    lines = ["## Valid Comment Lines", ""]
    lines.append("You can ONLY report issues on these exact line numbers:")
    lines.append("")

    # Process each file
    for file_path, hunks in sorted(hunks_by_file.items()):
        if not hunks:
            continue

        # Collect all valid line numbers from all hunks in this file
        all_valid = sorted(set(ln for hunk in hunks for ln in hunk.commentable_lines))

        # Group consecutive lines into ranges for readability
        # Example: [10, 11, 12, 15, 16] → [(10, 12), (15, 16)]
        ranges = []
        if all_valid:
            range_start = all_valid[0]
            range_end = all_valid[0]

            for ln in all_valid[1:]:
                if ln == range_end + 1:
                    # Consecutive line, extend the range
                    range_end = ln
                else:
                    # Gap found, save previous range and start new one
                    ranges.append((range_start, range_end))
                    range_start = ln
                    range_end = ln

            # Don't forget the last range
            ranges.append((range_start, range_end))

        # Convert ranges to strings: (10, 12) → "10-12", (15, 15) → "15"
        range_strs = []
        for s, e in ranges:
            if s == e:
                range_strs.append(str(s))
            else:
                range_strs.append(f"{s}-{e}")

        # Limit output to prevent context overflow
        if len(range_strs) > 10:
            total_lines = len(all_valid)
            # Show first 5 ranges only
            lines.append(f"- `{file_path}`: {total_lines} lines ({', '.join(range_strs[:5])}...)")
        else:
            lines.append(f"- `{file_path}`: {', '.join(range_strs)}")

    return "\n".join(lines)
