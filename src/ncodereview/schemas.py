"""Structured output schemas for the DeepAgent review pipeline.

Reuses the existing `ReviewCodeIssue` / `AgentPositiveFinding` / `FileBasedWalkthrough`
types so the new pipeline stays compatible with the existing comment formatters
and Firestore persistence.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from code_review_agent.nagent.nstate import (
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


__all__ = [
    "AgentPositiveFinding",
    "FileBasedIssues",
    "FileBasedWalkthrough",
    "SubagentReviewIssue",
    "SubagentReviewPayload",
    "FinalReviewOutput",
]
