"""Shared utilities for normal and deep review pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from code_graph import (
    build_graph,
    changed_files_from_diff,
    clone_with_token,
    extract_pr_call_graph,
    parse_source_files,
    render_blast_radius_markdown,
)
from common.diff_parser import split_diff_by_file
from common.github_client import GitHubClient
from ncodereview.agent import build_user_message, build_verifier_task, create_verifier_agent
from ncodereview.artifacts import _dump_debug_artifacts, _save_stage, safe_serialize
from ncodereview.config import config
from ncodereview.llm import load_chat_model
from ncodereview.normalize import flatten_issues
from ncodereview.sandbox import (
    create_sandbox_with_repo,
    inject_call_graph,
    inject_diff,
    kill_sandbox,
)

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    """Review pipeline error."""


async def _get_review_diff(
    github: GitHubClient,
    review_mode: str,
    owner: str,
    repo: str,
    last_review_sha: str | None,
    head_sha: str,
    base_diff: str,
) -> str:
    if review_mode == "incremental" and last_review_sha and last_review_sha != head_sha:
        incremental = await github.get_incremental_diff(owner, repo, last_review_sha, head_sha)
        if incremental:
            return incremental
        logger.warning("Incremental diff failed, falling back to full diff")
    return base_diff


async def _create_sandbox(
    owner: str,
    repo: str,
    head_sha: str,
    head_branch: str,
    github_token: str,
    template: str | None = None,
) -> Any:
    return await create_sandbox_with_repo(
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        head_branch=head_branch,
        github_token=github_token,
        timeout=config.DEEPAGENT_SANDBOX_TIMEOUT,
        template=template,
    )


def _generate_call_graph(
    diff_text: str,
    github_token: str,
    owner: str,
    repo: str,
    head_sha: str,
) -> tuple[dict, str, str]:
    changed_files = changed_files_from_diff(diff_text)
    if not changed_files:
        return {}, "{}", "# Call Graph\n\nNo changed files found.\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        clone_with_token(github_token, f"{owner}/{repo}", head_sha, clone_path)
        files, parsed = parse_source_files(str(clone_path))
        graph = build_graph(str(clone_path), files, parsed)

    call_graph = extract_pr_call_graph(graph, changed_files)
    call_graph_json = json.dumps(call_graph, indent=2)
    blast_radius_md = render_blast_radius_markdown(call_graph)

    return call_graph, call_graph_json, blast_radius_md


def _slice_diff_for_batch(diff_text: str, batch_files: list[str]) -> str:
    patches_by_file = split_diff_by_file(diff_text)
    sliced_parts = []
    for file_path in batch_files:
        if file_path in patches_by_file:
            sliced_parts.append(patches_by_file[file_path])
    return "\n".join(sliced_parts)


async def _run_verifier_in_sandbox(
    sbx: Any,
    review_data: dict,
    review_diff_text: str,
) -> dict:
    raw_issues = review_data.get("issues", [])
    if not raw_issues:
        return review_data

    flat = flatten_issues(raw_issues)
    if not flat:
        return review_data

    user_msg = build_verifier_task(flat)
    run_limit = min(len(flat) * config.VERIFIER_RUN_LIMIT_MULTIPLIER, config.VERIFIER_RUN_LIMIT_MAX)
    verifier_agent = create_verifier_agent(
        model=load_chat_model(config.VERIFIER_MODEL),
        sbx=sbx,
        run_limit=run_limit,
    )

    logger.info("Running verifier in sandbox for %d findings (run_limit=%d)", len(flat), run_limit)

    try:
        result = await verifier_agent.ainvoke({
            "messages": [{"role": "user", "content": user_msg}],
        })
    except Exception as exc:
        logger.warning("Sandbox verifier failed — keeping all findings: %s", exc)
        return review_data

    verdicts_raw = result.get("structured_response") or result.get("content") or {}
    if hasattr(verdicts_raw, "model_dump"):
        verdicts_raw = verdicts_raw.model_dump()
    elif isinstance(verdicts_raw, str):
        try:
            import json as _json
            verdicts_raw = _json.loads(verdicts_raw)
        except (json.JSONDecodeError, TypeError):
            verdicts_raw = {}

    verdict_list = verdicts_raw.get("verdicts", []) if isinstance(verdicts_raw, dict) else []
    if not verdict_list:
        logger.info("Verifier returned no verdicts — keeping all findings")
        return review_data

    verdict_by_index: dict[int, dict] = {}
    for v in verdict_list:
        idx = v.get("index")
        if isinstance(idx, int) and 0 <= idx < len(flat):
            verdict_by_index[idx] = v

    kept_issues: list[dict] = []
    dropped_count = 0
    corrections_applied = 0
    for i, issue in enumerate(flat):
        v = verdict_by_index.get(i)
        if v and not v.get("keep", True):
            issue["classification"] = "false"
            issue["drop_reason"] = v.get("rationale", "")
            dropped_count += 1
        else:
            issue["classification"] = "valid"
            # Apply line range corrections if verifier provided them
            cls = v.get("corrected_line_start") if v else None
            if isinstance(cls, int) and cls > 0:
                issue["line_start"] = cls
                cle = v.get("corrected_line_end")
                if isinstance(cle, int) and cle > 0:
                    issue["line_end"] = cle
                ccs = v.get("corrected_code_snippet")
                if isinstance(ccs, str) and ccs:
                    issue["code_snippet"] = ccs
                corrections_applied += 1
            kept_issues.append(issue)

    if corrections_applied:
        logger.info("Verifier corrected line ranges for %d findings", corrections_applied)

    file_groups: dict[str, dict] = {}
    for issue in kept_issues:
        fp = issue.get("file", "")
        if fp not in file_groups:
            file_groups[fp] = {"file": fp, "issues": []}
        file_groups[fp]["issues"].append(issue)
    grouped_issues = list(file_groups.values())

    review_data["issues"] = grouped_issues
    review_data["_judgment_counts"] = {"kept": len(kept_issues), "dropped": dropped_count}

    if dropped_count > 0:
        logger.info("Verifier dropped %d/%d findings (sandbox)", dropped_count, len(flat))

    return review_data


def _dump_review_artifacts(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    call_graph_json: str,
    blast_radius_md: str,
    diff_text: str,
) -> None:
    logger.info("Review artifacts generated for %s/%s PR#%s", owner, repo, pr_number)
