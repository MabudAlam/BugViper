from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class ReviewCodeIssue(BaseModel):
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
        description=("Issue category: 'bug', 'security', 'performance', 'error_handling'"),
    )
    severity: str = Field(
        default="medium",
        description=(
            "Issue severity: 'critical' (data loss, security breach, outage), "
            "'high' (crash, incorrect results, broken functionality), "
            "'medium' (performance degradation, poor error handling, edge case failure)"
        ),
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
            "What the code does wrong, what input triggers it, "
            "what happens at runtime, why it matters."
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
            "Self-assessed confidence 0-10. "
            "10 = provable from diff. 7-9 = strong signal. "
            "Issues below 7 must be excluded."
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


class FileBasedIssues(BaseModel):
    """Issues grouped by file."""

    file: str = Field(description="File path where issues were found")
    issues: list[ReviewCodeIssue] = Field(
        default_factory=list,
        description="List of issues found in this file. May be empty if no issues.",
    )


class AgentPositiveFinding(BaseModel):
    """Positive finding - something good the agent noticed."""

    file_path: str = Field(description="File path where positive finding was observed")
    positive_finding: list[str] = Field(
        default_factory=list,
        description=(
            "List of positive findings in this file. "
            "Examples: 'Good error handling', 'Clear logic', "
            "'Well-structured code', 'Performance optimization', 'Security best practice'."
        ),
    )


class FileBasedWalkthrough(BaseModel):
    """Single-sentence summary of changes in a file."""

    file: str = Field(description="File path being summarized")
    summary: str = Field(
        default="",
        description=(
            "A single concise sentence summarizing what this file changed. "
            "Examples: 'Added input validation and error handling for user queries', "
            "'Refactored database connection logic to use connection pooling', "
            "'Fixed null pointer dereference in user profile handler'."
        ),
    )


class ReviewerOutput(BaseModel):
    """Structured output from the reviewer node."""

    file_based_issues: list[FileBasedIssues] = Field(
        default_factory=list,
        description="List of issues found, grouped by file.",
    )
    file_based_positive_findings: list[AgentPositiveFinding] = Field(
        default_factory=lambda: [],
        description="List of positive findings, grouped by file.",
    )


class PreviousIssue(BaseModel):
    """A previous issue from an earlier review run."""

    title: str = Field(description="Issue title")
    file: str = Field(description="File path")
    line_start: int = Field(description="Starting line number")
    line_end: int | None = Field(default=None, description="Ending line number")
    description: str = Field(default="", description="Issue description")
    code_snippet: str = Field(default="", description="Code snippet that shows the issue")
    status: str = Field(default="new", description="Original status from previous review")


class ValidatedIssue(BaseModel):
    """A previous issue validated by AI against current code."""

    title: str = Field(description="Original issue title")
    file: str = Field(description="File path")
    line_start: int = Field(description="Original line number (may have shifted)")
    line_end: int | None = Field(default=None, description="End line if multi-line")
    status: str = Field(description="One of: 'fixed', 'still_open', or 'partially_fixed'")
    reason: str = Field(description="Brief explanation of why this status was determined")
    confidence: int = Field(
        default=8,
        ge=0,
        le=10,
        description="Confidence 0-10 in the status determination",
    )
    category: str = Field(default="bug", description="Issue category")
    severity: str = Field(default="medium", description="Issue severity")
    description: str = Field(default="", description="Issue description")
    suggestion: str = Field(default="", description="How to fix")
    impact: str = Field(default="", description="Impact if not fixed")
    code_snippet: str = Field(default="", description="Code snippet showing the issue")


class ValidatorOutput(BaseModel):
    """Structured output from the validator node."""

    validated_issues: list[ValidatedIssue] = Field(
        default_factory=list,
        description="List of previous issues with their validation status.",
    )


class SummarizerOutput(BaseModel):
    """Structured output from the summarizer node."""

    file_based_walkthrough: list[FileBasedWalkthrough] = Field(
        default_factory=list,
        description="Step-by-step walkthrough of the code, grouped by file.",
    )


def _merge_sources(
    current: list[dict[str, Any]], update: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge source artifacts without duplicates."""
    seen = {(s.get("path"), s.get("line_number")) for s in current if isinstance(s, dict)}
    result = list(current)
    for s in update:
        if isinstance(s, dict):
            key = (s.get("path"), s.get("line_number"))
            if key not in seen:
                seen.add(key)
                result.append(s)
    return result


class CodeReviewAgentState(TypedDict):
    """State for the code review LangGraph agent."""

    file_based_context: str
    file_content: str
    previous_issues: list[dict[str, Any]]
    validated_previous_issues: list[dict[str, Any]]
    entity_risk_context: str

    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    sources: Annotated[list[dict[str, Any]], _merge_sources]

    file_based_issues: list[FileBasedIssues]
    file_based_positive_findings: list[AgentPositiveFinding]
    file_based_walkthrough: list[FileBasedWalkthrough]
