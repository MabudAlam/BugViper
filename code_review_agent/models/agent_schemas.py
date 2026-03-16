from typing import Any, Literal
from pydantic import BaseModel, Field


class Issue(BaseModel):
    """Code review issue/finding."""

    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="Issue severity level"
    )
    category: str = Field(description="Issue category (e.g., 'security', 'bug', 'style')")
    title: str = Field(description="Short title of the issue")
    file: str = Field(description="File path where issue was found")
    line_start: int = Field(description="Starting line number")
    line_end: int | None = Field(default=None, description="Ending line number (optional)")
    description: str = Field(description="Detailed description of the issue")
    suggestion: str | None = Field(default=None, description="Suggested fix (optional)")
    impact: str | None = Field(default=None, description="Impact assessment (optional)")
    code_snippet: str | None = Field(
        default=None,
        description=(
            "The exact problematic lines from the diff (2-6 lines max), "
            "verbatim as they appear in the `+` lines. Used for inline display."
        ),
    )
    confidence: int = Field(
        default=8,
        ge=0,
        le=10,
        description=(
            "Self-assessed confidence 0-10. "
            "10 = provable from diff lines alone. "
            "7-9 = strong signal, some context assumed. "
            "4-6 = uncertain, possible false positive. "
            "0-3 = very speculative."
        ),
    )
    ai_fix: str | None = Field(
        default=None,
        description=(
            "Unified diff patch showing the fix. Use `-` prefix for removed lines "
            "and `+` prefix for added lines. Keep it minimal - only the changed lines "
            "plus 1-2 lines of context. Only populate when the fix is unambiguous."
        ),
    )
    status: Literal["new", "still_open", "fixed"] = Field(
        default="new",
        description=(
            "new = not seen before. "
            "still_open = was in previous review and is still present. "
            "fixed = was in previous review and has been addressed."
        ),
    )


class ReconciledReview(BaseModel):
    """Review results ready for display."""

    issues: list[Issue] = Field(default_factory=list)
    positive_findings: list[str] = Field(default_factory=list)
    summary: str = ""


class AgentFindings(BaseModel):
    """Structured output from the reviewer agent."""

    walk_through: list[str] = Field(
        default_factory=list,
        description=(
            "One entry per changed file, formatted as 'filename — one-sentence summary of what changed'. "
            "Focus on the intent of the change, not just 'Modified'."
        ),
    )
    issues: list[Issue] = Field(default_factory=list)
    positive_findings: list[str] = Field(
        default_factory=list,
        description=(
            "3–6 specific things done well in this PR: good patterns, security improvements, "
            "test coverage, refactors that reduce complexity, etc. "
            "Be concrete — reference the actual code or file, not generic praise. "
            "Always populate this — even if there are many issues, acknowledge what was done right."
        ),
    )


class FileSummary(BaseModel):
    """Summary of changes in a single file."""

    file: str
    lines_added: int
    lines_removed: int
    what_changed: str


class ReviewResults(BaseModel):
    """Results from code review analysis."""

    summary: str = Field(description="Brief 1-2 sentence overview of the review")
    issues: list[Issue] = Field(default_factory=list, description="List of issues found")
    positive_findings: list[str] = Field(
        default_factory=list, description="Positive aspects of the code"
    )
    walk_through: list[str] = Field(
        default_factory=list, description="Per-file change summaries from the agent"
    )
    error: str | None = Field(default=None, description="Error message if review failed")
    files_changed_summary: list[FileSummary] = Field(default_factory=list)


class ContextData(BaseModel):
    """Impact analysis and dependency graph data."""

    files_changed: list[str] = Field(default_factory=list)
    modified_symbols: list[str] = Field(default_factory=list)
    total_callers: int = 0
    risk_level: Literal["low", "medium", "high"] = "low"


class ReviewResult(BaseModel):
    """Complete output from code review workflow."""

    should_proceed: bool = Field(description="Whether review was performed")
    intent_reason: str = Field(description="Reason for proceed/skip decision")
    context: ContextData | None = None
    review_results: ReviewResults = Field(
        default_factory=lambda: ReviewResults(summary="", issues=[], positive_findings=[]),
    )
    final_comment: str = Field(default="")
