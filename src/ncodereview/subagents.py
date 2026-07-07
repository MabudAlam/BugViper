from __future__ import annotations

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.subagents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.todo import TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from ncodereview.prompts import (
    CORRECTNESS_REVIEWER_PROMPT,
    GENERALIST_PROMPT,
    JUDGE_REVIEWER_PROMPT,
    PERF_REVIEWER_PROMPT,
    SECURITY_AUDITOR_PROMPT,
)
from ncodereview.schemas import JudgeVerdict, SubagentReviewPayload

RUN_LIMIT = 20
SUMMARIZATION_TRIGGER_TOKENS = 15000
SUMMARIZATION_KEEP_TOKENS = 5000

CODE_REVIEW_TODO_PROMPT = """## Coverage todo list

Use the `write_todos` tool to track which files you have reviewed. Before starting,
list every file from your assigned batch as a `pending` todo item.

For each file:
1. Mark it `in_progress` before you start reading it
2. After finding issues (or confirming none exist), mark it `completed`
3. Move to the next file

Do NOT skip files. Every file in the batch must be ticked `completed` before you
finalize your response. If you run out of tool calls before covering all files,
report whatever findings you have and note which files were not reviewed."""


def calculate_batch_tool_limits(files_in_batch: int) -> int:
    base = 40 if files_in_batch <= 3 else 60
    extra = max(0, (files_in_batch - 8) * 4)
    return min(base + extra, 120)


def _build_review_agent(
    name: str,
    description: str,
    system_prompt: str,
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int = 20,
) -> dict:
    from deepagents.middleware.summarization import (
        SummarizationMiddleware,
        SummarizationToolMiddleware,
    )

    summ = SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", SUMMARIZATION_TRIGGER_TOKENS),
        keep=("tokens", SUMMARIZATION_KEEP_TOKENS),
    )
    runnable = create_agent(
        model=model,
        system_prompt=system_prompt,
        tools=[],
        middleware=[
            FilesystemMiddleware(backend=backend),
            ToolCallLimitMiddleware(run_limit=run_limit),
            TodoListMiddleware(system_prompt=CODE_REVIEW_TODO_PROMPT),
            summ,
            SummarizationToolMiddleware(summ),
        ],
        name=name,
        response_format=SubagentReviewPayload,
    )  # ty:ignore[no-matching-overload]
    return {
        "name": name,
        "description": description,
        "runnable": runnable,
    }


def _build_judge_agent(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int = 20,
) -> dict:
    from deepagents.middleware.summarization import (
        SummarizationMiddleware,
        SummarizationToolMiddleware,
    )

    summ = SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", SUMMARIZATION_TRIGGER_TOKENS),
        keep=("tokens", SUMMARIZATION_KEEP_TOKENS),
    )
    runnable = create_agent(
        model=model,
        system_prompt=JUDGE_REVIEWER_PROMPT,
        tools=[],
        middleware=[
            FilesystemMiddleware(backend=backend),
            ToolCallLimitMiddleware(run_limit=run_limit),
            TodoListMiddleware(system_prompt=CODE_REVIEW_TODO_PROMPT),
            summ,
            SummarizationToolMiddleware(summ),
        ],
        name="judge-reviewer",
        response_format=JudgeVerdict,
    )  # ty:ignore[no-matching-overload]
    return {
        "name": "judge-reviewer",
        "description": (
            "Validates raw findings from the review subagents against the "
            "actual code in the sandbox. Returns per-finding classification: "
            "valid | nitpick | outside-diff | false. Used after review subagents."
        ),
        "runnable": runnable,
    }


def build_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int = 20,
    use_generalist: bool = False,
) -> list[dict]:
    if use_generalist:
        return _build_generalist_subagents(model, backend, run_limit)
    return _build_full_subagents(model, backend, run_limit)


def _build_full_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int,
) -> list[dict]:
    return [
        _build_review_agent(
            "correctness-reviewer",
            "Reviews code for bugs, logic errors, edge cases, broken error "
            "handling, and race conditions.",
            CORRECTNESS_REVIEWER_PROMPT,
            model,
            backend,
            run_limit=run_limit,
        ),
        _build_review_agent(
            "security-auditor",
            "Audits code for security issues: injection, hardcoded secrets, "
            "auth/authz bypasses, SSRF, insecure deserialization, weak "
            "crypto, and sensitive data leaks.",
            SECURITY_AUDITOR_PROMPT,
            model,
            backend,
            run_limit=run_limit,
        ),
        _build_review_agent(
            "perf-reviewer",
            "Reviews code for performance issues: N+1 queries, unbounded "
            "loops, blocking calls in async code, missing pagination, "
            "and inefficient algorithms.",
            PERF_REVIEWER_PROMPT,
            model,
            backend,
            run_limit=run_limit,
        ),
        _build_judge_agent(
            model,
            backend,
            run_limit=run_limit,
        ),
    ]


def _build_generalist_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int,
) -> list[dict]:
    return [
        _build_review_agent(
            "generalist-reviewer",
            "Combined correctness, security, and performance review in a single pass.",
            GENERALIST_PROMPT,
            model,
            backend,
            run_limit=run_limit,
        ),
        _build_judge_agent(
            model,
            backend,
            run_limit=run_limit,
        ),
    ]
