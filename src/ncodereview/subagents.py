"""Subagent creation with step budgets and mode dispatch."""

from __future__ import annotations

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.subagents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.todo import TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from ncodereview.model_call_limit import ModelCallLimitMiddleware
from ncodereview.prompts import (
    CORRECTNESS_REVIEWER_PROMPT,
    GENERALIST_PROMPT,
    PERF_REVIEWER_PROMPT,
    SECURITY_AUDITOR_PROMPT,
)
from ncodereview.schemas import SubagentReviewPayload

# Step budgets per review mode
FAST_MODE_STEPS: dict[str, int] = {
    'generalist': 4,
    'bug': 4,
    'security': 3,
    'performance': 3,
}
NORMAL_MODE_STEPS: dict[str, int] = {
    'generalist': 30,
    'bug': 30,
    'security': 20,
    'performance': 20,
}
DEEP_MODE_STEPS = 100

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


def calculate_batch_tool_limits(files_in_batch: int, review_mode: str = 'normal') -> int:
    if review_mode == 'fast':
        base = max(FAST_MODE_STEPS.values())
    elif review_mode == 'deep':
        base = DEEP_MODE_STEPS
    else:
        base = max(NORMAL_MODE_STEPS.values())

    if review_mode == 'deep':
        return DEEP_MODE_STEPS

    BASELINE_FILES = 8
    STEPS_PER_EXTRA_FILE = 0.5

    if files_in_batch <= BASELINE_FILES:
        return base

    extra = round((files_in_batch - BASELINE_FILES) * STEPS_PER_EXTRA_FILE)
    return min(base + extra, DEEP_MODE_STEPS)


def get_subagent_steps(agent_name: str, review_mode: str, files_in_batch: int) -> int:
    if review_mode == 'deep':
        return DEEP_MODE_STEPS

    table = FAST_MODE_STEPS if review_mode == 'fast' else NORMAL_MODE_STEPS
    base = table.get(agent_name, 20)

    if review_mode == 'fast':
        return base

    BASELINE_FILES = 8
    STEPS_PER_EXTRA_FILE = 0.5

    if files_in_batch <= BASELINE_FILES:
        return base

    extra = round((files_in_batch - BASELINE_FILES) * STEPS_PER_EXTRA_FILE)
    return min(base + extra, DEEP_MODE_STEPS)


def _build_review_agent(
    name: str,
    description: str,
    system_prompt: str,
    model: BaseChatModel | str,
    backend: BackendProtocol,
    run_limit: int = 30,
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
            ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior="report"),
            FilesystemMiddleware(backend=backend),
            ToolCallLimitMiddleware(run_limit=300),
            TodoListMiddleware(system_prompt=CODE_REVIEW_TODO_PROMPT),
            summ,
            SummarizationToolMiddleware(summ),
        ],
        name=name,
        response_format=SubagentReviewPayload,
    )
    return {
        "name": name,
        "description": description,
        "runnable": runnable,
    }


def build_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    review_mode: str = 'normal',
    files_in_batch: int = 1,
    use_generalist: bool = False,
) -> list[dict]:
    if use_generalist:
        return _build_generalist_subagents(model, backend, review_mode, files_in_batch)
    return _build_full_subagents(model, backend, review_mode, files_in_batch)


def _build_full_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    review_mode: str,
    files_in_batch: int,
) -> list[dict]:
    return [
        _build_review_agent(
            "correctness-reviewer",
            "Reviews code for bugs, logic errors, edge cases, broken error "
            "handling, and race conditions.",
            CORRECTNESS_REVIEWER_PROMPT,
            model,
            backend,
            run_limit=get_subagent_steps('bug', review_mode, files_in_batch),
        ),
        _build_review_agent(
            "security-auditor",
            "Audits code for security issues: injection, hardcoded secrets, "
            "auth/authz bypasses, SSRF, insecure deserialization, weak "
            "crypto, and sensitive data leaks.",
            SECURITY_AUDITOR_PROMPT,
            model,
            backend,
            run_limit=get_subagent_steps('security', review_mode, files_in_batch),
        ),
        _build_review_agent(
            "perf-reviewer",
            "Reviews code for performance issues: N+1 queries, unbounded "
            "loops, blocking calls in async code, missing pagination, "
            "and inefficient algorithms.",
            PERF_REVIEWER_PROMPT,
            model,
            backend,
            run_limit=get_subagent_steps('performance', review_mode, files_in_batch),
        ),
    ]


def _build_generalist_subagents(
    model: BaseChatModel | str,
    backend: BackendProtocol,
    review_mode: str,
    files_in_batch: int,
) -> list[dict]:
    return [
        _build_review_agent(
            "generalist-reviewer",
            "Combined correctness, security, and performance review in a single pass.",
            GENERALIST_PROMPT,
            model,
            backend,
            run_limit=get_subagent_steps('generalist', review_mode, files_in_batch),
        ),
    ]
