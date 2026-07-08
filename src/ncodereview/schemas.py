
from __future__ import annotations

from pydantic import BaseModel, Field

from common.schemas import (
    AgentPositiveFinding,
    FileBasedIssues,
    FileBasedWalkthrough,
)


class SubagentReviewIssue(BaseModel):
    """Subagent-friendly issue type.

    Mirrors `ReviewCodeIssue` but strips the `ge`/`le` bounds on `confidence` so
    the generated JSON schema does not contain `minimum`/`maximum` on an integer
    field — Anthropic/Bedrock reject those for `integer` types.
    """

    file: str
    line_start: int
    line_end: int | None = None
    issue_type: str = "Bug"
    category: str = "bug"
    severity: str = "medium"
    title: str
    description: str = ""
    suggestion: str = ""
    impact: str = ""
    code_snippet: str = ""
    confidence: int = 8


class SubagentReviewPayload(BaseModel):
    """What a single specialized subagent returns."""

    issues: list[SubagentReviewIssue] = Field(default_factory=list)
    positives: list[AgentPositiveFinding] = Field(default_factory=list)
    walkthrough: list[FileBasedWalkthrough] = Field(
        default_factory=list,
        description="One-sentence summary per changed file.",
    )


class FinalReviewOutput(BaseModel):
    """Final structured response from the orchestrator after subagents finish."""

    issues: list[FileBasedIssues] = Field(
        default_factory=list,
        description="All issues found, grouped by file.",
    )
    positives: list[AgentPositiveFinding] = Field(default_factory=list)
    walkthrough: list[FileBasedWalkthrough] = Field(
        default_factory=list,
        description="One-sentence summary per file reviewed.",
    )
    summary: str = Field(
        default="",
        description="One-paragraph overall review summary for the PR.",
    )
    raw_agent_outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Raw JSON output from each subagent. "
        "Keys are subagent names, values are the raw JSON strings.",
    )


__all__ = [
    "AgentPositiveFinding",
    "FileBasedIssues",
    "FileBasedWalkthrough",
    "FinalReviewOutput",
    "SubagentReviewIssue",
    "SubagentReviewPayload",
]
