
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from ai_code_review.batch import (
    _is_low_signal,
    _score_file,
    filter_call_graph_for_files,
    filter_blast_radius_for_files,
)
from ai_code_review.agent import build_user_message, create_specialized_agent, get_subagent_steps, merge_subagent_results
from ai_code_review.artifacts import _dump_debug_artifacts, _save_stage, safe_serialize
from ai_code_review.config import config, ensure_env
from ai_code_review.diff import get_changed_line_ranges
from ai_code_review.llm import load_chat_model
from ai_code_review.normalize import flatten_issues, normalize_and_validate_review_data, resolve_review_mode
from ai_code_review.pipeline_utils import (
    ReviewError,
    _create_sandbox,
    _dump_review_artifacts,
    _generate_call_graph,
    _get_review_diff,
    _run_verifier_in_sandbox,
    _slice_diff_for_batch,
)
from ai_code_review.prompts import CORRECTNESS_REVIEWER_PROMPT, PERF_REVIEWER_PROMPT, SECURITY_AUDITOR_PROMPT
from ai_code_review.result_merger import merge_batch_results
from ai_code_review.sandbox import inject_call_graph, inject_diff, kill_sandbox
from ai_code_review.tracking import get_last_review_sha, mark_review_failed, mark_review_running, upsert_repo_metadata

logger = logging.getLogger(__name__)


async def _run_deep_single_review(
    sbx: Any,
    pr_title: str,
    batch_files: list[str],
    blast_radius_md: str,
    review_type: str,
    review_mode: str = 'deep',
    run_limit: int = 30,
    line_ranges: dict[str, list[dict[str, int]]] | None = None,
) -> dict:
    user_msg = build_user_message(pr_title=pr_title, pr_files=batch_files, line_ranges=line_ranges)
    model = load_chat_model(config.DEEPAGENT_CODE_REVIEW_MODEL)

    agents_info = [
        ("correctness-reviewer", CORRECTNESS_REVIEWER_PROMPT, get_subagent_steps('bug', review_mode, len(batch_files))),
        ("security-auditor", SECURITY_AUDITOR_PROMPT, get_subagent_steps('security', review_mode, len(batch_files))),
        ("perf-reviewer", PERF_REVIEWER_PROMPT, get_subagent_steps('performance', review_mode, len(batch_files))),
    ]

    agents = [
        create_specialized_agent(model, sbx, prompt, name, limit)
        for name, prompt, limit in agents_info
    ]

    logger.info(
        "Invoking %d specialized agents for batch (files=%d, type=%s)",
        len(agents), len(batch_files), review_type,
    )

    results = await asyncio.gather(*[
        a.ainvoke({"messages": [{"role": "user", "content": user_msg}]})
        for a in agents
    ], return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.error("Specialized agent errors: %s", errors)

    valid = [r for r in results if not isinstance(r, Exception)]
    if not valid:
        return None

    merged = merge_subagent_results(valid)
    review_data = merged.model_dump() if hasattr(merged, "model_dump") else merged
    return review_data


async def _run_deep_review_with_batches(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    head_branch: str,
    github_token: str,
    pr_title: str,
    batches: list[list[str]],
    blast_radius_md: str,
    call_graph_json: str,
    diff_text: str,
    review_type: str,
    total_files: int = 0,
) -> dict:
    max_concurrent = config.MAX_CONCURRENT_SANDBOXES

    logger.info(
        "Starting deep review: %d batches with %d total files, max_concurrent=%d",
        len(batches), sum(len(b) for b in batches), max_concurrent,
    )

    async def run_single_batch(batch_idx: int, batch_files: list[str]) -> dict:
        if not batch_files:
            return {"file_based_issues": [], "file_based_positive_findings": [], "file_based_walkthrough": {}}

        sbx = None
        try:
            sandbox_template = (
                config.E2B_SANDBOX_TEMPLATE_LARGE if len(batch_files) > 1
                else config.E2B_SANDBOX_TEMPLATE_SMALL
            )

            sbx = await _create_sandbox(
                owner=owner, repo=repo, head_sha=head_sha, head_branch=head_branch,
                github_token=github_token, template=sandbox_template or None,
            )

            batch_blast_radius = filter_blast_radius_for_files(blast_radius_md, batch_files)
            batch_call_graph = filter_call_graph_for_files(call_graph_json, batch_files)
            batch_diff = _slice_diff_for_batch(diff_text, batch_files)
            batch_line_ranges = get_changed_line_ranges(batch_diff)
            inject_diff(sbx, batch_diff)
            inject_call_graph(sbx, batch_call_graph, batch_blast_radius)

            review_data = await _run_deep_single_review(
                sbx=sbx, pr_title=pr_title, batch_files=batch_files,
                blast_radius_md=batch_blast_radius, review_type=review_type,
                review_mode='deep', line_ranges=batch_line_ranges,
            )

            if review_data:
                _save_stage(owner, repo, pr_number, "agent_extracted", review_data)
                _save_stage(owner, repo, pr_number, "verifier_input",
                            flatten_issues(review_data.get("issues", [])))

                if review_data.get("issues"):
                    review_data = await _run_verifier_in_sandbox(
                        sbx=sbx, review_data=review_data, review_diff_text=batch_diff,
                    )
                    _save_stage(owner, repo, pr_number, "verifier_output",
                                safe_serialize(review_data))

            return review_data or {}
        finally:
            if sbx is not None:
                kill_sandbox(sbx)

    all_results: list[Any] = []
    for chunk_start in range(0, len(batches), max_concurrent):
        chunk = list(enumerate(batches))[chunk_start:chunk_start + max_concurrent]
        logger.info("Launching deep batch chunk %d/%d (%d batches)",
                     chunk_start // max_concurrent + 1,
                     (len(batches) + max_concurrent - 1) // max_concurrent,
                     len(chunk))
        chunk_tasks = [run_single_batch(idx, files) for idx, files in chunk]
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
        all_results.extend(chunk_results)

    errors = [r for r in all_results if isinstance(r, Exception)]
    if errors:
        logger.error("Deep batch review errors: %s", errors)

    valid_results = [r for r in all_results if not isinstance(r, Exception)]
    if not valid_results:
        raise ReviewError("All deep batch reviews failed")

    return merge_batch_results(valid_results)


async def run_deep_review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    review_type: str = "incremental_review",
    comment_id: int | None = None,
    uid: str | None = None,
) -> None:
    if not config.E2B_API_KEY:
        logger.error("E2B_API_KEY not set — cannot run deepagent review")
        return

    ensure_env()
    os.environ["E2B_API_KEY"] = config.E2B_API_KEY
    started_at = time.time()

    from common.github_client import get_github_client

    github = get_github_client()
    sbx = None

    try:
        from common.github_client import GitHubClient as _GC
        pr_data = await github.fetch_pr_data(owner, repo, pr_number)
        if not pr_data.difftext:
            raise ReviewError("Empty diff — nothing to review")

        pr_title = pr_data.prMeta.prTitle
        diff_review_mode = resolve_review_mode(review_type)
        last_review_sha = await get_last_review_sha(uid, owner, repo, pr_number)

        review_diff_text = await _get_review_diff(
            github, diff_review_mode, owner, repo, last_review_sha, pr_data.head_sha, pr_data.difftext,
        )

        if uid:
            for attempt in range(3):
                try:
                    repo_info = await github.get_repository_info(owner, repo)
                    await upsert_repo_metadata(uid, owner, repo, repo_info)
                    break
                except Exception as exc:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    logger.warning("Repo metadata unavailable after 3 attempts: %s", exc)

        mark_review_running(uid, owner, repo, pr_number, review_type)

        github_token = await github.get_installation_token(owner, repo)
        call_graph, call_graph_json, blast_radius_md = _generate_call_graph(
            pr_data.difftext, github_token, owner, repo, pr_data.head_sha,
        )

        deep_batch_size = 16
        pr_files = [f.filename for f in pr_data.files]
        if len(pr_files) <= deep_batch_size:
            batches = [pr_files]
        else:
            scores = {f: _score_file(f, call_graph) for f in pr_files}
            def sort_key(f):
                return (0 if not _is_low_signal(f) else 1, -scores.get(f, 0), f)
            sorted_files = sorted(pr_files, key=sort_key)
            batches = [sorted_files[i:i + deep_batch_size] for i in range(0, len(sorted_files), deep_batch_size)]

        is_batched = len(batches) > 1
        if is_batched:
            logger.info("Deep batching: %d files -> %d batches of %d",
                        len(pr_files), len(batches), deep_batch_size)

        total_files = len(pr_data.files)

        if is_batched:
            review_data = await _run_deep_review_with_batches(
                owner=owner, repo=repo, pr_number=pr_number,
                head_sha=pr_data.head_sha, head_branch=pr_data.head_branch,
                github_token=github_token, pr_title=pr_title,
                batches=batches, blast_radius_md=blast_radius_md,
                call_graph_json=call_graph_json, diff_text=pr_data.difftext,
                review_type=review_type, total_files=total_files,
            )
        else:
            sandbox_template = (
                config.E2B_SANDBOX_TEMPLATE_LARGE if total_files > 3
                else config.E2B_SANDBOX_TEMPLATE_SMALL
            )
            sbx = await _create_sandbox(
                owner=owner, repo=repo, head_sha=pr_data.head_sha,
                head_branch=pr_data.head_branch, github_token=github_token,
                template=sandbox_template or None,
            )
            inject_diff(sbx, review_diff_text)
            inject_call_graph(sbx, call_graph_json, blast_radius_md)

            batch_line_ranges = get_changed_line_ranges(review_diff_text)
            review_data = await _run_deep_single_review(
                sbx=sbx, pr_title=pr_title, batch_files=[f.filename for f in pr_data.files],
                blast_radius_md=blast_radius_md, review_type=review_type, review_mode='deep',
                line_ranges=batch_line_ranges,
            )

            if review_data and review_data.get("issues"):
                _save_stage(owner, repo, pr_number, "verifier_input",
                            flatten_issues(review_data.get("issues", [])))
                review_data = await _run_verifier_in_sandbox(
                    sbx=sbx, review_data=review_data, review_diff_text=review_diff_text,
                )

        _dump_review_artifacts(
            owner=owner, repo=repo, pr_number=pr_number, head_sha=pr_data.head_sha,
            call_graph_json=call_graph_json, blast_radius_md=blast_radius_md,
            diff_text=pr_data.difftext,
        )

        _dump_debug_artifacts(owner=owner, repo=repo, pr_number=pr_number,
                              review_data=review_data, review_diff_text=review_diff_text)

        review_data = normalize_and_validate_review_data(
            review_data=review_data, diff_text=review_diff_text,
            changed_files=[f.filename for f in pr_data.files],
        )

        from ai_code_review.comment import run_post_review
        await run_post_review(
            gh=github, owner=owner, repo=repo, pr_number=pr_number,
            head_sha=pr_data.head_sha, base_sha=pr_data.base_sha,
            pr_files={f.filename: f.fileContent for f in pr_data.files},
            diff_text=review_diff_text, review_data=review_data,
            uid=uid, review_type=review_type, started_at=started_at,
        )

    except ReviewError:
        raise
    except Exception as exc:
        logger.exception("Deep review pipeline failed")
        mark_review_failed(uid, owner, repo, pr_number, str(exc))
        raise
    finally:
        kill_sandbox(sbx)
