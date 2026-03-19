"""LangGraph graphs for PR review context-gathering and review synthesis.

Explorer graph (Phase 1):
    Uses a TypedDict state with a `tool_rounds` counter so the graph stops
    deterministically after MAX_TOOL_ROUNDS without relying on recursion limits.

Review Agent graph (Phase 2):
    A ReAct agent backed by a reasoning/thinking model. Receives the full
    Explorer message history as context, then reasons and optionally calls tools
    to verify specific doubts before producing the final JSON review.

    Structured output: the terminal `synthesize_node` calls the model with
    `with_structured_output(AgentFindings)`, so the result is a validated
    Pydantic instance — no JSON text parsing anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from code_review_agent.agent.tools import get_tools
from code_review_agent.agent.utils import load_chat_model
from code_review_agent.models.agent_schemas import AgentFindings
from db.code_serarch_layer import CodeSearchService

MAX_TOOL_ROUNDS = 10


class ReviewExplorerState(TypedDict):
    messages: Annotated[Sequence[AnyMessage], add_messages]
    tool_rounds: int


class ReviewAgentState(TypedDict):
    """State for the Phase-2 Review Agent.

    Extends the ReAct loop state with a `findings` field that is populated
    by the terminal `synthesize_node` using structured output — never via
    text parsing.
    """
    messages: Annotated[Sequence[AnyMessage], add_messages]
    tool_rounds: int
    findings: Optional[AgentFindings]


def _slim_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Return a token-efficient view of the Explorer message history.

    The Explorer ReAct loop accumulates: HumanMessage → AIMessage (tool calls)
    → ToolMessage → AIMessage → ToolMessage → …

    The large initial PR prompt (HumanMessage, ~23k tokens) gets re-sent with
    all growing AI reasoning text on every round.  We trim it by:
      1. Keeping the original HumanMessage intact (PR context is required).
      2. Keeping all AIMessages but zeroing out their text content — the API
         requires AIMessage ↔ ToolMessage pairing via tool_call_id, so we
         cannot drop AIMessages entirely; we just blank their text to avoid
         resending verbose reasoning tokens.
      3. Keeping all ToolMessages (tool results are cheap and necessary).

    This shaves roughly 1–2k tokens per round (all the accumulated AI
    reasoning text) without breaking the OpenAI-compatible message contract.
    """
    slimmed: list[AnyMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage) and not slimmed:
            # Keep only the first (original) user message intact.
            slimmed.append(msg)
        elif isinstance(msg, AIMessage) and msg.tool_calls:
            # Keep the AIMessage but blank its text — preserves tool_call_id
            # pairing required by the OpenAI message format.
            slimmed.append(msg.model_copy(update={"content": ""}))
        elif isinstance(msg, ToolMessage):
            slimmed.append(msg)
        # Pure text AIMessages (no tool calls) are dropped entirely —
        # they are intermediate reasoning steps we don't need to replay.
    return slimmed


def build_review_explorer(
    query_service: CodeSearchService,
    system_prompt: str,
    model: str,
    repo_id: str | None = None,
):
    """Build a tool-limited ReAct graph for PR context exploration (Phase 1).

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
            [{"role": "system", "content": formatted + repo_note}, *_slim_messages(state["messages"])]
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


def build_review_agent(
    query_service: CodeSearchService,
    system_prompt: str,
    model: str,
    repo_id: str | None = None,
    max_rounds: int = 5,
):
    """Build a reasoning-model Review Agent with structured output (Phase 2).

    Flow:
        llm_node  →  (has tool calls AND budget left?)
                         yes → tools → increment_rounds → llm_node
                         no  → synthesize_node → __end__

    The `synthesize_node` calls the model with
    ``with_structured_output(AgentFindings)``, so ``state["findings"]`` is a
    validated Pydantic instance — no JSON text parsing anywhere.

    Args:
        query_service: Neo4j code search service (same tools as Explorer).
        system_prompt: Pre-formatted REVIEW_AGENT_PROMPT string.
        model: Reasoning/thinking model identifier (e.g. Gemini 2.5 Pro).
        repo_id: Repository scope for all tool calls.
        max_rounds: Maximum tool-call rounds allowed before forcing synthesis.
    """
    tools = get_tools(query_service, repo_id=repo_id)

    # Two LLM variants for the same model:
    #   - llm_react: bound to tools for the ReAct reasoning loop
    #   - llm_structured: uses with_structured_output for the terminal call
    llm_react = load_chat_model(model, timeout=300).bind_tools(tools)
    llm_structured = load_chat_model(model, timeout=300).with_structured_output(AgentFindings)

    repo_note = (
        f"\n\nActive repository: **{repo_id}** — all tools are scoped to this repo."
        if repo_id
        else ""
    )
    full_system = system_prompt + repo_note

    def llm_node(state: ReviewAgentState) -> dict:
        """ReAct reasoning node — reasons and decides whether to call tools."""
        if state["tool_rounds"] >= max_rounds:
            # Budget exhausted — skip directly to synthesis.
            return {}

        response: AIMessage = llm_react.invoke(
            [{"role": "system", "content": full_system}, *state["messages"]]
        )
        return {"messages": [response]}

    def should_continue(state: ReviewAgentState) -> Literal["tools", "synthesize"]:
        """Route to tool execution or final structured synthesis."""
        last = state["messages"][-1]
        if (
            isinstance(last, AIMessage)
            and last.tool_calls
            and state["tool_rounds"] < max_rounds
        ):
            return "tools"
        # No tool calls (or budget exhausted) → produce structured findings.
        return "synthesize"

    def increment_rounds(state: ReviewAgentState) -> dict:
        return {"tool_rounds": state["tool_rounds"] + 1}

    def synthesize_node(state: ReviewAgentState) -> dict:
        """Terminal node — produces a validated AgentFindings via structured output.

        Uses with_structured_output so LangChain handles schema enforcement
        (via tool-calling or JSON mode depending on provider). The result is a
        validated Pydantic AgentFindings instance stored in state["findings"].
        """
        findings: AgentFindings = llm_structured.invoke(
            [{"role": "system", "content": full_system}, *state["messages"]]
        )
        return {"findings": findings}

    builder = StateGraph(ReviewAgentState)
    builder.add_node("llm_node", llm_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("increment_rounds", increment_rounds)
    builder.add_node("synthesize", synthesize_node)

    builder.set_entry_point("llm_node")
    builder.add_conditional_edges("llm_node", should_continue)
    builder.add_edge("tools", "increment_rounds")
    builder.add_edge("increment_rounds", "llm_node")
    builder.add_edge("synthesize", "__end__")

    return builder.compile(name="ReviewAgent")
