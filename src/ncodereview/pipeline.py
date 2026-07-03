"""Main entry point for the sandboxed DeepAgent review pipeline."""

from __future__ import annotations

import logging
import os
import time
import traceback

from ncodereview.agent import build_user_message, create_deep_agent
from ncodereview.config import config, ensure_env
from ncodereview.github import ReviewGitHub
from ncodereview.llm import load_chat_model
from ncodereview.normalize import (
    extract_review_from_result,
    normalize_and_validate_review_data,
    resolve_review_mode,
)
from ncodereview.orchestrator import run_post_review
from ncodereview.sandbox import create_sandbox_with_repo, inject_diff, kill_sandbox
from ncodereview.tracking import (
    get_last_review_sha,
    mark_review_failed,
    mark_review_running,
    upsert_repo_metadata,
)

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def run_review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    review_type: str = "incremental_review",
    comment_id: int | None = None,
    uid: str | None = None,
) -> None:
    if not config.e2b_api_key:
        logger.error("E2B_API_KEY not set — cannot run deepagent review")
        return

    ensure_env()
    os.environ["E2B_API_KEY"] = config.e2b_api_key

    started_at = time.time()
    github = ReviewGitHub()
    sbx = None

    try:
        diff_text, pr_info, head_sha, base_sha, head_branch, pr_files = await github.fetch_pr_data(
            owner, repo, pr_number
        )
        if not diff_text:
            raise ReviewError("Empty diff — nothing to review")

        pr_title = pr_info.get("title", "")
        review_mode = resolve_review_mode(review_type)
        last_review_sha = await get_last_review_sha(uid, owner, repo, pr_number)

        incremental_diff_text = None
        if review_mode == "incremental" and last_review_sha and last_review_sha != head_sha:
            incremental_diff_text = await github.get_incremental_diff(
                owner, repo, last_review_sha, head_sha
            )

        review_diff_text = incremental_diff_text if incremental_diff_text else diff_text

        if uid:
            repo_info = await github.get_repository_info(owner, repo)
            await upsert_repo_metadata(uid, owner, repo, repo_info)

        mark_review_running(uid, owner, repo, pr_number, review_type)

        github_token = await github.get_installation_token(owner, repo)
        sbx = await create_sandbox_with_repo(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            head_branch=head_branch,
            github_token=github_token,
            timeout=config.deepagent_sandbox_timeout,
        )

        inject_diff(sbx, review_diff_text)

        agent = create_deep_agent(
            model=load_chat_model(config.deepagent_model),
            sbx=sbx,
            review_type=review_type,
        )

        user_msg = build_user_message(pr_title=pr_title, pr_files=list(pr_files))
        logger.info(
            "Invoking DeepAgent for %s/%s#%s (files=%d, type=%s)",
            owner, repo, pr_number, len(pr_files), review_type,
        )
        result = await agent.ainvoke({"messages": [{"role": "user", "content": user_msg}]})

        review_data = extract_review_from_result(result)
        if not review_data:
            raise ReviewError("Agent did not return valid review JSON")

        if not review_data.get("judge_verdict"):
            raise ReviewError(
                "judge_verdict missing — orchestrator must call judge-reviewer subagent "
                "and include the result as judge_verdict in the final JSON"
            )

        review_data = normalize_and_validate_review_data(
            review_data=review_data,
            diff_text=review_diff_text,
            changed_files=list(pr_files),
        )

        if not review_data.get("_saw_judge_classifications"):
            raise ReviewError(
                "Orchestrator did not classify findings via judge-reviewer subagent"
            )

        stats = await run_post_review(
            gh=github.gh,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_files=pr_files,
            diff_text=review_diff_text,
            review_data=review_data,
            uid=uid,
            review_type=review_type,
            started_at=started_at,
        )
        logger.info(
            "Posted review for %s/%s#%s: %d inline, %d skipped, issues=%d, positives=%d",
            owner, repo, pr_number,
            stats["posted"], stats["skipped"],
            stats["issues_count"], stats["positives_count"],
        )

    except ReviewError as exc:
        logger.warning(
            "Review precondition failed for %s/%s#%s: %s",
            owner, repo, pr_number, exc.reason,
        )
        await github.post_failure_comment(owner, repo, pr_number, exc.reason)
        mark_review_failed(uid, owner, repo, pr_number, exc.reason)

    except Exception as exc:
        logger.error("DeepAgent review failed:\n%s", traceback.format_exc())
        reason = str(exc) or type(exc).__name__
        await github.post_failure_comment(owner, repo, pr_number, reason)
        mark_review_failed(uid, owner, repo, pr_number, reason)

    finally:
        kill_sandbox(sbx)
