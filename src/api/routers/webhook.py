import json
import logging

from fastapi import APIRouter, Depends, Request

from api.middleware.webhook_auth import (
    verify_github_webhook_signature,
    verify_marketplace_webhook_signature,
)
from api.services.review_commands import (
    ReviewType,
    extract_review_command,
    format_help_text,
    is_bot_mentioned,
)
from common.firebase_models import PrReviewStatus
from common.firebase_service import firebase_service
from common.github_client import get_github_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/onComment", dependencies=[Depends(verify_github_webhook_signature)])
async def on_comment(request: Request):
    """
    Single GitHub webhook endpoint. Routes all events by X-GitHub-Event header:

    - issue_comment → run AI review when @bugviper is mentioned on a PR
    - push / pull_request → ingestion disabled (noop)
    - installation → handle app install/uninstall
    """
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "")

    if event_type == "issue_comment":
        return await _handle_comment_review(payload)

    if event_type in ("push", "pull_request"):
        return {"status": "ignored", "reason": "ingestion disabled"}

    if event_type == "installation":
        return await _handle_installation_event(payload)

    return {"status": "ignored", "reason": f"unhandled event '{event_type}'"}


async def _handle_resolve(
    uid: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    gh,
) -> dict:
    """Resolve all BugViper inline comments on a PR."""
    if not uid:
        return {"status": "ignored", "reason": "project owner not found in BugViper"}

    try:
        from common.firebase_service import firebase_service

        runs = firebase_service.get_all_review_runs(uid, owner, repo, pr_number)
    except Exception as exc:
        logger.warning("Could not fetch review runs for resolve: %s", exc)
        runs = []

    if not runs:
        await gh.post_comment(
            owner,
            repo,
            pr_number,
            "🔇 **BugViper**\n\nNo BugViper review comments found to resolve.",
        )
        return {"status": "ok", "action": "resolve", "resolved": 0}

    resolved_entries: list[dict] = []
    skipped = 0
    for run in runs:
        comment_ids = run.get("githubCommentIds", [])
        for entry in comment_ids:
            if entry.get("status") == "resolved":
                skipped += 1
                continue

            cid = entry.get("comment_id")
            tid = entry.get("thread_id")
            if cid is None:
                continue
            try:
                ok = await gh.resolve_pr_review_comment(owner, repo, pr_number, cid, tid)
                if ok:
                    resolved_entries.append(entry)
                else:
                    skipped += 1
            except Exception as exc:
                logger.warning("Failed to resolve comment %s: %s", cid, exc)
                skipped += 1

    resolved = len(resolved_entries)
    db_updated = 0
    if resolved_entries:
        try:
            db_updated = firebase_service.mark_review_comments_resolved(
                uid, owner, repo, pr_number, resolved_entries
            )
        except Exception as exc:
            logger.warning("Could not mark review comments resolved in Firebase: %s", exc)

    await gh.post_comment(
        owner,
        repo,
        pr_number,
        f"✅ **BugViper**\n\nResolved {resolved} comment thread{'s' if resolved != 1 else ''}."
        + (f" Updated {db_updated} database record{'s' if db_updated != 1 else ''}.")
        + (f" ({skipped} skipped)" if skipped else ""),
    )
    return {
        "status": "ok",
        "action": "resolve",
        "resolved": resolved,
        "db_updated": db_updated,
        "skipped": skipped,
    }


async def _handle_comment_review(payload: dict) -> dict:
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

    if not is_bot_mentioned(comment_body) and not comment_body.strip().startswith("@bugviper"):
        return {"status": "ignored", "reason": "@bugviper mentioned but not at start of comment"}

    uid = firebase_service.find_project_owner_id(owner)

    if uid is None:
        return {"status": "ignored", "reason": "project owner not found in BugViper"}

    review_type = extract_review_command(comment_body)

    if review_type is None:
        gh = get_github_client()
        await gh.post_comment(
            owner,
            repo_name,
            pr_number,
            "❓ **Unrecognized command.**\n\n" + format_help_text(),
        )
        return {"status": "ignored", "reason": "unrecognized command"}

    if review_type == ReviewType.HELP:
        gh = get_github_client()
        await gh.post_comment(owner, repo_name, pr_number, format_help_text())
        return {"status": "ok", "action": "help"}

    gh = get_github_client()

    if review_type == ReviewType.RESOLVE:
        return await _handle_resolve(uid, owner, repo_name, pr_number, gh)

    comment_id = comment.get("id")
    logger.info(
        "Review triggered: %s/%s#%s (type=%s, comment_id=%s)",
        owner,
        repo_name,
        pr_number,
        review_type.value,
        comment_id,
    )

    if comment_id:
        try:
            await gh.create_comment_reaction(owner, repo_name, comment_id, "rocket")
            logger.info(f"Added 🚀 reaction to comment {comment_id}")
        except Exception as exc:
            logger.warning("Failed to add rocket reaction to comment %s: %s", comment_id, exc)

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

    from ncodereview import config, run_review_pipeline, run_deep_review_pipeline

    if config.deepagent_review_mode == 'deep':
        await run_deep_review_pipeline(
            owner,
            repo_name,
            pr_number,
            review_type=review_type.value,
            comment_id=comment_id,
            uid=uid,
        )
    else:
        await run_review_pipeline(
            owner,
            repo_name,
            pr_number,
            review_type=review_type.value,
            comment_id=comment_id,
            uid=uid,
        )

    return {"status": "completed", "pr": f"{owner}/{repo_name}#{pr_number}", "action": "review"}


# ── GitHub App Installation Events ────────────────────────────────────────


async def _handle_installation_event(payload: dict) -> dict:
    """Handle installation.created and installation.deleted webhooks.

    - created: look up user by github_username, link or store as pending
    - deleted: remove installation_id from user doc
    """
    action = payload.get("action")
    installation = payload.get("installation", {})
    account = installation.get("account", {})

    installation_id = installation.get("id")
    github_username = account.get("login", "")
    account_id = account.get("id")
    account_type = account.get("type", "User")
    repo_selection = installation.get("repository_selection")

    logger.info(
        "Installation %s: id=%s account=%s type=%s",
        action,
        installation_id,
        github_username,
        account_type,
    )

    if action == "created":
        uid = firebase_service.find_project_owner_id(github_username)
        if uid:
            firebase_service.link_installation_to_user(
                uid=uid,
                installation_id=installation_id,
                account_id=account_id,
                account_type=account_type,
                repository_selection=repo_selection,
            )
            logger.info("Linked installation %s to existing user uid=%s", installation_id, uid)
        else:
            firebase_service.store_pending_installation(
                github_username=github_username,
                installation_id=installation_id,
                account_id=account_id,
                account_type=account_type,
                repository_selection=repo_selection,
            )
            logger.info(
                "Stored pending installation %s for github_username=%s",
                installation_id,
                github_username,
            )
        return {"status": "ok", "action": "created", "installation_id": installation_id}

    if action == "deleted":
        uid = firebase_service.find_project_owner_id(github_username)
        if uid:
            firebase_service.db.collection("users").document(uid).update(
                {
                    "githubInstallationId": None,
                    "githubAccountId": None,
                    "accountType": None,
                    "repositorySelection": None,
                }
            )
            logger.info("Removed installation %s from uid=%s", installation_id, uid)
        return {"status": "ok", "action": "deleted"}

    return {"status": "ignored", "reason": f"unhandled installation action '{action}'"}


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


@router.post("/marketplace", dependencies=[Depends(verify_marketplace_webhook_signature)])
async def on_marketplace(request: Request):
    """
    GitHub Marketplace webhook.

    Receives purchase lifecycle events and verifies the payload signature
    using HMAC-SHA256 (X-Hub-Signature-256 header).

    Supported actions: purchased, cancelled, changed,
                       pending_change, pending_change_cancelled.
    """
    body = await request.body()

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = json.loads(body)
    else:
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
