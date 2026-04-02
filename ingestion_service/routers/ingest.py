import logging

from fastapi import APIRouter

from common.job_models import IngestionJobStats, IngestionTaskPayload, JobStatus
from common.job_tracker import JobTrackerService
from db.client import get_neo4j_client
from ingestion_service.core.repo_ingestion_engine import AdvancedIngestionEngine

logger = logging.getLogger(__name__)

router = APIRouter()
job_tracker = JobTrackerService()


@router.post("/tasks/ingest")
async def handle_ingestion_task(payload: IngestionTaskPayload):
    """Execute the ingestion pipeline for a single job.

    Always returns 200 to prevent Cloud Tasks from retrying on permanent failures.
    The job status in Firestore reflects the real outcome.
    """
    job_id = payload.job_id
    logger.info("Starting ingestion job %s for %s/%s", job_id, payload.owner, payload.repo_name)

    try:
        # Ensure the Firestore job document exists (it may not when called directly)
        if not job_tracker.get_job(job_id):
            job_tracker.create_job(payload)

        job_tracker.update_status(job_id, JobStatus.RUNNING)

        client = get_neo4j_client()
        engine = AdvancedIngestionEngine(client)
        engine.setup()

        stats = await engine.ingest_github_repository(
            owner=payload.owner,
            repo_name=payload.repo_name,
            branch=payload.branch,
            clear_existing=payload.clear_existing,
        )

        engine.close()

        job_tracker.update_status(
            job_id,
            JobStatus.COMPLETED,
            stats=IngestionJobStats(
                files_processed=stats.files_processed,
                files_skipped=stats.files_skipped,
                classes_found=stats.classes_found,
                functions_found=stats.functions_found,
                imports_found=stats.imports_found,
                total_lines=stats.total_lines,
                errors=stats.errors or [],
            ),
        )
        logger.info("Ingestion job %s completed successfully", job_id)

    except Exception as exc:
        logger.exception("Ingestion job %s failed", job_id)
        job_tracker.update_status(
            job_id,
            JobStatus.FAILED,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    # Always 200 — Cloud Tasks should not retry permanent failures
    return {"status": "processed", "job_id": job_id}
