from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PrReviewStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FirebaseUserData(BaseModel):
    """User document written to / read from users/{uid}."""

    model_config = ConfigDict(populate_by_name=True)

    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, serialization_alias="displayName")
    github_username: Optional[str] = Field(None, serialization_alias="githubUsername")
    github_access_token: Optional[str] = Field(None, serialization_alias="githubAccessToken")
    photo_url: Optional[str] = Field(None, serialization_alias="photoURL")
    last_login: Optional[str] = Field(None, serialization_alias="lastLogin")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")


class FirebaseUserProfile(BaseModel):
    """Public user profile returned by service methods (no sensitive token)."""

    model_config = ConfigDict(populate_by_name=True)

    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, serialization_alias="displayName")
    github_username: Optional[str] = Field(None, serialization_alias="githubUsername")
    photo_url: Optional[str] = Field(None, serialization_alias="photoURL")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")


class RepoMetadata(BaseModel):
    """
    Full repo metadata document written at ingestion dispatch time.

    Stored at: users/{uid}/repos/{owner}_{repo}
    """

    model_config = ConfigDict(populate_by_name=True)

    owner: str
    repo_name: str = Field(serialization_alias="repoName")
    full_name: str = Field(serialization_alias="fullName")
    description: Optional[str] = None
    language: Optional[str] = None
    stars: int = 0
    forks: int = 0
    private: bool = False
    default_branch: str = Field("main", serialization_alias="defaultBranch")
    size: int = 0
    topics: list[str] = Field(default_factory=list)
    github_created_at: Optional[str] = Field(None, serialization_alias="githubCreatedAt")
    github_updated_at: Optional[str] = Field(None, serialization_alias="githubUpdatedAt")
    branch: Optional[str] = None
    ingestion_status: str = Field("pending", serialization_alias="ingestionStatus")


class RepoIngestionUpdate(BaseModel):
    """
    Partial update written after a successful ingestion run.

    Only the ingestion-result fields — merged into the existing repo doc.
    """

    model_config = ConfigDict(populate_by_name=True)

    ingestion_status: str = Field(serialization_alias="ingestionStatus")
    ingested_at: str = Field(serialization_alias="ingestedAt")
    files_processed: int = Field(serialization_alias="filesProcessed")
    files_skipped: int = Field(serialization_alias="filesSkipped")
    classes_found: int = Field(serialization_alias="classesFound")
    functions_found: int = Field(serialization_alias="functionsFound")
    imports_found: int = Field(serialization_alias="importsFound")
    total_lines: int = Field(serialization_alias="totalLines")


class RepoIngestionError(BaseModel):
    """
    Partial update written when ingestion fails.
    """

    model_config = ConfigDict(populate_by_name=True)

    ingestion_status: str = Field("failed", serialization_alias="ingestionStatus")
    error_message: str = Field(serialization_alias="errorMessage")


class PRMetadata(BaseModel):
    """
    PR metadata document.

    Stored at: users/{uid}/repos/{owner}_{repo}/prs/{pr_number}
    """

    model_config = ConfigDict(populate_by_name=True)

    owner: str
    repo: str
    pr_number: int = Field(serialization_alias="prNumber")
    repo_id: str = Field(serialization_alias="repoId")
    review_status: Optional[PrReviewStatus] = Field(
        default=None, serialization_alias="reviewStatus"
    )
    review_count: int = Field(default=0, serialization_alias="reviewCount")
    open_issue_count: int = Field(default=0, serialization_alias="openIssueCount")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    updated_at: Optional[str] = Field(None, serialization_alias="updatedAt")
    failed_reasons: list[str] = Field(default_factory=list, serialization_alias="failedReasons")


class ReviewRunData(BaseModel):
    """
    Review run document saved after each LLM review.

    Stored at: users/{uid}/repos/{owner}_{repo}/prs/{pr_number}/reviews/run_{n}
    """

    model_config = ConfigDict(populate_by_name=True)

    issues: list[dict]
    positive_findings: list[str]
    summary: str
    files_changed: list[str] = Field(default_factory=list, serialization_alias="filesChanged")
    repo_id: str = Field(serialization_alias="repoId")
    pr_number: int = Field(serialization_alias="prNumber")
