from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from common.llm import load_chat_model
from code_review_agent.nagent.nprompt import (
    MAX_TOOL_ROUNDS,
    get_explorer_system_prompt,
    get_reviewer_system_prompt,
    get_summarizer_system_prompt,
    get_validator_system_prompt,
)
from code_review_agent.nagent.nstate import (
    CodeReviewAgentState,
    ReviewerOutput,
    SummarizerOutput,
    ValidatorOutput,
)
from code_review_agent.nagent.ntools import get_code_review_tools
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


def _slim_messages(messages: list) -> list:
    """Return a token-efficient view of message history.
    Keeps ToolMessages intact and blanks AI reasoning text."""
    slimmed = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            slimmed.append(msg.model_copy(update={"content": ""}))
        elif isinstance(msg, ToolMessage):
            slimmed.append(msg)
        elif isinstance(msg, AIMessage) and not msg.tool_calls:
            slimmed.append(msg)
    return slimmed


def _format_messages(messages: list) -> str:
    """Format message history for reviewer/summarizer prompts."""
    MAX_TOOL_OUTPUT = 400
    MAX_MESSAGES = 30
    formatted: list[str] = []

    for msg in messages:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                names = [tc.get("name", "?") for tc in msg.tool_calls]
                formatted.append(f"[Explorer] Called tools: {', '.join(names)}")
            elif msg.content:
                formatted.append(f"[Explorer] {str(msg.content)[:500]}")
        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            if len(content) > MAX_TOOL_OUTPUT:
                content = content[:MAX_TOOL_OUTPUT] + "…(truncated)"
            formatted.append(f"[Tool:{msg.name}] {content}")

    return "\n".join(formatted[-MAX_MESSAGES:])


def build_code_review_graph(
    query_service: CodeSearchService,
    model: str,
    repo_id: str | None = None,
) -> StateGraph:
    """Build a LangGraph for code review.    Architecture:
    explorer (ReAct loop) → validator → reviewer → summarizer
    """
    tools = get_code_review_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model)
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    def explorer_node(state: CodeReviewAgentState) -> dict:
        current_rounds = state["tool_rounds"]

        system_prompt = get_explorer_system_prompt(
            file_based_context=state["file_based_context"],
            system_time=datetime.now(tz=UTC).isoformat(),
        )

        response: AIMessage = llm_with_tools.invoke(
            [SystemMessage(system_prompt), *_slim_messages(state["messages"])]
        )

        return {
            "messages": [response],
            "tool_rounds": current_rounds + 1,
        }

    def extract_sources(state: CodeReviewAgentState) -> dict:
        new_sources: list[dict] = []
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                if isinstance(msg.artifact, list):
                    new_sources.extend(msg.artifact)
            elif isinstance(msg, AIMessage):
                break
        return {"sources": new_sources}

    def validate_previous_issues_node(state: CodeReviewAgentState) -> dict:
        previous_issues = state.get("previous_issues", [])
        if not previous_issues:
            logger.info("No previous issues to validate")
            return {"validated_previous_issues": []}

        # Strip hallucination-prone fields from previous issues
        # Only pass: title, file, line_start, line_end, category, severity
        # Remove description, suggestion, impact, code_snippet which may contain
        # incorrect details that bias the LLM
        stripped_issues = []
        for issue in previous_issues:
            stripped = {
                "title": issue.get("title"),
                "file": issue.get("file"),
                "line_start": issue.get("line_start"),
                "line_end": issue.get("line_end"),
                "category": issue.get("category", "bug"),
                "severity": issue.get("severity", "medium"),
            }
            stripped_issues.append(stripped)

        logger.info(f"Validating {len(stripped_issues)} previous issues with AI")

        structured_llm = llm.with_structured_output(ValidatorOutput)

        previous_issues_json = json.dumps(stripped_issues, indent=2)
        system_prompt = get_validator_system_prompt(
            file_based_context=state["file_based_context"],
            previous_issues_json=previous_issues_json,
        )

        result: ValidatorOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                HumanMessage("Validate all previous issues against the current code."),
            ]
        )

        validated = [issue.model_dump() for issue in result.validated_issues]

        still_open = sum(1 for v in validated if v.get("status") == "still_open")
        fixed = sum(1 for v in validated if v.get("status") == "fixed")
        partial = sum(1 for v in validated if v.get("status") == "partially_fixed")
        logger.info(
            f"AI validated {len(validated)} issues: "
            f"{still_open} still_open, {partial} partially_fixed, {fixed} fixed"
        )

        return {"validated_previous_issues": validated}

    def reviewer_node(state: CodeReviewAgentState) -> dict:
        structured_llm = llm.with_structured_output(ReviewerOutput)

        validated_issues_json = ""
        validated_previous_issues = state.get("validated_previous_issues", [])
        if validated_previous_issues:
            validated_issues_json = json.dumps(validated_previous_issues, indent=2)

        system_prompt = get_reviewer_system_prompt(
            file_based_context=state["file_based_context"],
            validated_issues_json=validated_issues_json,
        )
        exploration_summary = _format_messages(state["messages"])

        result: ReviewerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                HumanMessage(
                    "Exploration findings:\n"
                    f"{exploration_summary}\n\n"
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

    def summarizer_node(state: CodeReviewAgentState) -> dict:
        structured_llm = llm.with_structured_output(SummarizerOutput)
        system_prompt = get_summarizer_system_prompt(file_based_context=state["file_based_context"])
        exploration_summary = _format_messages(state["messages"])

        result: SummarizerOutput = structured_llm.invoke(
            [
                SystemMessage(system_prompt),
                HumanMessage(
                    "Exploration findings:\n"
                    f"{exploration_summary}\n\n"
                    "Now produce the structured walkthrough output."
                ),
            ]
        )

        return {
            "file_based_walkthrough": [walk.model_dump() for walk in result.file_based_walkthrough]
        }

    def should_continue(state: CodeReviewAgentState) -> Literal["tools", "validator"]:
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            return "validator"

        last = state["messages"][-1] if state["messages"] else None

        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"

        return "validator"

    builder = StateGraph(CodeReviewAgentState)
    builder.add_node("explorer", explorer_node)
    builder.add_node("tools", tool_node)
    builder.add_node("extract_sources", extract_sources)
    builder.add_node("validator", validate_previous_issues_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("summarizer", summarizer_node)

    builder.set_entry_point("explorer")
    builder.add_conditional_edges("explorer", should_continue)
    builder.add_edge("tools", "extract_sources")
    builder.add_edge("extract_sources", "explorer")
    builder.add_edge("validator", "reviewer")
    builder.add_edge("reviewer", "summarizer")

    return builder.compile()
