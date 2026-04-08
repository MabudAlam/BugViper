"""Firestore-backed job tracking for ingestion jobs."""

import logging
from datetime import datetime, timezone
from typing import Optional

from common.firebase_init import _initialize_firebase
from common.job_models import (
    IngestionJob,
    IngestionJobStats,
    IngestionTaskPayload,
    JobStatus,
)

logger = logging.getLogger(__name__)

COLLECTION = "ingestion_jobs"


class JobTrackerService:
    """CRUD operations on the ``ingestion_jobs`` Firestore collection."""

    def __init__(self):
        self._db = _initialize_firebase()

    def create_job(self, payload: IngestionTaskPayload) -> IngestionJob:
        """Create a new pending ingestion job and persist it."""
        now = datetime.now(timezone.utc).isoformat()
        job = IngestionJob(
            job_id=payload.job_id,
            owner=payload.owner,
            repo_name=payload.repo_name,
            branch=payload.branch,
            clear_existing=payload.clear_existing,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self._db.collection(COLLECTION).document(job.job_id).set(job.model_dump())
        logger.info("Created ingestion job %s for %s/%s", job.job_id, job.owner, job.repo_name)
        return job

    def get_job(self, job_id: str) -> Optional[IngestionJob]:
        """Fetch a job by ID. Returns ``None`` if not found."""
        doc = self._db.collection(COLLECTION).document(job_id).get()
        if not doc.exists:
            return None
        return IngestionJob(**doc.to_dict())

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        stats: Optional[IngestionJobStats] = None,
        error_message: Optional[str] = None,
        cloud_task_name: Optional[str] = None,
    ) -> None:
        """Update job status and optional metadata fields."""
        now = datetime.now(timezone.utc).isoformat()
        data: dict = {"status": status.value, "updated_at": now}

        if status == JobStatus.RUNNING:
            data["started_at"] = now
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            data["completed_at"] = now
        if stats is not None:
            data["stats"] = stats.model_dump()
        if error_message is not None:
            data["error_message"] = error_message
        if cloud_task_name is not None:
            data["cloud_task_name"] = cloud_task_name

        # set(merge=True) is an upsert — safe even if the document doesn't exist yet.
        # This handles the Cloud Tasks path where the ingestion service receives a
        # job ID that was never written to Firestore by the API service.
        self._db.collection(COLLECTION).document(job_id).set(data, merge=True)
        logger.info("Job %s → %s", job_id, status.value)

    def list_jobs(self, limit: int = 20) -> list[IngestionJob]:
        """Return the most recent jobs, ordered by creation time descending."""
        docs = (
            self._db.collection(COLLECTION)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .stream()
        )
        return [IngestionJob(**doc.to_dict()) for doc in docs]

    def find_active_job(self, owner: str, repo_name: str) -> Optional[IngestionJob]:
        """Find a non-terminal job for the given owner/repo (prevent duplicates)."""
        active_statuses = [
            JobStatus.PENDING.value,
            JobStatus.DISPATCHED.value,
            JobStatus.RUNNING.value,
        ]
        docs = (
            self._db.collection(COLLECTION)
            .where("owner", "==", owner)
            .where("repo_name", "==", repo_name)
            .where("status", "in", active_statuses)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return IngestionJob(**doc.to_dict())
        return None
