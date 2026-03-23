import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_current_user, get_neo4j_client
from api.models.schemas import (
    GitHubIngestRequest,
    IngestionJobResponse,
    JobStatusResponse,
)
from api.services.cloud_tasks_service import CloudTasksService
from api.services.firebase_service import firebase_service
from common.firebase_models import RepoIngestionError, RepoIngestionUpdate, RepoMetadata
from common.github_client import get_github_client
from common.job_models import (
    IngestionJobStats,
    IngestionTaskPayload,
    JobStatus,
)
from common.job_tracker import JobTrackerService
from db import Neo4jClient

logger = logging.getLogger(__name__)

router = APIRouter()
cloud_tasks = CloudTasksService()
job_tracker = JobTrackerService()


@router.post("/github", response_model=IngestionJobResponse)
async def ingest_github_repository(
    request: GitHubIngestRequest,
    neo4j_client: Neo4jClient = Depends(get_neo4j_client),
    user: dict = Depends(get_current_user),
):
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Authenticated user has no UID")

    # Prevent duplicate active jobs for the same repo.
    # PENDING/DISPATCHED jobs older than 10 min are considered stale (e.g. the
    # worker crashed before it could start) — mark them failed and proceed.
    existing = job_tracker.find_active_job(request.owner, request.repo_name)
    if existing:
        stale = False
        if existing.status in (JobStatus.PENDING, JobStatus.DISPATCHED):
            try:
                created_dt = datetime.fromisoformat(existing.created_at)
                age_minutes = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
                if age_minutes > 10:
                    job_tracker.update_status(
                        existing.job_id,
                        JobStatus.FAILED,
                        error_message="Job timed out waiting to start (stale)",
                    )
                    stale = True
                    logger.warning(
                        "Stale job %s marked FAILED (age=%.1f min)", existing.job_id, age_minutes
                    )
            except Exception:
                pass

        if not stale:
            return IngestionJobResponse(
                job_id=existing.job_id,
                status=existing.status.value,
                message=f"Ingestion already in progress for {request.owner}/{request.repo_name}",
                poll_url=f"/api/v1/ingest/jobs/{existing.job_id}",
            )

    # ── Fetch GitHub repo metadata ─────────────────────────────────────────
    gh_meta: dict = {}
    try:
        gh = get_github_client()
        gh_meta = await gh.get_repository_info(request.owner, request.repo_name)
    except Exception:
        logger.warning(
            "Could not fetch GitHub metadata for %s/%s", request.owner, request.repo_name
        )

    # ── Write initial repo doc to Firestore (status: pending) ─────────────
    try:
        firebase_service.upsert_repo_metadata(
            uid,
            request.owner,
            request.repo_name,
            RepoMetadata(
                owner=request.owner,
                repo_name=request.repo_name,
                full_name=gh_meta.get("full_name", f"{request.owner}/{request.repo_name}"),
                description=gh_meta.get("description"),
                language=gh_meta.get("language"),
                stars=gh_meta.get("stars", 0),
                forks=gh_meta.get("forks", 0),
                private=gh_meta.get("private", False),
                default_branch=gh_meta.get("default_branch", request.branch or "main"),
                size=gh_meta.get("size", 0),
                topics=gh_meta.get("topics", []),
                github_created_at=gh_meta.get("created_at"),
                github_updated_at=gh_meta.get("updated_at"),
                branch=request.branch,
                ingestion_status="pending",
            ),
        )
    except Exception as exc:
        logger.warning(
            "Failed to write initial Firestore repo doc (uid=%s owner=%s repo=%s branch=%s): %s",
            uid,
            request.owner,
            request.repo_name,
            request.branch,
            exc,
        )

    job_id = str(uuid.uuid4())
    payload = IngestionTaskPayload(
        job_id=job_id,
        owner=request.owner,
        repo_name=request.repo_name,
        branch=request.branch,
        clear_existing=request.clear_existing,
        uid=uid,
    )

    if not cloud_tasks.is_enabled:
        # Local dev — INGESTION_SERVICE_URL is unset so run in-process.
        # Uses BackgroundTasks so 200 is returned immediately and the frontend
        # can poll /jobs/{job_id} for progress — same UX as Cloud Tasks.
        from ingestion_service.core.repo_ingestion_engine import AdvancedIngestionEngine

        job_tracker.create_job(payload)

        async def _run_ingestion():
            job_tracker.update_status(job_id, JobStatus.RUNNING)
            try:
                engine = AdvancedIngestionEngine(neo4j_client)
                engine.setup()
                stats = await engine.ingest_github_repository(
                    owner=request.owner,
                    repo_name=request.repo_name,
                    branch=request.branch,
                    clear_existing=request.clear_existing,
                )
                engine.close()

                job_stats = IngestionJobStats(
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
                )
                job_tracker.update_status(job_id, JobStatus.COMPLETED, stats=job_stats)
                try:
                    firebase_service.upsert_repo_metadata(
                        uid,
                        request.owner,
                        request.repo_name,
                        RepoIngestionUpdate(
                            ingestion_status="ingested",
                            ingested_at=datetime.now(timezone.utc).isoformat(),
                            files_processed=stats.files_processed,
                            files_skipped=stats.files_skipped,
                            classes_found=stats.classes_found,
                            functions_found=stats.functions_found,
                            imports_found=stats.imports_found,
                            total_lines=stats.total_lines,
                        ),
                    )
                except Exception as fb_exc:
                    logger.warning("Firestore stats update failed: %s", fb_exc)
                logger.info("Local ingestion completed for %s/%s", request.owner, request.repo_name)

            except Exception as exc:
                logger.exception(
                    "Local ingestion failed for %s/%s", request.owner, request.repo_name
                )
                job_tracker.update_status(
                    job_id, JobStatus.FAILED, error_message=f"{type(exc).__name__}: {exc}"
                )
                try:
                    firebase_service.upsert_repo_metadata(
                        uid,
                        request.owner,
                        request.repo_name,
                        RepoIngestionError(ingestion_status="failed", error_message=str(exc)),
                    )
                except Exception as fb_exc:
                    logger.warning("Firestore error update failed: %s", fb_exc)

        asyncio.create_task(_run_ingestion())

        return IngestionJobResponse(
            job_id=job_id,
            status=JobStatus.PENDING.value,
            message=f"Ingestion started for {request.owner}/{request.repo_name}",
            poll_url=f"/api/v1/ingest/jobs/{job_id}",
        )

    else:
        # Create Firestore job record
        job_tracker.create_job(payload)

        # Dispatch to Cloud Tasks → ingestion service (uid is in payload for worker to use)
        task_name = cloud_tasks.dispatch_ingestion(payload)
        if task_name:
            job_tracker.update_status(job_id, JobStatus.DISPATCHED, cloud_task_name=task_name)
        else:
            job_tracker.update_status(
                job_id,
                JobStatus.FAILED,
                error_message="Failed to dispatch Cloud Task",
            )
            firebase_service.upsert_repo_metadata(
                uid,
                request.owner,
                request.repo_name,
                RepoIngestionError(
                    ingestion_status="failed",
                    error_message="Failed to dispatch Cloud Task",
                ),
            )
            raise HTTPException(status_code=500, detail="Failed to dispatch ingestion task")

        return IngestionJobResponse(
            job_id=job_id,
            status=JobStatus.PENDING.value,
            message=f"Ingestion queued for {request.owner}/{request.repo_name}",
            poll_url=f"/api/v1/ingest/jobs/{job_id}",
        )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    job = job_tracker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.job_id,
        owner=job.owner,
        repo_name=job.repo_name,
        branch=job.branch,
        status=job.status.value,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        stats=job.stats.model_dump() if job.stats else None,
        error_message=job.error_message,
    )


@router.post("/{owner}/{repo_name}/embed")
async def embed_repository(
    owner: str,
    repo_name: str,
    neo4j_client: Neo4jClient = Depends(get_neo4j_client),
    user: dict = Depends(get_current_user),
):
    """
    Re-run semantic embedding for an already-ingested repository without re-cloning.
    Safe to call multiple times — skips nodes that already have embeddings.
    Use this to recover from a failed embedding step after ingestion.
    """
    import os

    from common.embedder import embed_nodes_in_neo4j

    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY is not set")

    try:
        embed_stats = embed_nodes_in_neo4j(neo4j_client)
        nodes_embedded = sum(embed_stats.values())
        return {
            "repo_id": f"{owner}/{repo_name}",
            "nodes_embedded": nodes_embedded,
            "breakdown": {label: count for label, count in embed_stats.items() if count},
            "message": f"Embedded {nodes_embedded} nodes successfully"
            if nodes_embedded
            else "All nodes already had embeddings",
        }
    except Exception as exc:
        logger.exception("Re-embedding failed for %s/%s", owner, repo_name)
        raise HTTPException(status_code=500, detail=f"Embedding failed: {exc}")


@router.get("/jobs", response_model=list[JobStatusResponse])
async def list_jobs(limit: int = 20):
    jobs = job_tracker.list_jobs(limit=limit)
    return [
        JobStatusResponse(
            job_id=j.job_id,
            owner=j.owner,
            repo_name=j.repo_name,
            branch=j.branch,
            status=j.status.value,
            created_at=j.created_at,
            updated_at=j.updated_at,
            started_at=j.started_at,
            completed_at=j.completed_at,
            stats=j.stats.model_dump() if j.stats else None,
            error_message=j.error_message,
        )
        for j in jobs
    ]
