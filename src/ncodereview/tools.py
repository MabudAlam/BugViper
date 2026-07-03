"""Host-side review posting — called from pipeline.py after the agent finishes.

This used to be a tool that subagents could accidentally invoke, posting
partial/empty results. Now it's a regular async function that the pipeline
calls once, with the orchestrator's final structured output.
"""

from __future__ import annotations

import logging
from typing import Any

from api.services.lint_service import run_lint
from api.utils.comment_formatter import format_inline_comment, format_review_summary
from common.diff_parser import (
    calculate_comment_line,
    extract_valid_diff_lines,
    snap_lines_to_diff,
    split_diff_by_file,
)
from common.github_client import GitHubClient

logger = logging.getLogger(__name__)

_MAX_COMMENT_RANGE = 15


async def post_review(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    gh: GitHubClient,
    pr_files: dict[str, str],
    diff_text: str,
    issues: list[dict],
    positives: list[str],
    walkthrough: list[dict],
    summary: str,
    raw_agent_outputs: dict[str, str] | None = None,
    judgment_counts: dict[str, int] | None = None,
) -> dict:
    """Post the review to GitHub — inline comments + PR review body.

    Line numbers from the agent are snapped to the nearest valid diff range
    so every comment lands on a line GitHub will accept.  ``post_inline_comment``
    adds a 3-attempt retry that collapses multi-line ranges to a single line
    if GitHub rejects the first attempt.  Ported from kodus-ai.
    """
    issue_models = _build_issue_models(issues)

    try:
        lint_results = await run_lint(pr_files)
        lint_issues = [i for i in lint_results if i.file in pr_files]
    except Exception as exc:
        logger.warning("Lint failed during review post: %s", exc)
        lint_issues = []

    walkthrough_lines = [
        f"{w.get('file', '?')} — {w.get('summary', '')}" for w in walkthrough
    ]

    from common.schemas import ReconciledReview

    summary_issue_models = [issue for issue in issue_models if issue.confidence >= 7]
    reconciled = ReconciledReview(
        issues=summary_issue_models,
        positive_findings=positives,
        summary=summary,
    )

    body = format_review_summary(
        reconciled,
        None,
        pr_number,
        lint_issues=lint_issues,
        walk_through=walkthrough_lines,
        inline_posted=0,
        inline_skipped=0,
        raw_agent_outputs=raw_agent_outputs or {},
        debug_info={"deepagent": True, "head_sha": head_sha},
        judgment_counts=judgment_counts,
    )

    has_blocking = any(
        i.category in ("bug", "security") and i.confidence >= 7
        for i in issue_models
    )
    event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

    patches_by_file = split_diff_by_file(diff_text)

    inline_posted = inline_skipped = 0
    github_comment_ids: list[dict] = []
    for issue in issue_models:
        if issue.status not in ("new", "still_open"):
            continue

        if issue.classification == "outside-diff":
            inline_skipped += 1
            continue

        valid_ranges = extract_valid_diff_lines(patches_by_file.get(issue.file))
        snapped = snap_lines_to_diff(
            issue.line_start,
            issue.line_end,
            valid_ranges,
        )
        if snapped is None:
            logger.warning(
                "post_review: %s has no valid diff ranges — skipping inline",
                issue.file,
            )
            inline_skipped += 1
            continue

        line_start, line_end = snapped
        line = calculate_comment_line(line_start, line_end, _MAX_COMMENT_RANGE)
        start_line = line_start if line != line_start else None

        if (
            issue.line_start != line_start
            or (issue.line_end or issue.line_start) != line_end
        ):
            logger.info(
                "post_review: snapped %s:%d-%d → %d-%d (comment at line=%s, start=%s)",
                issue.file, issue.line_start, issue.line_end or issue.line_start,
                line_start, line_end, line, start_line,
            )

        result = await gh.post_inline_comment(
            owner, repo, pr_number, head_sha, issue.file, line,
            format_inline_comment(issue),
            start_line=start_line,
        )
        if result.get("success"):
            inline_posted += 1
            cid = result.get("comment_id")
            tid = result.get("thread_id")
            if cid is not None:
                github_comment_ids.append({
                    "comment_id": cid,
                    "thread_id": tid,
                    "file": issue.file,
                    "line": line,
                    "title": issue.title,
                })
        else:
            inline_skipped += 1

    await gh.post_pr_review(owner, repo, pr_number, head_sha, body, event)

    return {
        "posted": inline_posted,
        "skipped": inline_skipped,
        "event": event,
        "issues_count": len(issue_models),
        "lint_count": len(lint_issues),
        "github_comment_ids": github_comment_ids,
    }


def _build_issue_models(raw_issues: list[dict]) -> list:
    from common.schemas import Issue

    models = []
    for issue in raw_issues:
        issue["status"] = issue.get("status", "new")
        if issue["status"] not in ("new", "still_open", "fixed"):
            issue["status"] = "new"
        confidence = _clamp(issue.get("confidence", 8))
        if not isinstance(issue.get("line_start"), int) or issue["line_start"] < 1:
            continue
        classification = issue.get("classification")
        if classification not in ("valid", "nitpick", "outside-diff", "false"):
            classification = None
        if classification == "false":
            continue  # ponytail: judge said drop; never reaches the PR
        try:
            models.append(Issue(
                file=issue.get("file", ""),
                line_start=issue["line_start"],
                line_end=issue.get("line_end"),
                title=issue.get("title", "Untitled issue"),
                category=issue.get("category", "bug"),
                severity=issue.get("severity", "medium"),
                issue_type=issue.get("issue_type", "Potential issue"),
                description=issue.get("description", ""),
                suggestion=issue.get("suggestion", ""),
                impact=issue.get("impact", ""),
                code_snippet=issue.get("code_snippet", ""),
                confidence=confidence,
                classification=classification,
                drop_reason=issue.get("drop_reason"),
                status=issue["status"],
            ))
        except Exception as exc:
            logger.warning("Skipping malformed issue: %s", exc)
    return models


def _clamp(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 8
    return max(0, min(10, n))
