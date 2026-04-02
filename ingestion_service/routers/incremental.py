import logging

from fastapi import APIRouter

from common.job_models import (
    IncrementalPRPayload,
    IncrementalPushPayload,
    IngestionTaskPayload,
    JobStatus,
)
from common.job_tracker import JobTrackerService
from db.client import get_neo4j_client
from ingestion_service.core.incremental_updater import ingest_direct_push, ingest_merged_pr

logger = logging.getLogger(__name__)

router = APIRouter()
job_tracker = JobTrackerService()


@router.post("/tasks/incremental-pr")
async def handle_pr_merge(payload: IncrementalPRPayload):
    """Ingest changed files for a merged PR.

    Always returns 200 to prevent Cloud Tasks from retrying on permanent failures.
    The job status in Firestore reflects the real outcome.
    """
    job_id = payload.job_id
    logger.info(
        "PR merge ingestion %s: %s/%s#%d",
        job_id,
        payload.owner,
        payload.repo_name,
        payload.pr_number,
    )

    if not job_tracker.get_job(job_id):
        job_tracker.create_job(
            IngestionTaskPayload(
                job_id=payload.job_id,
                owner=payload.owner,
                repo_name=payload.repo_name,
                branch=None,
                clear_existing=False,
                pr_number=payload.pr_number,
            )
        )

    try:
        job_tracker.update_status(job_id, JobStatus.RUNNING)

        stats = await ingest_merged_pr(
            owner=payload.owner,
            repo=payload.repo_name,
            pr_number=payload.pr_number,
            neo4j_client=get_neo4j_client(),
        )

        job_tracker.update_status(job_id, JobStatus.COMPLETED)
        logger.info(
            "PR merge ingestion %s done: added=%d modified=%d deleted=%d errors=%d",
            job_id,
            stats.files_added,
            stats.files_modified,
            stats.files_deleted,
            len(stats.errors),
        )

    except Exception as exc:
        logger.exception("PR merge ingestion %s failed", job_id)
        job_tracker.update_status(
            job_id, JobStatus.FAILED, error_message=f"{type(exc).__name__}: {exc}"
        )

    return {"status": "processed", "job_id": job_id}


@router.post("/tasks/incremental-push")
async def handle_direct_push(payload: IncrementalPushPayload):
    """Ingest changed files for a direct branch push.

    Always returns 200 to prevent Cloud Tasks from retrying on permanent failures.
    The job status in Firestore reflects the real outcome.
    """
    job_id = payload.job_id
    logger.info(
        "Push ingestion %s: %s/%s (%s..%s)",
        job_id,
        payload.owner,
        payload.repo_name,
        payload.before_sha[:7],
        payload.after_sha[:7],
    )

    if not job_tracker.get_job(job_id):
        job_tracker.create_job(
            IngestionTaskPayload(
                job_id=payload.job_id,
                owner=payload.owner,
                repo_name=payload.repo_name,
                branch=None,
                clear_existing=False,
            )
        )

    try:
        job_tracker.update_status(job_id, JobStatus.RUNNING)

        stats = await ingest_direct_push(
            owner=payload.owner,
            repo=payload.repo_name,
            before_sha=payload.before_sha,
            after_sha=payload.after_sha,
            neo4j_client=get_neo4j_client(),
        )

        job_tracker.update_status(job_id, JobStatus.COMPLETED)
        logger.info(
            "Push ingestion %s done: added=%d modified=%d deleted=%d errors=%d",
            job_id,
            stats.files_added,
            stats.files_modified,
            stats.files_deleted,
            len(stats.errors),
        )

    except Exception as exc:
        logger.exception("Push ingestion %s failed", job_id)
        job_tracker.update_status(
            job_id, JobStatus.FAILED, error_message=f"{type(exc).__name__}: {exc}"
        )

    return {"status": "processed", "job_id": job_id}
