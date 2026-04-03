from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from code_review_agent.agent.utils import load_chat_model
from code_review_agent.nagent.nprompt import (
    MAX_TOOL_ROUNDS,
    get_explorer_system_prompt,
    get_reviewer_system_prompt,
    get_summarizer_system_prompt,
)
from code_review_agent.nagent.nstate import (
    CodeReviewAgentState,
    ReviewerOutput,
    SummarizerOutput,
)
from code_review_agent.nagent.ntools import get_tools
from db.code_serarch_layer import CodeSearchService


def _slim_messages(messages: list) -> list:
    """Return a token-efficient view of message history by blanking AI reasoning text."""
    from langchain_core.messages import ToolMessage

    slimmed = []
    for msg in messages:
        if isinstance(msg, HumanMessage) and not slimmed:
            slimmed.append(msg)
        elif isinstance(msg, AIMessage) and msg.tool_calls:
            slimmed.append(msg.model_copy(update={"content": ""}))
        elif isinstance(msg, ToolMessage):
            slimmed.append(msg)
    return slimmed


def build_code_review_graph(
    query_service: CodeSearchService,
    model: str,
    repo_id: str | None = None,
) -> StateGraph:
    """Build a 3-node LangGraph for code review.

    Architecture: Explorer (ReAct) → Reviewer (Structured) → Summarizer (Structured)

    Args:
        query_service: Neo4j query service for code search
        model: Model name for the LLM (e.g., "openai/gpt-4-turbo")
        repo_id: Repository ID to scope queries

    Returns:
        Compiled StateGraph
    """
    tools = get_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model)
    llm_with_tools = llm.bind_tools(tools)

    # ── Node 1: Explorer (ReAct Loop) ────────────────────────────────────────

    def explorer_node(state: CodeReviewAgentState) -> dict:
        """Investigate code using tools (ReAct loop).

        This node:
        1. Receives file context (diff, code, AST)
        2. Calls tools to investigate dependencies, complexity, etc.
        3. Accumulates evidence in messages
        4. Does NOT generate structured output (that's reviewer/summarizer)
        """
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            return {}

        system_prompt = get_explorer_system_prompt(
            file_based_context=state["file_based_context"],
            system_time=datetime.now(tz=UTC).isoformat(),
        )

        response: AIMessage = llm_with_tools.invoke(
            [
                SystemMessage(system_prompt),
                *_slim_messages(state["messages"]),
            ]
        )

        return {"messages": [response]}

    # ── Node 2: Tools Execution ───────────────────────────────────────────────

    # Uses LangGraph's prebuilt ToolNode
    tool_node = ToolNode(tools)

    # ── Node 3: Extract Sources ─────────────────────────────────────────────

    def extract_sources(state: CodeReviewAgentState) -> dict:
        """Extract sources from ToolMessage artifacts and merge into state.

        Tools return (content, sources) where sources are stored in
        ToolMessage.artifact. This node extracts and merges them.
        """
        new_sources = []
        for msg in state["messages"]:
            # ToolMessage stores artifact in msg.artifact
            if hasattr(msg, "artifact") and isinstance(msg.artifact, list):
                new_sources.extend(msg.artifact)

        return {"sources": new_sources}

    # ── Node 4: Increment Rounds ─────────────────────────────────────────────

    def increment_rounds(state: CodeReviewAgentState) -> dict:
        """Increment the tool round counter."""
        return {"tool_rounds": state["tool_rounds"] + 1}

    # ── Node 5: Reviewer (Structured Output) ────────────────────────────────

    def reviewer_node(state: CodeReviewAgentState) -> dict:
        """Generate structured output for issues and positive findings.

        This node:
        1. Reads full message history from exploration
        2. Calls LLM with structured output
        3. Output: file_based_issues, file_based_positive_findings
        """
        structured_llm = llm.with_structured_output(ReviewerOutput)

        system_prompt = get_reviewer_system_prompt(file_based_context=state["file_based_context"])

        result: ReviewerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                SystemMessage(f"Previous exploration:\n{_format_messages(state['messages'])}"),
            ]
        )

        return {
            "file_based_issues": [issue.model_dump() for issue in result.file_based_issues],
            "file_based_positive_findings": [
                finding.model_dump() for finding in result.file_based_positive_findings
            ],
        }

    # ── Node 6: Summarizer (Structured Output) ──────────────────────────────

    def summarizer_node(state: CodeReviewAgentState) -> dict:
        """Generate structured output for walkthrough.

        This node:
        1. Reads message history and context
        2. Calls LLM with structured output
        3. Output: file_based_walkthrough
        """
        structured_llm = llm.with_structured_output(SummarizerOutput)

        system_prompt = get_summarizer_system_prompt(file_based_context=state["file_based_context"])

        result: SummarizerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                SystemMessage(f"Previous exploration:\n{_format_messages(state['messages'])}"),
            ]
        )

        return {
            "file_based_walkthrough": [walk.model_dump() for walk in result.file_based_walkthrough]
        }

    # ── Conditional Edge Router ─────────────────────────────────────────────

    def should_continue(state: CodeReviewAgentState) -> Literal["tools", "reviewer"]:
        """Route after explorer: continue with tools or move to reviewer."""
        last = state["messages"][-1]
        tool_rounds = state["tool_rounds"]

        # Max rounds reached - move to reviewer
        if tool_rounds >= MAX_TOOL_ROUNDS:
            return "reviewer"

        # Has tool calls - continue exploring
        if isinstance(last, AIMessage) and last.tool_calls:
            # After 3+ rounds, check if we should stop early
            if tool_rounds >= 3:
                content = str(last.content).lower() if last.content else ""
                early_stop_keywords = ["caller", "found", "definition", "hierarchy", "complete"]
                if any(kw in content for kw in early_stop_keywords):
                    return "reviewer"
            return "tools"

        # No tool calls - done exploring, move to reviewer
        return "reviewer"

    # ── Build Graph ───────────────────────────────────────────────────────────

    builder = StateGraph(CodeReviewAgentState)

    # Add nodes
    builder.add_node("explorer", explorer_node)
    builder.add_node("tools", tool_node)
    builder.add_node("extract_sources", extract_sources)
    builder.add_node("increment_rounds", increment_rounds)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("summarizer", summarizer_node)

    # Set entry point
    builder.set_entry_point("explorer")

    # Add edges
    builder.add_conditional_edges("explorer", should_continue)
    builder.add_edge("tools", "extract_sources")
    builder.add_edge("extract_sources", "increment_rounds")
    builder.add_edge("increment_rounds", "explorer")
    builder.add_edge("reviewer", "summarizer")

    # Compile
    return builder.compile(name="BugViper Review Agent 2.0")


def _format_messages(messages: list) -> str:
    """Format message history for reviewer/summarizer prompts.

    Provides a condensed view of the exploration for context.
    """
    formatted = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            formatted.append(f"Human: {msg.content[:200]}...")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                tools = [tc.get("name", "?") for tc in msg.tool_calls]
                formatted.append(f"AI: Called tools: {', '.join(tools)}")
            else:
                formatted.append(f"AI: {msg.content[:200]}...")
    return "\n".join(formatted[-20:])  # Last 20 messages
