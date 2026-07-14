"""Model call limit middleware with a 'report' exit behavior.

Vendored from `langchain.agents.middleware.model_call_limit` (commit-pinned via
`uv.lock`) and extended with a third `exit_behavior="report"` mode.

In `"report"` mode, when the limit is hit and no structured response has been
captured yet, the middleware injects a strong `HumanMessage` and lets one more
model call run. If the model still hasn't invoked the structured output tool
after that final call (and didn't attempt it in the last message),
`after_model` force-sets `structured_response` via `_parse_with_schema({})`
— the exact same validation path used by the stock
`OutputToolBinding.parse` / `ToolStrategy`. If the model DID attempt the
tool but failed Pydantic validation, the `ToolStrategy` retry loop is left
to continue instead of forcing empty. This guarantees a valid Pydantic
instance (with all-default fields) instead of either:
  - exiting cold with an empty `structured_response` (`"end"`), or
  - crashing the run (`"error"`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.channels.untracked_value import UntrackedValue
from typing_extensions import NotRequired, override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    PrivateStateAttr,
    ResponseT,
    hook_config,
)
from langchain.agents.structured_output import _SchemaSpec, _parse_with_schema

if TYPE_CHECKING:
    from langgraph.runtime import Runtime


def _last_message_has_structured_tool_call(
    state: dict[str, Any], tool_name: str
) -> bool:
    """Check if the last AI message contains a tool call to the structured output tool.

    When true, the model attempted the tool but failed Pydantic validation —
    don't force an empty response because the `ToolStrategy` retry loop is
    already handling it. Only force empty when the model never even tried.
    """
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            return any(tc["name"] == tool_name for tc in msg.tool_calls)
    return False


_REPORT_NUDGE = (
    "STOP. This is your absolute final model call. "
    "You MUST invoke the FinalReviewOutput structured output tool NOW with all "
    "findings you have so far. Do not call any other tool. Do not output JSON as "
    "text or inside markdown fences — only a tool call is captured."
)


class ModelCallLimitState(AgentState[ResponseT]):
    """State schema for `ModelCallLimitMiddleware`.

    Extends `AgentState` with model call tracking fields.
    """

    thread_model_call_count: NotRequired[Annotated[int, PrivateStateAttr]]
    run_model_call_count: NotRequired[Annotated[int, UntrackedValue, PrivateStateAttr]]


def _build_limit_exceeded_message(
    thread_count: int,
    run_count: int,
    thread_limit: int | None,
    run_limit: int | None,
) -> str:
    exceeded_limits = []
    if thread_limit is not None and thread_count >= thread_limit:
        exceeded_limits.append(f"thread limit ({thread_count}/{thread_limit})")
    if run_limit is not None and run_count >= run_limit:
        exceeded_limits.append(f"run limit ({run_count}/{run_limit})")
    return f"Model call limits exceeded: {', '.join(exceeded_limits)}"


class ModelCallLimitExceededError(Exception):
    """Exception raised when model call limits are exceeded with `exit_behavior='error'`."""

    def __init__(
        self,
        thread_count: int,
        run_count: int,
        thread_limit: int | None,
        run_limit: int | None,
    ) -> None:
        self.thread_count = thread_count
        self.run_count = run_count
        self.thread_limit = thread_limit
        self.run_limit = run_limit
        msg = _build_limit_exceeded_message(thread_count, run_count, thread_limit, run_limit)
        super().__init__(msg)


class ModelCallLimitMiddleware(
    AgentMiddleware[ModelCallLimitState[ResponseT], ContextT, ResponseT]
):
    """Tracks model call counts and enforces limits.

    Adds a third `exit_behavior='report'` (vs. upstream's `'end'` / `'error'`):
    on the call that hits the limit, if `state['structured_response']` is unset
    AND the report nudge has not been sent yet, injects a `HumanMessage`
    telling the model to invoke the structured output tool on this final call
    and returns `None` so the model runs one more time. On the subsequent hit
    (the one-past-limit call) the middleware forces `jump_to='end'`. This
    gives the model a hard final chance to submit naturally.

    When `response_format` is provided and `exit_behavior='report'`,
    `after_model` force-sets `structured_response` via `_parse_with_schema({})`
    on the call where the nudge was already sent but the model did not invoke
    the structured output tool (checked via `_last_message_has_structured_tool_call`).
    If the model DID attempt the tool call but failed Pydantic validation, the
    `ToolStrategy` retry loop is left to continue instead of forcing empty.
    This uses the exact validation path as the stock `ToolStrategy` /
    `OutputToolBinding.parse`.

    If `state['structured_response']` is already set when the limit hits, the
    middleware jumps to end immediately (same as upstream `'end'`).
    """

    state_schema = ModelCallLimitState  # type: ignore[assignment]

    def __init__(
        self,
        *,
        thread_limit: int | None = None,
        run_limit: int | None = None,
        exit_behavior: Literal["end", "error", "report"] = "end",
        response_format: type | dict | None = None,
    ) -> None:
        super().__init__()

        if thread_limit is None and run_limit is None:
            raise ValueError("At least one limit must be specified (thread_limit or run_limit)")

        if exit_behavior not in {"end", "error", "report"}:
            raise ValueError(
                f"Invalid exit_behavior: {exit_behavior}. Must be 'end', 'error', or 'report'"
            )

        self.thread_limit = thread_limit
        self.run_limit = run_limit
        self.exit_behavior = exit_behavior
        self._report_nudge_sent = False
        self._spec = _SchemaSpec(response_format) if response_format is not None else None

    def reset_for_new_run(self) -> None:
        """Reset per-run state. Call from `before_agent` if you reuse instances."""
        self._report_nudge_sent = False

    @hook_config(can_jump_to=["end"])
    @override
    def before_model(
        self, state: ModelCallLimitState[ResponseT], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        thread_count = state.get("thread_model_call_count", 0)
        run_count = state.get("run_model_call_count", 0)

        thread_limit_exceeded = self.thread_limit is not None and thread_count >= self.thread_limit
        run_limit_exceeded = self.run_limit is not None and run_count >= self.run_limit

        if not (thread_limit_exceeded or run_limit_exceeded):
            return None

        if self.exit_behavior == "error":
            raise ModelCallLimitExceededError(
                thread_count=thread_count,
                run_count=run_count,
                thread_limit=self.thread_limit,
                run_limit=self.run_limit,
            )

        if self.exit_behavior == "end":
            limit_message = _build_limit_exceeded_message(
                thread_count, run_count, self.thread_limit, self.run_limit
            )
            return {"jump_to": "end", "messages": [AIMessage(content=limit_message)]}

        # exit_behavior == "report"
        if state.get("structured_response") is not None:
            return {
                "jump_to": "end",
                "messages": [AIMessage(content="Model call limit reached after submission.")],
            }

        if not self._report_nudge_sent:
            self._report_nudge_sent = True
            return {"messages": [HumanMessage(content=_REPORT_NUDGE)]}

        return {
            "jump_to": "end",
            "messages": [AIMessage(content="Model call limit reached; no structured response.")],
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: ModelCallLimitState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    @override
    def after_model(
        self, state: ModelCallLimitState[ResponseT], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        thread_count = state.get("thread_model_call_count", 0)
        run_count = state.get("run_model_call_count", 0)

        updates: dict[str, Any] = {
            "thread_model_call_count": thread_count + 1,
            "run_model_call_count": run_count + 1,
        }

        thread_limit_exceeded = self.thread_limit is not None and thread_count >= self.thread_limit
        run_limit_exceeded = self.run_limit is not None and run_count >= self.run_limit

        if (
            self.exit_behavior == "report"
            and self._spec is not None
            and self._report_nudge_sent
            and (thread_limit_exceeded or run_limit_exceeded)
            and state.get("structured_response") is None
            and not _last_message_has_structured_tool_call(state, self._spec.name)
        ):
            updates["structured_response"] = _parse_with_schema(
                self._spec.schema,
                self._spec.schema_kind,
                {},
            )

        return updates

    async def aafter_model(
        self,
        state: ModelCallLimitState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


__all__ = [
    "ModelCallLimitMiddleware",
    "ModelCallLimitState",
    "ModelCallLimitExceededError",
]
