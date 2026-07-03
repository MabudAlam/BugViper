"""Shared Pydantic models for code review I/O.

Used by:
- `ncodereview/` — DeepAgent pipeline (tools.py, schemas.py, pipeline.py)
- `api/utils/comment_formatter.py` — formats Issue/ContextData for posting
- `api/services/lint_service.py` — Issue type for static-analysis findings
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Issue(BaseModel):
    """Single review finding (LLM-produced or from a linter)."""

    issue_type: str = Field(
        default="Potential issue",
        description="Human-readable label: 'Bug', 'Security', 'Performance', etc.",
    )
    category: str = Field(
        default="bug",
        description="Issue category: 'bug', 'security', 'performance', 'error_handling'",
    )
    severity: str = Field(
        default="medium",
        description="'critical' (data loss, breach, outage), 'high' (crash, broken), 'medium'",
    )
    title: str = Field(description="Short specific title naming the exact issue")
    file: str = Field(description="File path where issue was found")
    line_start: int = Field(description="Starting line number in the POST-CHANGE file")
    line_end: int | None = Field(default=None, description="Ending line number if multi-line")
    description: str = Field(
        default="",
        description="What's wrong, what triggers it, runtime impact",
    )
    suggestion: str = Field(default="", description="One sentence on how to fix")
    impact: str = Field(default="", description="Concrete production consequence")
    code_snippet: str = Field(default="", description="The exact problematic lines (3-8), verbatim")
    confidence: int = Field(default=8, ge=0, le=10, description="Self-assessed confidence 0-10")
    ai_fix: str = Field(default="", description="The CORRECTED code (not a diff, just new code)")
    ai_agent_prompt: str = Field(default="", description="Instruction for AI fix agent")
    classification: str | None = Field(
        default=None,
        description=(
            "Judge classification: 'valid' | 'nitpick' | 'outside-diff'. "
            "Used by the comment formatter to bucket issues. None = pre-judge, fall back to confidence."
        ),
    )
    drop_reason: str | None = Field(
        default=None,
        description="Why the judge classified this as 'false'. Never displayed in PR comments.",
    )
    status: Literal["new", "still_open", "fixed"] = Field(
        default="new",
        description="new = not seen before. still_open = still present. fixed = addressed.",
    )


class ReconciledReview(BaseModel):
    """Aggregated review ready for display / posting."""

    issues: list[Issue] = Field(default_factory=list)
    positive_findings: list[str] = Field(default_factory=list)
    summary: str = ""


class ContextData(BaseModel):
    """Impact analysis and dependency graph data."""

    files_changed: list[str] = Field(default_factory=list)
    modified_symbols: list[str] = Field(default_factory=list)
    total_callers: int = 0
    risk_level: Literal["low", "medium", "high"] = "low"


class FileSummary(BaseModel):
    """Summary of changes in a single file."""

    file: str
    lines_added: int
    lines_removed: int
    what_changed: str


class FileBasedIssues(BaseModel):
    """Issues grouped by file."""

    file: str
    issues: list[Issue] = Field(default_factory=list)


class AgentPositiveFinding(BaseModel):
    """Positive finding — something good the agent noticed."""

    file_path: str
    positive_finding: list[str] = Field(default_factory=list)


class FileBasedWalkthrough(BaseModel):
    """Single-sentence summary of changes in a file."""

    file: str
    summary: str = ""


__all__ = [
    "AgentPositiveFinding",
    "ContextData",
    "FileBasedIssues",
    "FileBasedWalkthrough",
    "FileSummary",
    "Issue",
    "ReconciledReview",
]
