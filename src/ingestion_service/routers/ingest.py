import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from common.firebase_service import firebase_service
from common.firebase_models import RepoIngestionError, RepoIngestionUpdate
from common.github_client import get_github_client
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

        gh_meta: dict = {}
        try:
            gh = get_github_client()
            gh_meta = await gh.get_repository_info(payload.owner, payload.repo_name)
        except Exception:
            logger.warning(
                "Could not fetch GitHub metadata for %s/%s", payload.owner, payload.repo_name
            )

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
                embedding_status=stats.embedding_status,
                nodes_embedded=stats.nodes_embedded,
                embedding_error=stats.embedding_error,
            ),
        )
        logger.info("Ingestion job %s completed successfully", job_id)

        if payload.uid:
            try:
                firebase_service.upsert_repo_metadata(
                    payload.uid,
                    payload.owner,
                    payload.repo_name,
                    RepoIngestionUpdate(
                        ingestion_status="ingested",
                        ingested_at=datetime.now(timezone.utc).isoformat(),
                        files_processed=stats.files_processed,
                        files_skipped=stats.files_skipped,
                        classes_found=stats.classes_found,
                        functions_found=stats.functions_found,
                        imports_found=stats.imports_found,
                        total_lines=stats.total_lines,
                        owner=payload.owner,
                        repo_name=payload.repo_name,
                        full_name=gh_meta.get("full_name", f"{payload.owner}/{payload.repo_name}"),
                        description=gh_meta.get("description"),
                        language=gh_meta.get("language"),
                        stars=gh_meta.get("stars", 0),
                        forks=gh_meta.get("forks", 0),
                        private=gh_meta.get("private", False),
                        default_branch=gh_meta.get("default_branch", payload.branch or "main"),
                        size=gh_meta.get("size", 0),
                        topics=gh_meta.get("topics", []),
                        github_created_at=gh_meta.get("created_at"),
                        github_updated_at=gh_meta.get("updated_at"),
                        branch=payload.branch,
                    ),
                )
            except Exception as fb_exc:
                logger.warning("Firestore stats update failed: %s", fb_exc)

    except Exception as exc:
        logger.exception("Ingestion job %s failed", job_id)
        job_tracker.update_status(
            job_id,
            JobStatus.FAILED,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        if payload.uid:
            try:
                firebase_service.upsert_repo_metadata(
                    payload.uid,
                    payload.owner,
                    payload.repo_name,
                    RepoIngestionError(
                        ingestion_status="failed",
                        error_message=f"{type(exc).__name__}: {exc}",
                    ),
                )
            except Exception as fb_exc:
                logger.warning("Firestore error update failed: %s", fb_exc)

    # Always 200 — Cloud Tasks should not retry permanent failures
    return {"status": "processed", "job_id": job_id}
