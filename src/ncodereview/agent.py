from __future__ import annotations

import os

from langchain_e2b import E2BSandbox

from ncodereview.schemas import FinalReviewOutput
from ncodereview.subagents import build_subagents


def create_deep_agent(
    model,
    sbx,
    review_type: str,
    review_mode: str = 'normal',
    run_limit: int = 30,
    files_in_batch: int = 1,
    use_generalist: bool = False,
):
    """Create a DeepAgent with orchestrator + subagents.

    Args:
        model: The LLM model.
        sbx: E2B sandbox.
        review_type: Type of review (incremental_review, full_review).
        review_mode: One of 'fast', 'normal', 'deep'.
        run_limit: Max tool calls for the orchestrator.
        files_in_batch: Number of files in this batch (for adaptive step budget).
        use_generalist: If True, use generalist instead of 3 specialized agents.
    """
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
        subagents=build_subagents(
            model=model,
            backend=backend,
            review_mode=review_mode,
            files_in_batch=files_in_batch,
            use_generalist=use_generalist,
        ),
        system_prompt=build_orchestrator_prompt(
            review_type,
            use_generalist=use_generalist,
            review_mode=review_mode,
        ),
        response_format=FinalReviewOutput,
    )


def build_orchestrator_prompt(
    review_type: str,
    use_generalist: bool = False,
    review_mode: str = 'normal',
) -> str:
    """Build the orchestrator's system prompt.

    Selects the prompt base by dispatch policy and appends mode-specific guidance.
    """
    from ncodereview.prompts import GENERALIST_ORCHESTRATOR_PROMPT, ORCHESTRATOR_PROMPT

    if use_generalist:
        prompt = GENERALIST_ORCHESTRATOR_PROMPT
    else:
        prompt = ORCHESTRATOR_PROMPT

    # Append mode-specific instructions
    if review_mode == 'deep':
        prompt += (
            "\n\n<SpeedMode>DEEP REVIEW</SpeedMode> — Run ALL specialized subagents "
            "(correctness-reviewer, security-auditor, perf-reviewer) in parallel. "
            "Each subagent has up to 100 tool calls for thorough investigation.\n"
        )
    elif review_mode == 'fast':
        prompt += (
            "\n\n<SpeedMode>FAST REVIEW</SpeedMode> — Focus on the most impactful issues. "
            "Subagents have reduced tool budgets for quick scanning.\n"
        )
    else:
        prompt += (
            "\n\n<SpeedMode>NORMAL REVIEW</SpeedMode> — Standard depth review.\n"
        )

    if review_type == "incremental_review":
        prompt += (
            "\nThis is an INCREMENTAL review — focus on changes in the diff. "
            "Pre-existing issues outside the diff are out of scope.\n"
        )
    return prompt


def create_verifier_agent(model, sbx, run_limit=10):
    """Create a standalone verifier agent with sandbox tools (read_file, grep).

    Runs on the same runner with the same tools as the finder agent.
    """
    from deepagents.backends import CompositeBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from deepagents.middleware.subagents import create_agent
    from langchain.agents.middleware import ToolCallLimitMiddleware
    from langchain_e2b import E2BSandbox

    from ncodereview.model_call_limit import ModelCallLimitMiddleware
    from ncodereview.prompts import VERIFIER_SYSTEM_PROMPT
    from ncodereview.schemas import VerifierOutput

    e2b_backend = E2BSandbox(sandbox=sbx)
    backend = CompositeBackend(
        default=e2b_backend,
        routes={},
    )
    return create_agent(
        model=model,
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        tools=[],
        middleware=[
            ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior="report"),
            FilesystemMiddleware(backend=backend),
            ToolCallLimitMiddleware(run_limit=run_limit),
        ],
        name="verifier",
        response_format=VerifierOutput,
    )


def build_verifier_task(flat_issues: list[dict]) -> str:
    """Build the verifier task prompt with all findings inline."""
    from ncodereview.prompts import VERIFIER_TASK_PROMPT

    lines: list[str] = []
    for i, issue in enumerate(flat_issues):
        file = issue.get("file", "?")
        ls = issue.get("line_start", "?")
        le = issue.get("line_end", ls)
        cat = issue.get("category", "?")
        sev = issue.get("severity", "?")
        title = (issue.get("title") or "")[:120]
        desc = (issue.get("description") or "")[:300]
        snippet = (issue.get("code_snippet") or "")[:200]
        lines.append(
            f"[{i}] {file}:{ls}-{le} [{cat}/{sev}]: {title}\n"
            f"    description: {desc}\n"
            f"    code: {snippet}"
        )

    findings_block = "\n\n".join(lines) if lines else "No findings to verify."
    return VERIFIER_TASK_PROMPT.format(findings_block=findings_block)


def create_direct_generalist_agent(
    model,
    sbx,
    review_type: str,
    review_mode: str = 'normal',
    run_limit: int = 30,
    files_in_batch: int = 1,
):
    """Create a generalist reviewer agent directly — no orchestrator.

    The agent explores code with tools (read_file, grep, etc.), then returns
    a structured FinalReviewOutput via response_format. No JSON parsing
    needed — result["structured_response"] is always a valid Pydantic model.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend
    from langchain.agents.middleware import ToolCallLimitMiddleware

    from ncodereview.prompts import GENERALIST_PROMPT
    from ncodereview.schemas import FinalReviewOutput
    from ncodereview.model_call_limit import ModelCallLimitMiddleware
    # from langchain.agents.middleware import ModelCallLimitMiddleware

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
        system_prompt=GENERALIST_PROMPT,
        middleware=[
            ModelCallLimitMiddleware(
                run_limit=run_limit,
                exit_behavior="report",
                response_format=FinalReviewOutput,
            ),
            # ModelCallLimitMiddleware( run_limit=3, exit_behavior="end"),

            ToolCallLimitMiddleware(run_limit=300),
        ],
        response_format=FinalReviewOutput,
    )


MAX_RANGES_DISPLAY = 8


def build_user_message(
    pr_title: str,
    pr_files: list[str],
    line_ranges: dict[str, list[dict[str, int]]] | None = None,
) -> str:
    """Build user message in <ReviewJob> format."""
    if line_ranges is None:
        line_ranges = {}

    file_lines: list[str] = []
    for f in pr_files:
        ranges = line_ranges.get(f, [])
        if ranges:
            if len(ranges) > MAX_RANGES_DISPLAY:
                first = ranges[0]
                last = ranges[-1]
                summary = f"L{first['start']}-L{last['end']}, {len(ranges)} changes"
                file_lines.append(f"- `{f}` ({summary})")
            else:
                range_strs = [f"L{r['start']}-{r['end']}" for r in ranges]
                file_lines.append(f"- `{f}` ({', '.join(range_strs)})")
        else:
            file_lines.append(f"- `{f}` (new file)")

    file_list = "\n".join(file_lines) or "- (none)"
    return (
        f"<ReviewJob>\n"
        f"  <PRInfo>\n"
        f"    Title: {pr_title}\n"
        f"  </PRInfo>\n\n"
        f"  <Changes>\n"
        f"    The unified diff is at `/home/user/review/diff.patch`. Read it with `read_file` to see what changed.\n"
        f"  </Changes>\n\n"
        f"  <Brief>\n"
        f"    Review the files listed below. Only report issues on files in this batch.\n"
        f"    Files in this batch ({len(pr_files)}):\n"
        f"{file_list}\n\n"
        f"    The diff.patch contains changes ONLY for these files.\n"
        f"    When reading source files, use `read_file /home/user/workspace/repo/<file>`.\n"
        f"  </Brief>\n"
        f"</ReviewJob>"
    )
