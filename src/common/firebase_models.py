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
    github_installation_id: Optional[int] = Field(None, serialization_alias="githubInstallationId")
    github_account_id: Optional[int] = Field(None, serialization_alias="githubAccountId")
    account_type: Optional[str] = Field(None, serialization_alias="accountType")
    repository_selection: Optional[str] = Field(None, serialization_alias="repositorySelection")


class FirebaseUserProfile(BaseModel):
    """Public user profile returned by service methods (no sensitive token)."""

    model_config = ConfigDict(populate_by_name=True)

    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, serialization_alias="displayName")
    github_username: Optional[str] = Field(None, serialization_alias="githubUsername")
    photo_url: Optional[str] = Field(None, serialization_alias="photoURL")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    github_installation_id: Optional[int] = Field(None, serialization_alias="githubInstallationId")
    account_type: Optional[str] = Field(None, serialization_alias="accountType")
    repository_selection: Optional[str] = Field(None, serialization_alias="repositorySelection")


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

    Includes both ingestion-result fields and GitHub metadata to ensure
    the Firestore document has complete information even without the
    initial RepoMetadata write.
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
    owner: Optional[str] = None
    repo_name: Optional[str] = Field(None, serialization_alias="repoName")
    full_name: Optional[str] = Field(None, serialization_alias="fullName")
    description: Optional[str] = None
    language: Optional[str] = None
    stars: int = 0
    forks: int = 0
    private: bool = False
    default_branch: Optional[str] = Field(None, serialization_alias="defaultBranch")
    size: int = 0
    topics: list[str] = Field(default_factory=list)
    github_created_at: Optional[str] = Field(None, serialization_alias="githubCreatedAt")
    github_updated_at: Optional[str] = Field(None, serialization_alias="githubUpdatedAt")
    branch: Optional[str] = None


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
    total_issues_raised: int = Field(default=0, serialization_alias="totalIssuesRaised")
    total_positives: int = Field(default=0, serialization_alias="totalPositives")
    last_review_type: Optional[str] = Field(None, serialization_alias="lastReviewType")
    last_reviewed_sha: Optional[str] = Field(None, serialization_alias="lastReviewedSha")
    last_review_base_sha: Optional[str] = Field(None, serialization_alias="lastReviewBaseSha")
    last_reviewed_at: Optional[str] = Field(None, serialization_alias="lastReviewedAt")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    updated_at: Optional[str] = Field(None, serialization_alias="updatedAt")
    failed_reasons: list[str] = Field(default_factory=list, serialization_alias="failedReasons")
    merged_at: Optional[str] = Field(None, serialization_alias="mergedAt")
    closed_at: Optional[str] = Field(None, serialization_alias="closedAt")


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
    review_type: str = Field(default="incremental_review", serialization_alias="reviewType")
    issues_count: int = Field(default=0, serialization_alias="issuesCount")
    positives_count: int = Field(default=0, serialization_alias="positivesCount")
    walkthrough_count: int = Field(default=0, serialization_alias="walkthroughCount")
    head_sha: Optional[str] = Field(None, serialization_alias="headSha")
    base_sha: Optional[str] = Field(None, serialization_alias="baseSha")
    github_comment_ids: list[dict] = Field(
        default_factory=list,
        serialization_alias="githubCommentIds",
        description="List of {comment_id, thread_id, file, line} for inline comments posted",
    )
    started_at: Optional[str] = Field(None, serialization_alias="startedAt")
    ended_at: Optional[str] = Field(None, serialization_alias="endedAt")
    duration_seconds: Optional[float] = Field(None, serialization_alias="durationSeconds")


class RunAnalytics(BaseModel):
    """Per-run issue counts stored in the analytics doc."""

    model_config = ConfigDict(populate_by_name=True)

    run_number: int = Field(default=0, alias="runNumber")
    issues: int = 0
    resolved: int = 0


class PRAnalyticsEntry(BaseModel):
    """Per-PR summary stored in the analytics doc."""

    model_config = ConfigDict(populate_by_name=True)

    pr_number: int = Field(alias="prNumber")
    owner: str = ""
    repo: str = ""
    repo_id: str = Field(default="", alias="repoId")
    created_at: Optional[str] = Field(None, alias="createdAt")
    last_reviewed_at: Optional[str] = Field(None, alias="lastReviewedAt")
    last_review_type: Optional[str] = Field(None, alias="lastReviewType")
    last_reviewed_sha: Optional[str] = Field(None, alias="lastReviewedSha")
    review_status: Optional[str] = Field(None, alias="reviewStatus")
    runs: list[RunAnalytics] = Field(default_factory=list)
    total_issues: int = Field(default=0, alias="totalIssues")
    total_resolved: int = Field(default=0, alias="totalResolved")
    positives: int = 0
    merged_at: Optional[str] = Field(None, alias="mergedAt")
    closed_at: Optional[str] = Field(None, alias="closedAt")


class RepoAnalytics(BaseModel):
    """Repo-level analytics doc stored at users/{uid}/repos/{owner}_{repo}/analytics."""

    model_config = ConfigDict(populate_by_name=True)

    owner: str
    repo_name: str = Field(alias="repoName")
    total_prs: int = Field(default=0, alias="totalPrs")
    total_reviews: int = Field(default=0, alias="totalReviews")
    total_issues_generated: int = Field(default=0, alias="totalIssuesGenerated")
    total_issues_resolved: int = Field(default=0, alias="totalIssuesResolved")
    total_positives: int = Field(default=0, alias="totalPositives")
    prs_per_week: float = Field(default=0.0, alias="prsPerWeek")
    addressed_rate: float = Field(default=0.0, alias="addressedRate")
    avg_merge_time_hours: float = Field(default=0.0, alias="avgMergeTimeHours")
    daily_breakdown: list[dict] = Field(default_factory=list, alias="dailyBreakdown")
    prs: dict[str, PRAnalyticsEntry] = Field(default_factory=dict)
    updated_at: str = Field(default="", alias="updatedAt")


class LinterToolConfig(BaseModel):
    """Configuration for a single linter tool."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    enabled: bool = True
    url: str = Field(default="", alias="url")
    extensions: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list, alias="configFiles")
    config_file: str = Field(default="", alias="configFile")


class ToolsConfig(BaseModel):
    """User's linter tools configuration. Stored at users/{uid}/config/tools."""

    model_config = ConfigDict(populate_by_name=True)

    ruff: LinterToolConfig = Field(
        default_factory=lambda: LinterToolConfig(
            name="Ruff",
            enabled=True,
            url="https://docs.astral.sh/ruff/",
            extensions=[".py", ".ipynb"],
            config_files=["pyproject.toml", "ruff.toml", ".ruff.toml"],
        ),
    )
    eslint: LinterToolConfig = Field(
        default_factory=lambda: LinterToolConfig(
            name="ESLint",
            enabled=True,
            url="https://eslint.org/",
            extensions=[".js", ".ts", ".cjs", ".mjs", ".d.cts", ".d.mts", ".jsx", ".tsx",
                        ".css", ".vue", ".svelte", ".astro", ".graphql", ".gql", ".mdx"],
            config_files=["eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
                          "eslint.config.ts", "eslint.config.mts", "eslint.config.cts",
                          ".eslintrc", ".eslintrc.js", ".eslintrc.cjs",
                          ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml"],
        ),
        alias="eslint",
    )
    golangci_lint: LinterToolConfig = Field(
        default_factory=lambda: LinterToolConfig(
            name="golangci-lint",
            enabled=True,
            url="https://golangci-lint.run/",
            extensions=[".go", ".go.mod"],
            config_files=[".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json"],
        ),
        alias="golangciLint",
    )


class PendingInstallation(BaseModel):
    """Pending GitHub App installation, stored until user signs up and it's linked.

    Document path: pending_installations/{github_username}
    Auto-expires via Firestore TTL on expiresAt field (30 days).
    """

    model_config = ConfigDict(populate_by_name=True)

    github_username: str = Field(serialization_alias="githubUsername")
    github_installation_id: int = Field(serialization_alias="githubInstallationId")
    github_account_id: int = Field(serialization_alias="githubAccountId")
    account_type: str = Field(serialization_alias="accountType")
    repository_selection: Optional[str] = Field(None, serialization_alias="repositorySelection")
    created_at: str = Field(serialization_alias="createdAt")
    expires_at: str = Field(serialization_alias="expiresAt")
