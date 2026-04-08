import re
from enum import Enum
from typing import Optional


class ReviewType(Enum):
    FULL_REVIEW = "full_review"
    INCREMENTAL_REVIEW = "incremental_review"


class ReviewCommandService:
    BOT_NAME = "@bugviper"

    _BOT_MENTION_PATTERN = re.compile(r"@bugviper", re.IGNORECASE)

    _COMMAND_PATTERN = re.compile(
        r"""
        @bugviper                 # bot mention
        \s+                       # require whitespace after mention
        (?:
            full\s+review          # full review - must match first
            |
            review(?=\s|$)         # incremental review (lookahead prevents 'reviewed')
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    @classmethod
    def is_bot_mentioned(cls, comment_body: str) -> bool:
        """
        Check if @bugviper is mentioned in the comment (case-insensitive).

        Returns:
            True if bot is mentioned, False otherwise
        """
        if not comment_body:
            return False
        return bool(cls._BOT_MENTION_PATTERN.search(comment_body))

    @classmethod
    def extract_command(cls, comment_body: str) -> Optional[ReviewType]:
        """
        Extract review command from a GitHub comment.

        Supported:
            @bugviper review      → INCREMENTAL_REVIEW
            @bugviper full review → FULL_REVIEW

        Returns:
            ReviewType or None (if no valid command found)
        """
        if not comment_body:
            return None

        match = cls._COMMAND_PATTERN.search(comment_body)
        if not match:
            return None

        full_match = match.group(0).lower()
        if "full" in full_match:
            return ReviewType.FULL_REVIEW

        return ReviewType.INCREMENTAL_REVIEW


def is_bot_mentioned(comment_body: str) -> bool:
    """Check if @bugviper is mentioned in the comment."""
    return ReviewCommandService.is_bot_mentioned(comment_body)


def extract_review_command(comment_body: str) -> Optional[ReviewType]:
    """Extract review command from comment body."""
    return ReviewCommandService.extract_command(comment_body)
