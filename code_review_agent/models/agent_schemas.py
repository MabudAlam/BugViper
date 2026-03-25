from typing import Literal

from pydantic import BaseModel, Field


class Issue(BaseModel):
    """Code review issue/finding."""

    issue_type: str = Field(
        default="Potential issue",
        description=(
            "Human-readable label for the kind of issue. "
            "Examples: 'Bug', 'Security', 'Performance', 'Error Handling', 'Logic Error'."
        ),
    )
    category: str = Field(
        default="bug",
        description="Issue category: 'bug', 'security', 'performance', 'error_handling', 'style'",
    )
    title: str = Field(description="Short specific title naming the exact bug")
    file: str = Field(description="File path where issue was found")
    line_start: int = Field(description="Starting line number in the NEW file (post-change)")
    line_end: int | None = Field(
        default=None,
        description="Ending line number if multi-line issue. Same as line_start if single line.",
    )
    description: str = Field(
        default="",
        description=(
            "What the code does wrong, what input triggers it, what happens at runtime, why it matters."
        ),
    )
    suggestion: str = Field(
        default="",
        description="One clear sentence on how to fix the issue.",
    )
    impact: str = Field(
        default="",
        description="Concrete production consequence: crash, data loss, security breach, etc.",
    )
    code_snippet: str = Field(
        default="",
        description="The exact problematic lines from the diff (3-8 lines), copied VERBATIM.",
    )
    confidence: int = Field(
        default=8,
        ge=0,
        le=10,
        description=(
            "Self-assessed confidence 0-9. "
            "9 = provable from diff. 7-8 = strong signal. 5-6 = likely."
        ),
    )
    ai_fix: str = Field(
        default="",
        description="The CORRECTED code - the fixed version. NOT a diff, just the new code.",
    )
    ai_agent_prompt: str = Field(
        default="",
        description="Instruction for AI agent: file path, lines, what to check, what to change.",
    )
    status: Literal["new", "still_open", "fixed"] = Field(
        default="new",
        description="new = not seen before. still_open = still present. fixed = addressed.",
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
    raw_agent_json: str = Field(default="", description="Raw JSON string from the Review Agent")
    tool_rounds_used: int = Field(
        default=0, description="Tool rounds used by the Explorer (Phase 1)"
    )
    review_agent_rounds_used: int = Field(
        default=0, description="Tool rounds used by the Review Agent (Phase 2)"
    )


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
