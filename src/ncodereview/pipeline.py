"""Main entry point for the sandboxed DeepAgent review pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.diff_parser import split_diff_by_file
from common.github_client import GitHubClient, get_github_client
from knowledge_parser.call_graph import analyze_pr_call_graph, render_blast_radius_markdown
from knowledge_parser.knowledge_runner import (
    changed_files_from_diff,
    clone_with_token,
    parse_project,
)
from knowledge_parser.repo_graph import upload_pr_call_graph
from ncodereview.agent import build_user_message, create_deep_agent
from ncodereview.batch import batch_pr_files, filter_blast_radius_for_files
from ncodereview.config import config, ensure_env
from ncodereview.diff import get_changed_line_ranges
from ncodereview.types import GithubPrDetails
from ncodereview.llm import load_chat_model
from ncodereview.normalize import (
    extract_review_from_result,
    normalize_and_validate_review_data,
    resolve_review_mode,
)
from ncodereview.orchestrator import run_post_review
from ncodereview.result_merger import merge_batch_results
from ncodereview.sandbox import (
    create_sandbox_with_repo,
    inject_call_graph,
    inject_diff,
    inject_files,
    kill_sandbox,
)
from ncodereview.subagents import calculate_batch_tool_limits
from ncodereview.tracking import (
    get_last_review_sha,
    mark_review_failed,
    mark_review_running,
    upsert_repo_metadata,
)

logger = logging.getLogger(__name__)


def _dump_debug_artifacts(
    owner: str,
    repo: str,
    pr_number: int,
    review_data: dict,
    review_diff_text: str,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path("output") / f"review-{owner}-{repo}-pr{pr_number}-{ts}"
    out.mkdir(parents=True, exist_ok=True)

    try:
        (out / "diff.patch").write_text(review_diff_text)
    except Exception as exc:
        logger.warning("Failed to write diff debug artifact: %s", exc)

    try:
        (out / "raw_agent_output.json").write_text(json.dumps(review_data, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to write agent output debug artifact: %s", exc)

    try:
        raw_outputs = review_data.get("raw_agent_outputs") or {}
        for agent_name, raw_json in raw_outputs.items():
            (out / f"subagent_{agent_name}.json").write_text(
                raw_json if isinstance(raw_json, str) else json.dumps(raw_json, indent=2)
            )
    except Exception as exc:
        logger.warning("Failed to write subagent debug artifacts: %s", exc)

    logger.info("Debug artifacts dumped to %s", out)


###############################################################################
# Custom Exceptions
###############################################################################


class ReviewError(Exception):
    """Raised when a review precondition fails."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)




###############################################################################
# Step 2: Incremental Diff Calculation
###############################################################################


async def _get_review_diff(
    github: GitHubClient,
    review_mode: str,
    owner: str,
    repo: str,
    last_review_sha: str | None,
    head_sha: str,
    base_diff: str,
) -> str:
    """Determine which diff to use for review (full or incremental).

    Args:
        github: GitHub client
        review_mode: Review mode (full_review, incremental_review, etc)
        last_review_sha: SHA of last review (for incremental mode)
        head_sha: Current PR head SHA
        base_diff: Full PR diff

    Returns:
        The diff text to use for this review
    """
    if review_mode == "incremental" and last_review_sha and last_review_sha != head_sha:
        incremental = await github.get_incremental_diff(owner, repo, last_review_sha, head_sha)
        if incremental:
            return incremental
        logger.warning("Incremental diff failed, falling back to full diff")
    return base_diff


###############################################################################
# Step 3: Call Graph Generation
###############################################################################


def _generate_call_graph(
    diff_text: str,
    github_token: str,
    owner: str,
    repo: str,
    head_sha: str,
) -> tuple[dict, str, str]:
    """Generate call graph and blast radius markdown for PR files.

    Args:
        diff_text: The PR diff
        github_token: GitHub token for cloning
        owner: Repository owner
        repo: Repository name
        head_sha: PR head commit SHA

    Returns:
        Tuple of (call_graph_dict, call_graph_json, blast_radius_md)
    """
    changed_files = changed_files_from_diff(diff_text)
    if not changed_files:
        return {}, "{}", "# Call Graph\n\nNo changed files found.\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        clone_with_token(github_token, f"{owner}/{repo}", head_sha, clone_path)
        ast_data = parse_project(str(clone_path), owner, repo)

    call_graph = analyze_pr_call_graph(ast_data, changed_files)
    call_graph_json = json.dumps(call_graph, indent=2)
    blast_radius_md = render_blast_radius_markdown(call_graph)

    return call_graph, call_graph_json, blast_radius_md


###############################################################################
# Step 4: File Batching
###############################################################################


def _batch_pr_files_if_needed(
    call_graph: dict,
    pr_files: list[str],
    diff_text: str = "",
) -> tuple[list[list[str]], bool]:
    """Batch PR files if there are too many to process at once.

    Args:
        call_graph: The call graph dict
        pr_files: List of PR file paths
        diff_text: Full PR diff (for token-budget estimation)

    Returns:
        Tuple of (batches, is_batched) where is_batched=True if batching occurred
    """
    batches = batch_pr_files(call_graph, pr_files, diff_text=diff_text)
    is_batched = len(batches) > 1

    if is_batched:
        logger.info(
            "Batching review: %d files -> %d batches",
            len(pr_files),
            len(batches),
        )

    return batches, is_batched


###############################################################################
# Step 1: Sandbox Creation
###############################################################################


async def _create_sandbox(
    owner: str,
    repo: str,
    head_sha: str,
    head_branch: str,
    github_token: str,
    template: str | None = None,
) -> Any:
    """Create E2B sandbox with repository cloned.

    Args:
        owner: Repository owner
        repo: Repository name
        head_sha: PR head commit SHA
        head_branch: PR head branch name
        github_token: GitHub token for cloning
        template: E2B sandbox template name (e.g., 2vCPU or 4vCPU)

    Returns:
        E2B sandbox instance
    """
    return await create_sandbox_with_repo(
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        head_branch=head_branch,
        github_token=github_token,
        timeout=config.deepagent_sandbox_timeout,
        template=template,
    )


###############################################################################
# Step 6: Review Execution
###############################################################################


async def _run_single_review(
    sbx: Any,
    pr_title: str,
    batch_files: list[str],
    blast_radius_md: str,
    review_type: str,
    run_limit: int = 20,
    line_ranges: dict[str, list[dict[str, int]]] | None = None,
    use_generalist: bool = False,
) -> dict:
    """Run review for a single batch of files.

    Args:
        sbx: E2B sandbox instance
        pr_title: PR title
        batch_files: List of files in this batch
        blast_radius_md: Blast radius markdown (filtered for batch)
        review_type: Type of review to perform
        run_limit: Max tool calls per subagent
        line_ranges: Changed line ranges per file from diff
        use_generalist: If True, use generalist instead of 3 reviewers

    Returns:
        Review result dict
    """
    agent = create_deep_agent(
        model=load_chat_model(config.deepagent_model),
        sbx=sbx,
        review_type=review_type,
        run_limit=run_limit,
        use_generalist=use_generalist,
    )

    user_msg = build_user_message(pr_title=pr_title, pr_files=batch_files, line_ranges=line_ranges)
    logger.info(
        "Invoking DeepAgent for batch (files=%d, type=%s)",
        len(batch_files),
        review_type,
    )

    result = await agent.ainvoke({"messages": [{"role": "user", "content": user_msg}]})

    review_data = extract_review_from_result(result)
    if not review_data:
        raise ReviewError("Agent did not return valid review JSON")

    return review_data


def _slice_diff_for_batch(diff_text: str, batch_files: list[str]) -> str:
    """Extract diff sections only for files in this batch.

    Args:
        diff_text: Full unified diff
        batch_files: List of file paths in this batch

    Returns:
        Diff text containing only patches for the batch files
    """
    if not diff_text:
        return ""

    patches_by_file = split_diff_by_file(diff_text)

    sliced_parts = []
    for file_path in batch_files:
        if file_path in patches_by_file:
            sliced_parts.append(patches_by_file[file_path])

    return "\n".join(sliced_parts)


async def _run_review_with_batches(
    owner: str,
    repo: str,
    head_sha: str,
    head_branch: str,
    github_token: str,
    pr_title: str,
    batches: list[list[str]],
    blast_radius_md: str,
    diff_text: str,
    review_type: str,
    total_files: int,
) -> dict:
    """Run review across multiple batches in parallel, then merge results.

    Creates one sandbox per batch and runs all batches concurrently.
    Each batch only sees its slice of the diff and blast radius.

    Args:
        owner: Repository owner
        repo: Repository name
        head_sha: PR head commit SHA
        head_branch: PR head branch name
        github_token: GitHub token for cloning
        pr_title: PR title
        batches: List of file batches
        blast_radius_md: Full blast radius markdown (will be filtered per batch)
        diff_text: Full unified diff (will be sliced per batch)
        review_type: Type of review
        total_files: Total number of PR files (for generalist mode decision)

    Returns:
        Merged review result from all batches
    """
    use_generalist = sum(len(b) for b in batches) > 1
    if use_generalist:
        logger.info("PR has >1 files (%d) — using generalist agent", total_files)
    else:
        logger.info("PR has ≤1 files (%d) — using 3 specialized subagents", total_files)

    max_concurrent = config.max_concurrent_sandboxes

    logger.info(
        "Starting parallel review: %d batches with %d total files, max_concurrent=%d",
        len(batches),
        sum(len(b) for b in batches),
        max_concurrent,
    )

    async def run_single_batch(batch_idx: int, batch_files: list[str]) -> dict:
        if not batch_files:
            logger.warning("Batch %d has no files - skipping", batch_idx)
            return {
                "file_based_issues": [],
                "file_based_positive_findings": [],
                "file_based_walkthrough": {},
            }

        sbx = None
        try:
            batch_size = len(batch_files)
            sandbox_template = (
                config.e2b_sandbox_template_large
                if batch_size > 1
                else config.e2b_sandbox_template_small
            )

            sbx = await _create_sandbox(
                owner=owner,
                repo=repo,
                head_sha=head_sha,
                head_branch=head_branch,
                github_token=github_token,
                template=sandbox_template or None,
            )
            run_limit = calculate_batch_tool_limits(batch_size)
            logger.info(
                "Batch %d/%d: %d files, run_limit=%d",
                batch_idx + 1,
                len(batches),
                batch_size,
                run_limit,
            )

            batch_blast_radius = filter_blast_radius_for_files(blast_radius_md, batch_files)
            batch_diff = _slice_diff_for_batch(diff_text, batch_files)
            batch_line_ranges = get_changed_line_ranges(batch_diff)
            inject_files(sbx, batch_files, batch_blast_radius)
            inject_diff(sbx, batch_diff)

            result = await _run_single_review(
                sbx=sbx,
                pr_title=pr_title,
                batch_files=batch_files,
                blast_radius_md=batch_blast_radius,
                review_type=review_type,
                run_limit=run_limit,
                line_ranges=batch_line_ranges,
                use_generalist=use_generalist,
            )
            return result
        finally:
            if sbx is not None:
                kill_sandbox(sbx)

    # Process batches in chunks of max_concurrent so only N tasks
    # exist at a time — avoids overwhelming E2B with sandbox requests.
    all_results: list[Any] = []
    for chunk_start in range(0, len(batches), max_concurrent):
        chunk = list(enumerate(batches))[chunk_start : chunk_start + max_concurrent]
        logger.info(
            "Launching batch chunk %d/%d (%d batches)",
            chunk_start // max_concurrent + 1,
            (len(batches) + max_concurrent - 1) // max_concurrent,
            len(chunk),
        )
        chunk_tasks = [run_single_batch(batch_idx, batch_files) for batch_idx, batch_files in chunk]
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
        all_results.extend(chunk_results)

    errors = [r for r in all_results if isinstance(r, Exception)]
    if errors:
        logger.error("Batch review errors: %s", errors)
    valid_results = [r for r in all_results if not isinstance(r, Exception)]

    if not valid_results:
        raise ReviewError("All batch reviews failed")

    return merge_batch_results(valid_results)


###############################################################################
# Step 7: Upload Artifacts
###############################################################################


def _upload_review_artifacts(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    call_graph_json: str,
    blast_radius_md: str,
    diff_text: str,
) -> None:
    """Upload review artifacts to Firebase Storage.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
        head_sha: PR head SHA
        call_graph_json: Full call graph JSON string
        blast_radius_md: Blast radius markdown
        diff_text: PR diff text
    """
    logger.info("Uploading call graph artifacts to storage...")
    upload_pr_call_graph(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        sha=head_sha,
        call_graph_json=call_graph_json,
        blast_radius_md=blast_radius_md,
        diff_text=diff_text,
    )


###############################################################################
# Main Pipeline
###############################################################################


async def run_review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    review_type: str = "incremental_review",
    comment_id: int | None = None,
    uid: str | None = None,
) -> None:
    """Main entry point for the sandboxed DeepAgent review pipeline.

    Orchestrates the entire PR review process:
    1. Fetch PR data (diff, info, commits)
    2. Generate call graph and blast radius
    3. Batch files if too many
    4. Create sandbox with repo
    1. Run review (single or batched)
    6. Upload artifacts
    7. Post results to GitHub
    """
    # Validate environment
    if not config.e2b_api_key:
        logger.error("E2B_API_KEY not set — cannot run deepagent review")
        return

    ensure_env()
    os.environ["E2B_API_KEY"] = config.e2b_api_key

    started_at = time.time()

    github = get_github_client()
    sbx = None

    try:
        # Step 1: Fetch PR data
        pr_data = await github.fetch_pr_data(owner, repo, pr_number)
        if not pr_data.difftext:
            raise ReviewError("Empty diff — nothing to review")

        pr_title = pr_data.prMeta.prTitle
        review_mode = resolve_review_mode(review_type)
        last_review_sha = await get_last_review_sha(uid, owner, repo, pr_number)

        # Step 2: Determine review diff (full or incremental)
        review_diff_text = await _get_review_diff(
            github, review_mode, owner, repo, last_review_sha, pr_data.head_sha, pr_data.difftext
        )

        # Track repo metadata (non-critical — best-effort, retry transient failures)
        if uid:

            # In this step we would like to fetch the repository information and update the metadata in our system. We will attempt this up to 3 times in case of transient errors.
            for attempt in range(3):
                try:
                    repo_info = await github.get_repository_info(owner, repo)
                    await upsert_repo_metadata(uid, owner, repo, repo_info)
                    break
                except Exception as exc:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                        continue
                    logger.warning("Repo metadata unavailable after 3 attempts: %s", exc)

        # Mark the review as running so we can track status and avoid duplicate reviews.
        mark_review_running(uid, owner, repo, pr_number, review_type)

        # Step 3: Generate call graph and blast radius
        github_token = await github.get_installation_token(owner, repo)
        call_graph, call_graph_json, blast_radius_md = _generate_call_graph(
            pr_data.difftext, github_token, owner, repo, pr_data.head_sha
        )

        # Step 4: Batch files if needed
        batches, is_batched = _batch_pr_files_if_needed(
            call_graph, [f.filename for f in pr_data.files], diff_text=pr_data.difftext
        )

        # Step 5-6: Create sandbox and run review
        total_files = len(pr_data.files)

        if is_batched:
            _, call_graph_json, blast_radius_md = _generate_call_graph(
                pr_data.difftext, github_token, owner, repo, pr_data.head_sha
            )
            sbx = None

            review_data = await _run_review_with_batches(
                owner=owner,
                repo=repo,
                head_sha=pr_data.head_sha,
                head_branch=pr_data.head_branch,
                github_token=github_token,
                pr_title=pr_title,
                batches=batches,
                blast_radius_md=blast_radius_md,
                diff_text=pr_data.difftext,
                review_type=review_type,
                total_files=len(pr_data.files),
            )
        else:
            sandbox_template = (
                config.e2b_sandbox_template_large
                if total_files > 3
                else config.e2b_sandbox_template_small
            )
            sbx = await _create_sandbox(
                owner=owner,
                repo=repo,
                head_sha=pr_data.head_sha,
                head_branch=pr_data.head_branch,
                github_token=github_token,
                template=sandbox_template or None,
            )
            inject_diff(sbx, review_diff_text)
            inject_call_graph(sbx, call_graph_json, blast_radius_md)
            review_data = await _run_single_review(
                sbx=sbx,
                pr_title=pr_title,
                batch_files=[f.filename for f in pr_data.files],
                blast_radius_md=blast_radius_md,
                review_type=review_type,
                use_generalist=len(pr_data.files) > 1,
            )

        # Step 7: Upload artifacts
        _upload_review_artifacts(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_data.head_sha,
            call_graph_json=call_graph_json,
            blast_radius_md=blast_radius_md,
            diff_text=pr_data.difftext,
        )

        # Dump raw agent output before normalization (for debugging)
        _dump_debug_artifacts(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_data=review_data,
            review_diff_text=review_diff_text,
        )

        # Host-side judge: classify each finding against actual code
        from ncodereview.judge import judge_findings, summarize_judgment
        from ncodereview.schemas import SubagentReviewIssue
        from ncodereview.normalize import flatten_issues

        raw_issues = review_data.get("issues", [])
        if raw_issues:
            pr_files_dict = {f.filename: f.fileContent for f in pr_data.files}
            flat = flatten_issues(raw_issues)
            judge_input = [
                SubagentReviewIssue(**i) if isinstance(i, dict) else i
                for i in flat
            ]
            verdicts = await judge_findings(judge_input, pr_files_dict)
            verdict_map: dict[tuple[str, int, str], dict] = {}
            for v in verdicts:
                key = (v.get("file", ""), v.get("line_start", 0), v.get("category", ""))
                verdict_map[key] = v
            for issue in flat:
                key = (issue.get("file", ""), issue.get("line_start", 0), issue.get("category", ""))
                v = verdict_map.get(key)
                if v:
                    issue["classification"] = v.get("classification", "valid")
                    issue["drop_reason"] = v.get("drop_reason")
            review_data["issues"] = flat
            review_data["_judgment_counts"] = summarize_judgment(verdicts)

        # Validate and normalize results
        review_data = normalize_and_validate_review_data(
            review_data=review_data,
            diff_text=review_diff_text,
            changed_files=[f.filename for f in pr_data.files],
        )

        # Post results to GitHub
        stats = await run_post_review(
            gh=github,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_data.head_sha,
            base_sha=pr_data.base_sha,
            pr_files={f.filename: f.fileContent for f in pr_data.files},
            diff_text=review_diff_text,
            review_data=review_data,
            uid=uid,
            review_type=review_type,
            started_at=started_at,
        )

        logger.info(
            "Posted review for %s/%s#%s: %d inline, %d skipped, issues=%d, positives=%d",
            owner,
            repo,
            pr_number,
            stats["posted"],
            stats["skipped"],
            stats["issues_count"],
            stats["positives_count"],
        )

    except ReviewError as exc:
        logger.warning(
            "Review precondition failed for %s/%s#%s: %s",
            owner,
            repo,
            pr_number,
            exc.reason,
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
