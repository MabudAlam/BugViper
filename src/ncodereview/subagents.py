"""Specialized subagent definitions for the DeepAgent orchestrator.

Each subagent is focused, returns a structured `SubagentReviewPayload`, and
inherits the sandbox backend from the parent so it can read code via file
tools.
"""

from __future__ import annotations

from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from ncodereview.prompts import (
    CORRECTNESS_REVIEWER_PROMPT,
    PERF_REVIEWER_PROMPT,
    SECURITY_AUDITOR_PROMPT,
)
from ncodereview.schemas import SubagentReviewPayload


def build_subagents(model: BaseChatModel | str) -> list[dict]:
    """Return the list of declarative subagents.

    The main agent spawns these via `task(name=..., task=...)`. Each subagent
    has its own context, uses the parent's sandbox backend, and returns the
    same `SubagentReviewPayload` schema so the orchestrator can aggregate.

    `tools=[]` overrides the parent's tool inheritance so the host-side
    `submit_review` tool cannot be invoked by a subagent (which would skip
    the orchestrator's aggregation step and double-post to GitHub). Sandbox
    file tools are still available via the parent's E2BSandbox backend.

    `model` can be a pre-resolved `BaseChatModel` (preferred — bypasses the
    deepagents `init_chat_model` provider inference) or a string id.

    The `response_format` is wrapped in `ToolStrategy` so the schema is exposed
    as a tool call. `ProviderStrategy` (OpenAI structured outputs) is rejected
    by Anthropic/Bedrock which OpenRouter falls back to.
    """
    response_format = ToolStrategy(SubagentReviewPayload)
    return [
        {
            "name": "correctness-reviewer",
            "description": (
                "Reviews code for bugs, logic errors, edge cases, broken error "
                "handling, and race conditions. Use for the main correctness "
                "pass on the changed files."
            ),
            "system_prompt": CORRECTNESS_REVIEWER_PROMPT,
            "model": model,
            "tools": [],
            "response_format": response_format,
        },
        {
            "name": "security-auditor",
            "description": (
                "Audits code for security issues: injection, hardcoded secrets, "
                "auth/authz bypasses, SSRF, insecure deserialization, weak "
                "crypto, and sensitive data leaks."
            ),
            "system_prompt": SECURITY_AUDITOR_PROMPT,
            "model": model,
            "tools": [],
            "response_format": response_format,
        },
        {
            "name": "perf-reviewer",
            "description": (
                "Reviews code for performance issues: N+1 queries, unbounded "
                "loops, blocking calls in async code, missing pagination, "
                "and inefficient algorithms."
            ),
            "system_prompt": PERF_REVIEWER_PROMPT,
            "model": model,
            "tools": [],
            "response_format": response_format,
        },
    ]


__all__ = ["build_subagents", "SubagentReviewPayload"]
