"""LangGraph explorer graph for PR review context-gathering.

Uses a TypedDict state with a `tool_rounds` counter so the graph stops
deterministically after MAX_TOOL_ROUNDS without relying on recursion limits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Sequence

from langchain_core.messages import AIMessage, AnyMessage
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


def build_review_explorer(
    query_service: CodeSearchService,
    system_prompt: str,
    model: str,
    repo_id: str | None = None,
):
    """Build a tool-limited ReAct graph for PR context exploration.

    Stops after MAX_TOOL_ROUNDS tool invocations instead of relying on
    LangGraph's recursion limit, so we always get the accumulated messages
    back even if the model is tool-happy.
    """
    tools = get_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model).bind_tools(tools)

    def llm_node(state: ReviewExplorerState) -> dict:
        # If we've used all tool rounds, don't make another LLM call — just end.
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            return {}

        formatted = system_prompt.format(system_time=datetime.now(tz=UTC).isoformat())
        repo_note = (
            f"\n\nActive repository: **{repo_id}** — all tools are scoped to this repo."
            if repo_id
            else ""
        )
        response: AIMessage = llm.invoke(
            [{"role": "system", "content": formatted + repo_note}, *state["messages"]]
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
