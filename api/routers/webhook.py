import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Request

from api.services.cloud_tasks_service import CloudTasksService
from api.services.review_service import execute_pr_review
from common.job_models import IncrementalPRPayload, IncrementalPushPayload

logger = logging.getLogger(__name__)

router = APIRouter()
cloud_tasks = CloudTasksService()


@router.post("/onComment")
async def on_comment(request: Request, background_tasks: BackgroundTasks):
    """
    Single GitHub webhook endpoint. Routes all events by X-GitHub-Event header:

    - push          → ingest changed files into the graph
    - pull_request  → ingest changed files when a PR is merged
    - issue_comment → run AI review when @bugviper is mentioned on a PR
    """
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "")

    if event_type == "push":
        return await _handle_push(payload, background_tasks)

    if event_type == "pull_request":
        return await _handle_pr_merged(payload, background_tasks)

    if event_type == "issue_comment":
        return await _handle_comment_review(payload, background_tasks)

    return {"status": "ignored", "reason": f"unhandled event '{event_type}'"}



async def _handle_push(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Ingest changed files when code is pushed directly to a branch."""
    repo_info = payload.get("repository", {})
    owner = repo_info.get("owner", {}).get("login") or repo_info.get("owner", {}).get("name", "")
    repo_name = repo_info.get("name", "")
    ref = payload.get("ref", "")
    before_sha = payload.get("before", "")
    after_sha = payload.get("after", "")

    if after_sha == "0000000000000000000000000000000000000000":
        return {"status": "ignored", "reason": "branch deletion"}

    if before_sha == "0000000000000000000000000000000000000000":
        return {"status": "ignored", "reason": "new branch creation — use full ingestion"}

    logger.info("Push: %s/%s %s (%s..%s)", owner, repo_name, ref, before_sha[:7], after_sha[:7])

    job_id = f"inc-push-{uuid.uuid4().hex[:12]}"

    if cloud_tasks.is_enabled:
        task_payload = IncrementalPushPayload(
            job_id=job_id, owner=owner, repo_name=repo_name,
            before_sha=before_sha, after_sha=after_sha,
        )
        cloud_tasks.dispatch_incremental_push(task_payload)
    else:
        # Local dev only — ingestion_service code is not present in the Cloud Run image
        from db.client import get_neo4j_client
        from ingestion_service.core.incremental_updater import ingest_direct_push
        background_tasks.add_task(
            ingest_direct_push, owner, repo_name, before_sha, after_sha, get_neo4j_client()
        )

    return {
        "status": "processing",
        "job_id": job_id,
        "repo": f"{owner}/{repo_name}",
        "ref": ref,
        "commits": f"{before_sha[:7]}..{after_sha[:7]}",
    }


async def _handle_pr_merged(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Ingest changed files when a PR is merged."""
    action = payload.get("action", "")
    if action != "closed":
        return {"status": "ignored", "reason": f"action is '{action}', not 'closed'"}

    pr = payload.get("pull_request", {})
    if not pr.get("merged"):
        return {"status": "ignored", "reason": "PR closed but not merged"}

    repo_info = payload.get("repository", {})
    owner = repo_info.get("owner", {}).get("login", "")
    repo_name = repo_info.get("name", "")
    pr_number = pr.get("number")

    logger.info("PR merged: %s/%s#%s", owner, repo_name, pr_number)

    job_id = f"inc-pr-{uuid.uuid4().hex[:12]}"

    if cloud_tasks.is_enabled:
        task_payload = IncrementalPRPayload(
            job_id=job_id, owner=owner, repo_name=repo_name, pr_number=pr_number,
        )
        cloud_tasks.dispatch_incremental_pr(task_payload)
    else:
        # Local dev only — ingestion_service code is not present in the Cloud Run image
        from db.client import get_neo4j_client
        from ingestion_service.core.incremental_updater import ingest_merged_pr
        background_tasks.add_task(
            ingest_merged_pr, owner, repo_name, pr_number, get_neo4j_client()
        )

    return {
        "status": "processing",
        "job_id": job_id,
        "pr": f"{owner}/{repo_name}#{pr_number}",
        "action": "graph_update",
    }


async def _handle_comment_review(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Trigger an AI review when @bugviper is mentioned in a PR comment."""
    if payload.get("action") != "created":
        return {"status": "ignored", "reason": "not a new comment"}

    issue = payload.get("issue", {})
    if not issue.get("pull_request"):
        return {"status": "ignored", "reason": "comment is not on a pull request"}

    comment_body = payload.get("comment", {}).get("body", "")
    if "@bugviper" not in comment_body.lower():
        return {"status": "ignored", "reason": "no @bugviper mention"}

    repo_info = payload.get("repository", {})
    owner = repo_info.get("owner", {}).get("login", "")
    repo_name = repo_info.get("name", "")
    pr_number = issue.get("number")

    logger.info("Review triggered: %s/%s#%s", owner, repo_name, pr_number)

    background_tasks.add_task(execute_pr_review, owner, repo_name, pr_number)

    return {"status": "processing", "pr": f"{owner}/{repo_name}#{pr_number}", "action": "review"}
