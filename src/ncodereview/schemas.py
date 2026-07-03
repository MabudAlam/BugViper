
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
    judge_verdict: JudgeVerdict = Field(
        description="Per-finding classification from judge-reviewer: "
        "valid | nitpick | outside-diff | false. "
        "MUST be obtained by calling task(subagent_type='judge-reviewer') "
        "with the raw issues JSON as description. Required — cannot be null.",
    )


class JudgeVerdictEntry(BaseModel):
    """One finding's classification returned by the judge-reviewer."""

    file: str
    line_start: int
    line_end: int | None = None
    category: str = Field(
        description="Echo the finding's category for matching back to the raw issue."
    )
    classification: str = Field(
        description="'valid' | 'nitpick' | 'outside-diff' | 'false'"
    )
    drop_reason: str | None = None
    resolved_line_start: int | None = None
    resolved_line_end: int | None = None


class JudgeVerdict(BaseModel):
    """What the judge-reviewer subagent returns: classification per finding."""

    verdicts: list[JudgeVerdictEntry] = Field(default_factory=list)


__all__ = [
    "AgentPositiveFinding",
    "FileBasedIssues",
    "FileBasedWalkthrough",
    "FinalReviewOutput",
    "JudgeVerdict",
    "JudgeVerdictEntry",
    "SubagentReviewIssue",
    "SubagentReviewPayload",
]
