from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from api.agent.utils import load_chat_model, load_gemini_model
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
from code_review_agent.nagent.ntools import get_code_review_tools
from db.code_serarch_layer import CodeSearchService


def _slim_messages(messages: list) -> list:
    """Return a token-efficient view of message history.

    Blanks AI reasoning text in tool-calling turns (the content field is
    usually empty anyway for tool-call turns, but we normalise it explicitly).
    Keeps all ToolMessages intact so the LLM sees what tools returned.
    Skips bare HumanMessages — the explorer never injects them and the system
    prompt already carries the file context.
    """
    slimmed = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # Keep tool_calls, blank any prose the model emitted alongside them
            slimmed.append(msg.model_copy(update={"content": ""}))
        elif isinstance(msg, ToolMessage):
            slimmed.append(msg)
        elif isinstance(msg, AIMessage) and not msg.tool_calls:
            # Final "I'm done" reasoning turn — keep it, the reviewer uses it
            slimmed.append(msg)
        # HumanMessages are skipped; context lives in the SystemMessage
    return slimmed


def build_code_review_graph(
    query_service: CodeSearchService,
    model: str,
    repo_id: str | None = None,
) -> StateGraph:
    """Build a LangGraph for code review.

    Architecture:
        explorer (ReAct loop) → reviewer (structured) → summarizer (structured)

    tool_rounds is incremented inside explorer_node so the conditional edge
    always sees the post-increment value and the cap fires correctly.

    Args:
        query_service: Neo4j query service for code search.
        model: Model identifier (e.g. "openai/gpt-4o-mini").
        repo_id: Optional repository ID to scope graph queries.

    Returns:
        Compiled StateGraph ready to invoke.
    """
    tools = get_code_review_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model)
    llm_with_tools = llm.bind_tools(tools)

    # ── Node 1: Explorer (ReAct loop) ────────────────────────────────────────

    def explorer_node(state: CodeReviewAgentState) -> dict:
        """Investigate code using tools.

        Increments tool_rounds here (not in a separate node) so that
        should_continue always reads the up-to-date count.
        """
        current_rounds = state["tool_rounds"]

        # Hard cap: return empty update — should_continue will route to reviewer
        if current_rounds >= MAX_TOOL_ROUNDS:
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

        # Increment rounds here so should_continue sees the updated value
        return {
            "messages": [response],
            "tool_rounds": current_rounds + 1,
        }

    # ── Node 2: Tool execution ────────────────────────────────────────────────

    tool_node = ToolNode(tools)

    # ── Node 3: Extract sources ───────────────────────────────────────────────

    def extract_sources(state: CodeReviewAgentState) -> dict:
        """Extract sources only from the most recent batch of ToolMessages.

        Scanning all messages every round re-extracts already-seen artifacts.
        We only need to look at messages appended since the last extraction,
        which in practice means the ToolMessages from the current round.
        The _merge_sources reducer on the state handles deduplication.
        """
        new_sources: list[dict] = []

        # Walk backwards and collect ToolMessages until we hit an AIMessage
        # (the tool-calling turn that triggered this batch).
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                if isinstance(msg.artifact, list):
                    new_sources.extend(msg.artifact)
            elif isinstance(msg, AIMessage):
                break  # Stop at the AI turn that made these calls

        return {"sources": new_sources}

    # ── Node 4: Reviewer (structured output) ─────────────────────────────────

    def reviewer_node(state: CodeReviewAgentState) -> dict:
        """Produce file_based_issues and file_based_positive_findings."""
        structured_llm = llm.with_structured_output(ReviewerOutput)

        system_prompt = get_reviewer_system_prompt(file_based_context=state["file_based_context"])
        exploration_summary = _format_messages(state["messages"])

        # One SystemMessage + one HumanMessage — universally supported.
        result: ReviewerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                HumanMessage(
                    f"Exploration findings:\n{exploration_summary}\n\n"
                    "Now produce the structured review output."
                ),
            ]
        )

        return {
            "file_based_issues": [issue.model_dump() for issue in result.file_based_issues],
            "file_based_positive_findings": [
                finding.model_dump() for finding in result.file_based_positive_findings
            ],
        }

    # ── Node 5: Summarizer (structured output) ───────────────────────────────

    def summarizer_node(state: CodeReviewAgentState) -> dict:
        """Produce file_based_walkthrough."""
        structured_llm = llm.with_structured_output(SummarizerOutput)

        system_prompt = get_summarizer_system_prompt(file_based_context=state["file_based_context"])
        exploration_summary = _format_messages(state["messages"])

        result: SummarizerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                HumanMessage(
                    f"Exploration findings:\n{exploration_summary}\n\n"
                    "Now produce the structured walkthrough output."
                ),
            ]
        )

        return {
            "file_based_walkthrough": [walk.model_dump() for walk in result.file_based_walkthrough]
        }

    # ── Conditional edge router ───────────────────────────────────────────────

    def should_continue(state: CodeReviewAgentState) -> Literal["tools", "reviewer"]:
        """Route after explorer: keep exploring or hand off to reviewer.

        tool_rounds was already incremented inside explorer_node, so this
        value is always current.
        """
        # Cap reached
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            return "reviewer"

        last = state["messages"][-1] if state["messages"] else None

        # LLM made tool calls → continue the ReAct loop
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"

        # LLM emitted no tool calls → it's done exploring
        return "reviewer"

    # ── Build graph ───────────────────────────────────────────────────────────

    builder = StateGraph(CodeReviewAgentState)

    builder.add_node("explorer", explorer_node)
    builder.add_node("tools", tool_node)
    builder.add_node("extract_sources", extract_sources)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("summarizer", summarizer_node)

    builder.set_entry_point("explorer")

    builder.add_conditional_edges("explorer", should_continue)
    builder.add_edge("tools", "extract_sources")
    builder.add_edge("extract_sources", "explorer")
    builder.add_edge("reviewer", "summarizer")

    return builder.compile()


def _format_messages(messages: list) -> str:
    """Format message history for reviewer/summarizer prompts.

    Includes tool results so the reviewer sees what was actually found,
    not just which tools were called. Truncates large tool outputs to
    keep the prompt manageable.
    """
    MAX_TOOL_OUTPUT = 400  # chars per tool result
    MAX_MESSAGES = 30  # last N entries to include

    formatted: list[str] = []

    for msg in messages:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                names = [tc.get("name", "?") for tc in msg.tool_calls]
                formatted.append(f"[Explorer] Called tools: {', '.join(names)}")
            elif msg.content:
                # Final reasoning turn
                formatted.append(f"[Explorer] {str(msg.content)[:500]}")
        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            if len(content) > MAX_TOOL_OUTPUT:
                content = content[:MAX_TOOL_OUTPUT] + "…(truncated)"
            formatted.append(f"[Tool:{msg.name}] {content}")

    return "\n".join(formatted[-MAX_MESSAGES:])
