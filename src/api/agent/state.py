"""State structures for the BugViper agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated

from api.models.rag import Source


def _merge_sources(current: list[Source], update: list[Source]) -> list[Source]:
    """Reducer: append new sources, deduplicating by (path, line_number)."""
    seen = {(s.path, s.line_number) for s in current}
    result = list(current)
    for s in update:
        key = (s.path, s.line_number)
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


@dataclass
class InputState:
    """The input state — what the outside world sends in."""

    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)
    """
    Conversation history. Accumulates:
      1. HumanMessage       — user question
      2. AIMessage          — agent reasoning + optional tool_calls
      3. ToolMessage(s)     — results from executed tools
      4. AIMessage          — final answer (no tool_calls)
    Steps 2-4 repeat until the agent is satisfied.
    """


@dataclass
class State(InputState):
    """Full agent state — extends InputState with runtime-managed fields."""

    is_last_step: IsLastStep = field(default=False)
    """
    Set to True by LangGraph when recursion_limit - 1 steps have been taken.
    The agent uses this to bail out gracefully instead of looping forever.
    """

    sources: Annotated[list[Source], _merge_sources] = field(default_factory=list)
    """
    File/symbol citations collected from tool outputs as the agent runs.
    Each tool batch appends new sources; the reducer deduplicates by (path, line_number).
    Read from result["sources"] after ainvoke — no post-processing needed.
    """
