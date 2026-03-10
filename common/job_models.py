
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionJobStats(BaseModel):
    files_processed: int = 0
    files_skipped: int = 0
    classes_found: int = 0
    functions_found: int = 0
    imports_found: int = 0
    total_lines: int = 0
    errors: list[str] = Field(default_factory=list)
    # Embedding fields — "skipped" means OPENROUTER_API_KEY not set
    embedding_status: str = "skipped"   # "skipped" | "completed" | "failed"
    nodes_embedded: int = 0
    embedding_error: Optional[str] = None


class IngestionJob(BaseModel):
    job_id: str
    owner: str
    repo_name: str
    branch: Optional[str] = None
    clear_existing: bool = False
    status: JobStatus = JobStatus.PENDING
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stats: Optional[IngestionJobStats] = None
    error_message: Optional[str] = None
    cloud_task_name: Optional[str] = None


class IngestionTaskPayload(BaseModel):
    """Payload sent to the ingestion worker via Cloud Tasks."""

    job_id: str
    owner: str
    repo_name: str
    branch: Optional[str] = None
    clear_existing: bool = False
    pr_number: Optional[int] = None
    uid: Optional[str] = None  # Firebase UID — used to write repo metadata to Firestore on completion  


class IncrementalPRPayload(BaseModel):
    """Payload for incremental graph update triggered by a PR merge."""

    job_id: str
    owner: str
    repo_name: str
    pr_number: int


class IncrementalPushPayload(BaseModel):
    """Payload for incremental graph update triggered by a direct push."""

    job_id: str
    owner: str
    repo_name: str
    before_sha: str
    after_sha: str


class PRReviewPayload(BaseModel):
    """Payload dispatched to the review-service via Cloud Tasks."""

    owner: str
    repo: str
    pr_number: int
