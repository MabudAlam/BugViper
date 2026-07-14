
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.schemas import (
    AgentPositiveFinding,
    FileBasedIssues,
    FileBasedWalkthrough,
)


class GithubPrFiles(BaseModel):
    filename: str
    fileContent: str


class GithubPrMeta(BaseModel):
    prTitle: str
    prBody: str


class GithubPrDetails(BaseModel):
    difftext: str
    prMeta: GithubPrMeta
    head_sha: str
    base_sha: str
    head_branch: str
    files: list[GithubPrFiles]


class RepoDetails(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    description: Optional[str] = None
    private: Optional[bool] = None
    default_branch: Optional[str] = None
    language: Optional[str] = None
    size: Optional[int] = None
    stars: Optional[int] = None
    forks: Optional[int] = None
    topics: list[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SubagentReviewIssue(BaseModel):
    """Subagent-friendly issue type.

    Mirrors `ReviewCodeIssue` but strips the `ge`/`le` bounds on `confidence` so
    the generated JSON schema does not contain `minimum`/`maximum` on an integer
    field — Anthropic/Bedrock reject those for `integer` types.
    """

    file: str = ""
    line_start: int = 0
    line_end: int | None = None
    issue_type: str = "Bug"
    category: str = "bug"
    severity: str = "medium"
    title: str = ""
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
    """Final structured response from the generalist reviewer."""

    issues: list[SubagentReviewIssue] = Field(
        default_factory=list,
        description="All issues found by the reviewer.",
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
   


class VerifierVerdict(BaseModel):
    """Verdict for a single finding from the verifier."""

    index: int = Field(description="Index of the finding in the input list")
    keep: bool = Field(default=True, description="True to keep, False to drop")
    rationale: str = Field(default="", description="Why the finding was kept or dropped")
    confidence: str = Field(default="medium", description="high|medium|low")


class VerifierOutput(BaseModel):
    """Output from the verifier subagent. Verdicts for all input findings."""

    verdicts: list[VerifierVerdict] = Field(default_factory=list)


__all__ = [
    "AgentPositiveFinding",
    "FileBasedIssues",
    "FileBasedWalkthrough",
    "FinalReviewOutput",
    "GithubPrDetails",
    "GithubPrFiles",
    "GithubPrMeta",
    "RepoDetails",
    "SubagentReviewIssue",
    "SubagentReviewPayload",
    "VerifierVerdict",
    "VerifierOutput",
]
