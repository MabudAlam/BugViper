"""Orchestrator-level helpers that stitch modules together."""

from __future__ import annotations

from ncodereview.comment import post_review
from ncodereview.normalize import positives_to_strings
from ncodereview.tracking import mark_review_completed


async def run_post_review(
    gh,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_files: dict[str, str],
    diff_text: str,
    review_data: dict,
    uid: str | None,
    review_type: str,
    started_at: float,
) -> dict:
    gh_stats = await post_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        gh=gh,
        pr_files=pr_files,
        diff_text=diff_text,
        issues=review_data.get("issues", []),
        positives=positives_to_strings(review_data.get("positives", [])),
        walkthrough=review_data.get("walkthrough", []),
        summary=review_data.get("summary", ""),
        raw_agent_outputs=review_data.get("raw_agent_outputs"),
        judgment_counts=review_data.get("_judgment_counts"),
    )
    fb_stats = mark_review_completed(
        uid=uid,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        review_type=review_type,
        review_data=review_data,
        started_at=started_at,
        head_sha=head_sha,
        base_sha=base_sha,
        github_comment_ids=gh_stats.get("github_comment_ids", []),
    )
    return {
        "posted": gh_stats.get("posted", 0),
        "skipped": gh_stats.get("skipped", 0),
        "event": gh_stats.get("event", "COMMENT"),
        "issues_count": fb_stats.get("issues_count", 0),
        "positives_count": fb_stats.get("positives_count", 0),
    }
