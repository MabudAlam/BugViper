from typing import Any, Literal
from pydantic import BaseModel, Field


class Issue(BaseModel):
    """Code review issue/finding."""

    issue_type: str = Field(
        default="Potential issue",
        description=(
            "Human-readable label for the kind of issue. "
            "Examples: 'Bug', 'Potential issue', 'Security concern', "
            "'Performance', 'Logic error', 'Missing validation', 'Resource leak'."
        ),
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
            "Self-assessed confidence 0-9 for LLM findings. "
            "10 is RESERVED for deterministic static analysis tools only — never set 10 yourself. "
            "9 = provable from diff lines alone. "
            "7-8 = strong signal, some context assumed. "
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
    ai_agent_prompt: str | None = Field(
        default=None,
        description=(
            "A self-contained, machine-readable instruction for an AI agent to verify and fix this issue. "
            "Include: exact file path, line range, what to check, and what change to make. "
            "Written in second-person imperative. Example: "
            "'In `foo/bar.py` around lines 42-50, verify that ... then replace ... with ...'."
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
    # Debug fields — raw outputs for transparency
    raw_agent_json: str = Field(default="", description="Raw JSON string from the Review Agent")
    tool_rounds_used: int = Field(default=0, description="Tool rounds used by the Explorer (Phase 1)")
    review_agent_rounds_used: int = Field(default=0, description="Tool rounds used by the Review Agent (Phase 2)")


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
