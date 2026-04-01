"""Models for the agentic per-file code review pipeline."""


from pydantic import BaseModel, Field


class IssueDetail(BaseModel):
    """Structured issue output from the LLM."""

    issue_type: str = Field(description="Type: Bug, Security, Performance, etc.")
    category: str = Field(description="Category: bug, security, performance, etc.")
    title: str = Field(description="Short descriptive title")
    file: str = Field(description="File path")
    line_start: int = Field(description="Starting line number")
    line_end: int = Field(description="Ending line number")
    description: str = Field(description="What's wrong and what triggers it")
    suggestion: str = Field(description="How to fix the issue")
    impact: str = Field(description="Production consequence")
    code_snippet: str = Field(description="Exact code from the diff")
    confidence: int = Field(description="Confidence level 5-9")
    ai_fix: str = Field(description="Corrected code (not diff format)")
    ai_agent_prompt: str = Field(description="Fix instruction for AI agent")
    status: str = Field(description="new, fixed, or still_open")


class FileReviewLLMOutput(BaseModel):
    """Structured output schema for the LLM per-file review."""

    walk_through: str = Field(description="One sentence describing what changed in this file")
    issues: list[IssueDetail] = Field(default_factory=list, description="Issues found in this file")
    positive_findings: list[str] = Field(
        default_factory=list, description="Good patterns found in the code"
    )


class FileReviewResult(BaseModel):
    """Result of reviewing a single file."""

    file_path: str = Field(description="Path to the file that was reviewed")
    issues: list[dict] = Field(
        default_factory=list, description="List of issues found in this file"
    )
    walk_through_entry: str = Field(
        default="", description="One sentence about what changed in this file"
    )
    positive_findings: list[str] = Field(
        default_factory=list, description="Good patterns found in this file"
    )
    previous_issues_status: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of previous issue IDs to status: fixed/still_open/new",
    )
    tool_rounds: int = Field(default=0, description="Tool rounds used by Explorer agent")
    raw_agent_output: str = Field(
        default="", description="Raw JSON output from the review agent for this file"
    )
    error: str | None = Field(default=None, description="Error message if review failed")


class AggregatedReviewResult(BaseModel):
    """Aggregated results from all file reviews."""

    issues: list[dict] = Field(default_factory=list, description="All issues from all files")
    walk_through: list[str] = Field(default_factory=list, description="One entry per changed file")
    positive_findings: list[str] = Field(
        default_factory=list, description="All positive findings from all files"
    )
    previous_fixed: list[dict] = Field(
        default_factory=list, description="Issues that were fixed since last run"
    )
    previous_still_open: list[dict] = Field(
        default_factory=list, description="Issues that are still open from last run"
    )
    lint_issues: list[dict] = Field(default_factory=list, description="Issues from static analysis")
    total_files_reviewed: int = Field(default=0, description="Number of files reviewed")
    total_tool_rounds: int = Field(default=0, description="Total tool rounds used by all agents")
    summary: str = Field(default="", description="Brief summary of the review")
    raw_agent_outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Raw JSON output from each file review, keyed by file path",
    )
