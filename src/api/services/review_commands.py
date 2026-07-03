"""GitHub comment command registry.

Centralizes all @bugviper comment parsing and the mapping from comment
commands to review types/modes. When you need to add a new command, add it
here — nowhere else should need to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReviewType(Enum):
    """Review types exposed to the pipeline."""

    INCREMENTAL_REVIEW = "incremental_review"
    FULL_REVIEW = "full_review"
    RESOLVE = "resolve"
    HELP = "help"


@dataclass
class ReviewCommand:
    label: str
    review_type: ReviewType
    description: str


ALL_COMMANDS: list[ReviewCommand] = [
    ReviewCommand(
        label="review",
        review_type=ReviewType.INCREMENTAL_REVIEW,
        description="Review changed files in this PR.",
    ),
    ReviewCommand(
        label="full review",
        review_type=ReviewType.FULL_REVIEW,
        description="Full diff review — all changed files.",
    ),
    ReviewCommand(
        label="resolve",
        review_type=ReviewType.RESOLVE,
        description="Resolve all BugViper inline review comments on this PR.",
    ),
    ReviewCommand(
        label="help",
        review_type=ReviewType.HELP,
        description="Show this help message.",
    ),
]

_LABEL_TO_COMMAND: dict[str, ReviewCommand] = {c.label: c for c in ALL_COMMANDS}


_REVIEW_COMMAND_PATTERN = re.compile(
    r"""
    @bugviper                 # bot mention
    \s+                       # require whitespace after mention
    (?:
        full\s+review         # "full review" — must come before "review" to avoid partial match
        |
        help(?=\s|$)          # "help"
        |
        resolve(?=\s|$)       # "resolve"
        |
        review(?=\s|$)        # "review" — incremental/default
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_bot_mentioned(comment_body: str) -> bool:
    """True if @bugviper is mentioned anywhere in the comment."""
    if not comment_body:
        return False
    return bool(re.search(r"@bugviper", comment_body, re.IGNORECASE))


def extract_review_command(comment_body: str) -> Optional[ReviewType]:
    """Map a GitHub comment body to a ReviewType.

    Matching is in priority order: "full review" > "resolve" > "review".
    Only the first match wins.
    """
    if not comment_body:
        return None

    match = _REVIEW_COMMAND_PATTERN.search(comment_body)
    if not match:
        return None

    raw = match.group(0).lower().strip()

    if "full" in raw:
        return ReviewType.FULL_REVIEW
    if raw.startswith("@bugviper help"):
        return ReviewType.HELP
    if raw.startswith("@bugviper resolve"):
        return ReviewType.RESOLVE
    return ReviewType.INCREMENTAL_REVIEW


def describe_command(review_type: ReviewType) -> str:
    """Human-readable description of a review type."""
    cmd = next((c for c in ALL_COMMANDS if c.review_type == review_type), None)
    return cmd.description if cmd else ""


def format_help_text() -> str:
    """Build the help comment posted when @bugviper help is triggered."""
    lines = ["**Available @bugviper commands:**\n"]
    for cmd in ALL_COMMANDS:
        lines.append(f"• `@bugviper {cmd.label}` — {cmd.description}")
    return "\n".join(lines)

