import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Request

from api.services.cloud_tasks_service import CloudTasksService
from api.services.code_review_commands import extract_review_command, is_bot_mentioned
from common.firebase_service import firebase_service
from api.services.review_service import review_pipeline
from common.firebase_models import PrReviewStatus
from common.github_client import get_github_client
from common.job_models import (
    IncrementalPRPayload,
    IncrementalPushPayload,
    PRReviewPayload,
)

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
    """Ingest changed files when code is pushed directly to a branch.

    If the pushed branch has an open PR, ingestion is skipped — the PR review
    flow handles those pushes (triggered by an @bugviper mention on the PR).
    """
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

    # Extract branch name from ref (e.g. "refs/heads/pr-2" → "pr-2")
    branch = ref.removeprefix("refs/heads/")

    logger.info("Push: %s/%s %s (%s..%s)", owner, repo_name, ref, before_sha[:7], after_sha[:7])

    # If there's an open PR for this branch, skip graph ingestion.
    # The PR review pipeline owns those pushes — re-ingesting mid-PR would
    # corrupt the graph with an unmerged intermediate state.
    gh = get_github_client()
    if await gh.has_open_pr_for_branch(owner, repo_name, branch):
        logger.info(
            "Push to %s/%s branch '%s' has an open PR — skipping ingestion",
            owner,
            repo_name,
            branch,
        )
        return {
            "status": "ignored",
            "reason": f"branch '{branch}' has an open PR — ingestion skipped",
        }

    job_id = f"inc-push-{uuid.uuid4().hex[:12]}"

    if cloud_tasks.is_enabled:
        task_payload = IncrementalPushPayload(
            job_id=job_id,
            owner=owner,
            repo_name=repo_name,
            before_sha=before_sha,
            after_sha=after_sha,
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
            job_id=job_id,
            owner=owner,
            repo_name=repo_name,
            pr_number=pr_number,
        )
        cloud_tasks.dispatch_incremental_pr(task_payload)
    else:
        # Local dev only — ingestion_service code is not present in the Cloud Run image
        from db.client import get_neo4j_client
        from ingestion_service.core.incremental_updater import ingest_merged_pr

        background_tasks.add_task(ingest_merged_pr, owner, repo_name, pr_number, get_neo4j_client())

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

    repo_info = payload.get("repository", {})
    issue = payload.get("issue", {})
    comment = payload.get("comment", {})

    commenter_type = comment.get("user", {}).get("type", "")
    commenter_login = comment.get("user", {}).get("login", "")

    if commenter_type == "Bot" or "[bot]" in commenter_login:
        return {"status": "ignored", "reason": "comment from bot"}

    owner = repo_info.get("owner", {}).get("login", "")
    repo_name = repo_info.get("name", "")
    pr_number = issue.get("number")

    if not issue.get("pull_request"):
        return {"status": "ignored", "reason": "comment is not on a pull request"}

    comment_body = comment.get("body", "")
    uid = firebase_service.find_project_owner_id(owner)
    isRepoIndexed = firebase_service.checkIfRepoIndexedOrNot(uid=uid, owner=owner, repo=repo_name)

    if not isRepoIndexed:
        gh = get_github_client()
        await gh.post_comment(
            owner,
            repo_name,
            pr_number,
            "⚠️ **Repository not indexed.** "
            "Please ingest the repository before requesting reviews:\n\n"
            "1. Go to the BugViper dashboard:\n"
            "2. Find your project and click 'Ingest Repository'\n"
            "3. Wait for indexing, then try mentioning @bugviper again!",
        )
        return {"status": "ignored", "reason": "repository not indexed"}

    if not is_bot_mentioned(comment_body):
        return {"status": "ignored", "reason": "@bugviper not mentioned"}

    review_type = extract_review_command(comment_body)

    if review_type is None:
        gh = get_github_client()
        await gh.post_comment(
            owner,
            repo_name,
            pr_number,
            "❓ **Unrecognized command.** To trigger a review, mention @bugviper with:\n\n"
            "• `@bugviper review` — incremental review of new changes\n"
            "• `@bugviper full review` — complete review of all files",
        )
        return {"status": "ignored", "reason": "unrecognized command"}

    comment_id = comment.get("id")
    logger.info(
        "Review triggered: %s/%s#%s (type=%s, comment_id=%s)",
        owner,
        repo_name,
        pr_number,
        review_type.value,
        comment_id,
    )

    # Add snake reaction to show we're working on it
    gh = get_github_client()
    if comment_id:
        await gh.create_comment_reaction(owner, repo_name, comment_id, "rocket")
        logger.info(f"Added 🚀 reaction to comment {comment_id}")

    if uid:
        pr_meta = firebase_service.get_pr_metadata(uid, owner, repo_name, pr_number)
        if pr_meta and pr_meta.get("reviewStatus") == PrReviewStatus.RUNNING.value:
            logger.info(
                "Skipping review for %s/%s#%s — already running", owner, repo_name, pr_number
            )
            await gh.post_comment(
                owner,
                repo_name,
                pr_number,
                "⏳ **BugViper is already reviewing this PR.** "
                "Please wait until the current review completes before requesting another one!",
            )
            return {"status": "ignored", "reason": "review already running"}

    if cloud_tasks.review_is_enabled:
        review_payload = PRReviewPayload(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            review_type=review_type.value,
            comment_id=comment_id,
        )
        cloud_tasks.dispatch_pr_review(review_payload)
    else:
        background_tasks.add_task(
            review_pipeline,
            owner,
            repo_name,
            pr_number,
            review_type=review_type.value,
            comment_id=comment_id,
        )

    return {"status": "processing", "pr": f"{owner}/{repo_name}#{pr_number}", "action": "review"}


# ---------------------------------------------------------------------------
# GitHub Marketplace webhook
# ---------------------------------------------------------------------------

_MARKETPLACE_ACTIONS = {
    "purchased",
    "cancelled",
    "changed",
    "pending_change",
    "pending_change_cancelled",
}


@router.post("/marketplace")
async def on_marketplace(request: Request):
    """
    GitHub Marketplace webhook.

    Receives purchase lifecycle events and verifies the payload signature
    using HMAC-SHA256 (X-Hub-Signature-256 header).

    Supported actions: purchased, cancelled, changed,
                       pending_change, pending_change_cancelled.
    """
    body = await request.body()

    # ── Parse payload (JSON or form-encoded) ────────────────────────────────
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = json.loads(body)
    else:
        # application/x-www-form-urlencoded — GitHub sends `payload=<json>`
        form = await request.form()
        raw = form.get("payload", "")
        payload = json.loads(raw) if raw else {}

    action = payload.get("action", "")
    if action not in _MARKETPLACE_ACTIONS:
        return {"status": "ignored", "reason": f"unhandled action '{action}'"}

    purchase = payload.get("marketplace_purchase", {})
    account = purchase.get("account", {})
    plan = purchase.get("plan", {})
    sender = payload.get("sender", {})

    logger.info(
        "Marketplace %s: account=%s plan=%s sender=%s",
        action,
        account.get("login"),
        plan.get("name"),
        sender.get("login"),
    )

    return {
        "status": "received",
        "action": action,
        "account": account.get("login"),
        "plan": plan.get("name"),
    }
