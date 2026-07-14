"""Firebase tracking for review runs."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from common.firebase_models import PRMetadata, PrReviewStatus, RepoMetadata, ReviewRunData
from common.firebase_service import firebase_service
from ncodereview.schemas import RepoDetails

logger = logging.getLogger(__name__)


def mark_review_running(
    uid: str | None, owner: str, repo: str, pr_number: int, review_type: str
) -> None:
    if not uid:
        return
    try:
        firebase_service.mark_review_running(uid, owner, repo, pr_number, review_type)
    except Exception as exc:
        logger.warning("Could not mark review as running: %s", exc)


def mark_review_completed(
    uid: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    review_type: str,
    review_data: dict,
    started_at: float,
    head_sha: str,
    base_sha: str,
    github_comment_ids: list[dict],
) -> dict:
    from ncodereview.normalize import positives_to_strings

    issues = review_data.get("issues", [])
    positives = positives_to_strings(review_data.get("positives", []))
    walkthrough = review_data.get("walkthrough", [])

    now_iso = datetime.now(timezone.utc).isoformat()
    run_data = ReviewRunData(
        issues=issues,
        positive_findings=positives,
        summary=review_data.get("summary", ""),
        files_changed=review_data.get("files_changed", []),
        repo_id="",
        pr_number=pr_number,
        review_type=review_type,
        issues_count=len(issues),
        positives_count=len(positives),
        walkthrough_count=len(walkthrough),
        head_sha=head_sha,
        base_sha=base_sha,
        github_comment_ids=github_comment_ids,
        started_at=datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
        if started_at else None,
        ended_at=now_iso,
        duration_seconds=time.time() - started_at if started_at else None,
    )

    if uid:
        try:
            open_issue_count = len([
                i for i in issues if i.get("status") not in ("fixed", "resolved")
            ])
            firebase_service.mark_review_completed(
                uid=uid,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                review_type=review_type,
                issues_count=len(issues),
                positives_count=len(positives),
                walkthrough_count=len(walkthrough),
                open_issue_count=open_issue_count,
                run_data=run_data,
                head_sha=head_sha,
                base_sha=base_sha,
            )
        except Exception as exc:
            logger.warning("Could not save review run to Firebase: %s", exc)

    return {
        "issues_count": len(issues),
        "positives_count": len(positives),
        "walkthrough_count": len(walkthrough),
    }


def mark_review_failed(
    uid: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    reason: str,
) -> None:
    if not uid:
        return
    try:
        existing = firebase_service.get_pr_metadata(uid, owner, repo, pr_number)
        repo_id = existing.get("repoId", "") if existing else ""
        pr_data = PRMetadata(
            owner=owner, repo=repo, pr_number=pr_number, repo_id=repo_id,
            review_status=PrReviewStatus.FAILED, failed_reasons=[reason],
        )
        firebase_service.upsert_pr_metadata(uid, owner, repo, pr_number, pr_data)
    except Exception as exc:
        logger.warning("Could not mark review as failed in Firebase: %s", exc)


async def upsert_repo_metadata(
    uid: str,
    owner: str,
    repo: str,
    repo_info: RepoDetails,
) -> None:
    try:
        repo_data = RepoMetadata(
            owner=owner, repo_name=repo,
            full_name=repo_info.full_name or f"{owner}/{repo}",
            description=repo_info.description,
            language=repo_info.language,
            stars=repo_info.stars or 0,
            forks=repo_info.forks or 0,
            private=repo_info.private or False,
            default_branch=repo_info.default_branch or "main",
            size=repo_info.size or 0,
            topics=repo_info.topics or [],
            github_created_at=repo_info.created_at,
            github_updated_at=repo_info.updated_at,
            ingestion_status="pending",
        )
        firebase_service.upsert_repo_metadata(uid, owner, repo, repo_data)
    except Exception as exc:
        logger.warning("Could not upsert repo metadata: %s", exc)


async def get_last_review_sha(
    uid: str | None,
    owner: str,
    repo: str,
    pr_number: int,
) -> str | None:
    if not uid:
        return None
    try:
        pr_meta = firebase_service.get_pr_metadata(uid, owner, repo, pr_number)
        return pr_meta.get("lastReviewedSha") if pr_meta else None
    except Exception:
        return None
