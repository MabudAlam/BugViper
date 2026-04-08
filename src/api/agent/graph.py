from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from api.agent.context import Context
from api.agent.state import InputState, State
from api.agent.tools import get_tools
from common.llm import load_chat_model
from api.models.rag import Source
from db.code_serarch_layer import CodeSearchService


def build_graph(
    query_service: CodeSearchService, context: Context | None = None, repo_id: str | None = None
):
    ctx = context or Context()

    tools = get_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(ctx.model).bind_tools(tools)

    def LLM_Node(state: State):
        if repo_id:
            repo_context = (
                "\n\n---\n"
                f"Active repository: **{repo_id}** — "
                "all tools are already scoped to this repo."
            )
        else:
            repo_context = "\n\n---\nNo repository selected — searches run across the entire graph."
        system_prompt = (
            ctx.system_prompt.format(system_time=datetime.now(tz=UTC).isoformat()) + repo_context
        )
        response: AIMessage = llm.invoke(
            [{"role": "system", "content": system_prompt}, *state.messages]
        )

        if state.is_last_step and response.tool_calls:
            return {
                "messages": [
                    AIMessage(
                        id=response.id, content="Could not find an answer within the allowed steps."
                    )
                ]
            }

        return {"messages": [response]}

    def should_continue(state: State) -> Literal["tools", "__end__"]:
        last = state.messages[-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "__end__"

    def extract_sources_node(state: State) -> dict:
        """Read ToolMessage.artifact from the current batch and push into state.

        Tools set response_format="content_and_artifact" so LangChain stores
        structured source dicts in msg.artifact — no parsing required.
        """
        new_sources: list[Source] = []
        for msg in reversed(state.messages):
            if not isinstance(msg, ToolMessage):
                break
            for item in msg.artifact or []:
                if item.get("path"):
                    new_sources.append(
                        Source(
                            path=item["path"],
                            line_number=item.get("line_number"),
                            name=item.get("name"),
                            type=item.get("type"),
                        )
                    )
        return {"sources": new_sources}

    builder = StateGraph(State, input_schema=InputState)

    builder.add_node("llm_node", LLM_Node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("extract_sources", extract_sources_node)

    # Flow:
    #   __start__ → llm_node → (tool calls?) → tools → extract_sources → llm_node → ...
    #                        ↘ __end__  (plain answer, no tool calls)
    builder.add_edge("__start__", "llm_node")
    builder.add_conditional_edges("llm_node", should_continue)
    builder.add_edge("tools", "extract_sources")
    builder.add_edge("extract_sources", "llm_node")

    return builder.compile(name="BugViper Agent")
