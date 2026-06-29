"""Main entry point for the sandboxed DeepAgent review pipeline.

Called from the existing webhook handler when `USE_DEEPAGENT_REVIEW=true`.
Reuses the GitHub client and Firebase status updates already in the project;
the only thing new here is the e2b + deepagents orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path

from langchain_e2b import E2BSandbox

from common.github_client import get_github_client
from common.llm import load_chat_model
from ncodereview.config import config
from ncodereview.sandbox import create_sandbox_with_repo, kill_sandbox
from ncodereview.subagents import build_subagents
from ncodereview.tools import build_posting_tools

logger = logging.getLogger(__name__)


async def run_review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    review_type: str = "incremental_review",
    comment_id: int | None = None,
) -> None:
    """Run the sandboxed DeepAgent review.

    Mirrors the structure of the legacy `review_pipeline` so the two can
    coexist behind a flag: fetch PR data, run agent, post to GitHub, update
    Firebase. The agent lives inside an e2b sandbox; this function is the
    thin host-side orchestrator around it.
    """
    from common.firebase_models import PRMetadata, PrReviewStatus
    from common.firebase_service import firebase_service

    if not config.e2b_api_key:
        logger.error("E2B_API_KEY not set — cannot run deepagent review")
        return

    _ensure_env()
    import os

    os.environ["E2B_API_KEY"] = config.e2b_api_key
    _log_tracing_status()

    project_owner = firebase_service.find_project_owner_id(owner)
    repo_id = f"{owner}/{repo}"
    started_at = time.time()

    gh = get_github_client()
    try:
        gh.clear_pr_cache(owner, repo, pr_number)
    except Exception:
        pass

    review_dir = _ensure_debug_dir(owner, repo, pr_number)

    if project_owner:
        firebase_service.upsert_pr_metadata(
            project_owner,
            owner,
            repo,
            pr_number,
            PRMetadata(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                repo_id=repo_id,
                review_status=PrReviewStatus.RUNNING,
            ),
        )

    sbx = None
    try:
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )
        if not diff_text:
            logger.warning("Empty diff — skipping review")
            return

        pr_title = pr_info.get("title", "")
        head_branch = await _get_head_branch(gh, owner, repo, pr_number)

        pr_files_raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in _changed_files(diff_text)],
            return_exceptions=True,
        )
        pr_files = {
            fp: content
            for fp, content in zip(_changed_files(diff_text), pr_files_raw)
            if not isinstance(content, Exception) and content is not None
        }

        if review_dir:
            (review_dir / "00_diff.md").write_text(f"# Diff\n{pr_title}\n```diff\n{diff_text}\n```")

        github_token = await gh._get_installation_token(owner, repo)

        sbx = await create_sandbox_with_repo(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            head_branch=head_branch,
            github_token=github_token,
            timeout=config.deepagent_sandbox_timeout,
        )
        sbx.files.write("/home/user/review/diff.patch", diff_text)

        backend = E2BSandbox(sandbox=sbx)

        from deepagents import create_deep_agent


        model = load_chat_model(config.deepagent_model)
        subagents = build_subagents(model=model)
        tools = build_posting_tools(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            diff_text=diff_text,
            pr_files=pr_files,
            gh=gh,
            model_label=config.deepagent_model,
        )

        agent = create_deep_agent(
            model=model,
            backend=backend,
            tools=tools,
            subagents=subagents,
            system_prompt=_build_orchestrator_prompt(review_type),
        )

        user_msg = _build_user_message(pr_title=pr_title, pr_files=list(pr_files))
        logger.info(
            "Invoking DeepAgent for %s/%s#%s (files=%d, type=%s)",
            owner,
            repo,
            pr_number,
            len(pr_files),
            review_type,
        )
        result = await agent.ainvoke({"messages": [{"role": "user", "content": user_msg}]})

        if review_dir:
            messages = result.get("messages", [])
            (review_dir / "05_deepagent_messages.json").write_text(
                json.dumps(
                    [_serializable_message(m) for m in messages],
                    indent=2,
                    default=str,
                )
            )

        logger.info(
            "DeepAgent finished in %.1fs for %s/%s#%s",
            time.time() - started_at,
            owner,
            repo,
            pr_number,
        )

        if project_owner:
            firebase_service.upsert_pr_metadata(
                project_owner,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.COMPLETED,
                ),
            )

    except Exception as exc:
        logger.error("DeepAgent review failed:\n%s", traceback.format_exc())
        try:
            await gh.post_comment(
                owner,
                repo,
                pr_number,
                f"🚨 **BugViper DeepAgent Review Failed**\n\n`{exc or type(exc).__name__}`",
            )
        except Exception:
            pass
        if project_owner:
            firebase_service.upsert_pr_metadata(
                project_owner,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.FAILED,
                    failed_reasons=[str(exc)],
                ),
            )
    finally:
        kill_sandbox(sbx)


def _build_orchestrator_prompt(review_type: str) -> str:
    from ncodereview.prompts import ORCHESTRATOR_PROMPT

    extra = ""
    if review_type == "incremental_review":
        extra = (
            "\nThis is an INCREMENTAL review — focus on changes in the diff. "
            "Pre-existing issues outside the diff are out of scope.\n"
        )
    return ORCHESTRATOR_PROMPT + extra


def _build_user_message(pr_title: str, pr_files: list[str]) -> str:
    file_list = "\n".join(f"- `{f}`" for f in pr_files) or "- (none)"
    return (
        f"Please review this pull request.\n\n"
        f"**PR title:** {pr_title}\n\n"
        f"**Changed files ({len(pr_files)}):**\n{file_list}\n\n"
        f"Start by reading `/home/user/review/diff.patch` for the unified diff, then "
        f"explore the repo at `/home/user/workspace/repo` as needed."
    )


def _changed_files(diff_text: str) -> list[str]:
    from common.diff_parser import split_diff_by_file

    return list(split_diff_by_file(diff_text).keys())


async def _get_head_branch(gh, owner: str, repo: str, pr_number: int) -> str:
    try:
        pr = await gh._get_pr(owner, repo, pr_number)
        ref = pr.get("head", {}).get("ref", "")
        return ref or "main"
    except Exception:
        return "main"


def _ensure_env():
    """Load .env so all provider keys are available for the agent."""
    import os

    if not os.getenv("OPENROUTER_API_KEY") and not os.getenv("MINIMAX_API_KEY"):
        from dotenv import load_dotenv

        load_dotenv(override=True)


def _log_tracing_status() -> None:
    """Log whether LangSmith/LangChain tracing is hooked for this run."""
    import os

    tracing = (
        os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
        or os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    )
    project = os.getenv("LANGCHAIN_PROJECT") or os.getenv("LANGSMITH_PROJECT") or "default"
    if tracing and (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")):
        logger.info("LangSmith tracing ON — project=%s", project)
    else:
        logger.info(
            "LangSmith tracing OFF — set LANGCHAIN_TRACING_V2=true + "
            "LANGCHAIN_API_KEY=<key> in .env to enable"
        )


def _ensure_debug_dir(owner: str, repo: str, pr_number: int) -> Path | None:
    try:
        from common.debug_writer import make_review_dir

        return make_review_dir(owner, repo, pr_number)
    except Exception as exc:
        logger.warning("Could not create debug dir: %s", exc)
        return None


def _serializable_message(msg) -> dict:
    out: dict = {"type": type(msg).__name__}
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        out["content"] = content[:4000]
    elif isinstance(content, list):
        out["content"] = [str(c)[:500] for c in content]
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {"name": tc.get("name"), "args_preview": str(tc.get("args", ""))[:500]}
            for tc in tool_calls
        ]
    name = getattr(msg, "name", None)
    if name:
        out["tool_name"] = name
    return out


__all__ = ["run_review_pipeline"]


if __name__ == "__main__":
    from common.llm import load_chat_model
    from ncodereview.config import config
    from ncodereview.subagents import build_subagents

    assert config.deepagent_model, "deepagent_model must be set"
    resolved = load_chat_model(config.deepagent_model)
    subs = build_subagents(model=resolved)
    assert len(subs) == 3, f"expected 3 subagents, got {len(subs)}"
    names = {s["name"] for s in subs}
    assert names == {"correctness-reviewer", "security-auditor", "perf-reviewer"}, names

    from langchain.agents.structured_output import ToolStrategy

    from ncodereview.schemas import SubagentReviewPayload

    expected_rf = ToolStrategy(SubagentReviewPayload)
    for s in subs:
        assert s["response_format"] == expected_rf, s["name"]
        assert s["model"] is resolved, s["name"]

    print(
        f"OK: pipeline wiring valid — model={config.deepagent_model}, "
        f"subagents={sorted(names)}, "
        f"deepagent_review={'on' if config.use_deepagent_review else 'off'}"
    )
