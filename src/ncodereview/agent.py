"""Agent creation and prompt building utilities."""

from __future__ import annotations

from langchain_e2b import E2BSandbox

from ncodereview.schemas import FinalReviewOutput
from ncodereview.subagents import build_subagents


def create_deep_agent(model, sbx, review_type: str):
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend

    e2b_backend = E2BSandbox(sandbox=sbx)
    backend = CompositeBackend(
        default=e2b_backend,
        routes={},
        artifacts_root="/home/user/artifacts",
    )
    return create_deep_agent(
        model=model,
        backend=backend,
        tools=[],
        subagents=build_subagents(model=model, backend=backend),
        system_prompt=build_orchestrator_prompt(review_type),
        response_format=FinalReviewOutput,
    )


def build_orchestrator_prompt(review_type: str) -> str:
    from ncodereview.prompts import ORCHESTRATOR_PROMPT

    extra = ""
    if review_type == "incremental_review":
        extra = (
            "\nThis is an INCREMENTAL review — focus on changes in the diff. "
            "Pre-existing issues outside the diff are out of scope.\n"
        )
    return ORCHESTRATOR_PROMPT + extra


def build_user_message(pr_title: str, pr_files: list[str]) -> str:
    file_list = "\n".join(f"- `{f}`" for f in pr_files) or "- (none)"
    return (
        f"Please review this pull request.\n\n"
        f"**PR title:** {pr_title}\n\n"
        f"**Changed files ({len(pr_files)}):**\n{file_list}\n\n"
        f"Required protocol:\n"
        f"1. list `/home/user/workspace/repo`\n"
        f"2. read files from `/home/user/workspace/repo` to understand the code\n"
        f"3. Use available tools to traverse and understand the code structure\n"
    )
