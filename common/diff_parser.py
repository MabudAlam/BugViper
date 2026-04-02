import re
from collections import defaultdict
from typing import Dict, List


def parse_unified_diff(diff_text: str) -> List[Dict[str, object]]:
    """
    Parse a unified diff into a list of file changes with line ranges.

    Args:
        diff_text: Full unified diff string

    Returns:
        List of dicts: {"file_path": str, "start_line": int, "end_line": int}
        Each entry represents one hunk's new-file line range.
    """
    results: List[Dict[str, object]] = []
    current_file = None

    for line in diff_text.splitlines():
        # Match file header: +++ b/path/to/file.py
        file_match = re.match(r"^\+\+\+ b/(.+)$", line)
        if file_match:
            current_file = file_match.group(1)
            continue

        # Match hunk header: @@ -a,b +c,d @@
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match and current_file:
            start_line = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            # count=0 means a deletion-only hunk — no new-file lines exist,
            # so there is nothing to comment on (side=RIGHT). Skip it.
            if count == 0:
                continue
            end_line = start_line + count - 1
            results.append(
                {
                    "file_path": current_file,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )

    return results


def split_diff_by_file(diff_text: str) -> Dict[str, str]:
    """
    Split a unified diff into per-file diff chunks.

    Args:
        diff_text: Full unified diff string

    Returns:
        Dict mapping file_path -> that file's diff text
    """
    files: Dict[str, List[str]] = {}
    current_file = None
    current_lines: List[str] = []

    for line in diff_text.splitlines():
        diff_header = re.match(r"^diff --git a/.+ b/(.+)$", line)
        if diff_header:
            # Save previous file
            if current_file and current_lines:
                files[current_file] = "\n".join(current_lines)
            current_file = diff_header.group(1)
            current_lines = [line]
            continue

        if current_file is not None:
            current_lines.append(line)

    # Save last file
    if current_file and current_lines:
        files[current_file] = "\n".join(current_lines)

    return files


def group_changes_by_file(
    changes: List[Dict[str, object]],
) -> Dict[str, List[Dict[str, object]]]:
    """
    Group hunk-level changes by file path.

    Args:
        changes: Output of parse_unified_diff

    Returns:
        Dict mapping file_path -> list of hunks for that file
    """
    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for change in changes:
        grouped[change["file_path"]].append(change)
    return dict(grouped)
