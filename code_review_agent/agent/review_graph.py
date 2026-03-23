"""LangGraph pipelines for PR context exploration and review synthesis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from code_review_agent.agent.tools import get_tools
from code_review_agent.agent.utils import load_chat_model
from db.code_serarch_layer import CodeSearchService

MAX_TOOL_ROUNDS = 10


class ReviewExplorerState(TypedDict):
    messages: Annotated[Sequence[AnyMessage], add_messages]
    tool_rounds: int


def _slim_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Return a token-efficient view of the message history by blanking AI reasoning text."""
    slimmed: list[AnyMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage) and not slimmed:
            slimmed.append(msg)
        elif isinstance(msg, AIMessage) and msg.tool_calls:
            slimmed.append(msg.model_copy(update={"content": ""}))
        elif isinstance(msg, ToolMessage):
            slimmed.append(msg)
    return slimmed


def build_review_explorer(
    query_service: CodeSearchService,
    system_prompt: str,
    model: str,
    repo_id: str | None = None,
):
    """Build a tool-limited ReAct graph for PR context exploration (Phase 1)."""
    tools = get_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model).bind_tools(tools)

    def llm_node(state: ReviewExplorerState) -> dict:
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            return {}

        formatted = system_prompt.format(system_time=datetime.now(tz=UTC).isoformat())
        repo_note = (
            f"\n\nActive repository: **{repo_id}** — all tools are scoped to this repo."
            if repo_id
            else ""
        )
        response: AIMessage = llm.invoke(
            [
                {"role": "system", "content": formatted + repo_note},
                *_slim_messages(state["messages"]),
            ]
        )
        return {"messages": [response]}

    def should_continue(state: ReviewExplorerState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if (
            isinstance(last, AIMessage)
            and last.tool_calls
            and state["tool_rounds"] < MAX_TOOL_ROUNDS
        ):
            return "tools"
        return "__end__"

    def increment_rounds(state: ReviewExplorerState) -> dict:
        return {"tool_rounds": state["tool_rounds"] + 1}

    builder = StateGraph(ReviewExplorerState)
    builder.add_node("llm_node", llm_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("increment_rounds", increment_rounds)

    builder.set_entry_point("llm_node")
    builder.add_conditional_edges("llm_node", should_continue)
    builder.add_edge("tools", "increment_rounds")
    builder.add_edge("increment_rounds", "llm_node")

    return builder.compile(name="ReviewExplorer")
