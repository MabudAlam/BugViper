"""LangGraph-powered PR review runner.

Pipeline:
  1. ReAct exploration  — Explorer Agent uses Neo4j tools to gather context.
                          Capped at MAX_TOOL_ROUNDS to stop deterministically.
  2. Review Agent       — Reasoning model receives all Explorer context, reasons
                          deeply, optionally verifies doubts with tools, then
                          produces a validated AgentFindings via structured output.
                          No JSON text parsing anywhere.

Static analysis (lint service) runs in parallel with this pipeline from the
caller — results are merged before the final comment is posted.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from openrouter import OpenRouter

from code_review_agent.agent.review_graph import build_review_explorer
from code_review_agent.agent.review_prompt import (
    REVIEW_AGENT_PROMPT,
    REVIEW_EXPLORER_PROMPT,
)
from code_review_agent.config import config
from code_review_agent.models.agent_schemas import AgentFindings, ReviewResults
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


async def _run_review_agent(
    model: str,
    explored_messages: list,
) -> tuple[AgentFindings, int]:
    """Phase 2: Single LLM call via OpenRouter structured output."""


    schema = AgentFindings.model_json_schema()
    system_prompt = REVIEW_AGENT_PROMPT.format(
        system_time=datetime.now(tz=UTC).isoformat(),
        schema=schema,
    )

    # Extract raw content from explored_messages for prompt
    content_lines = []
    for msg in explored_messages:
        role = getattr(msg, "type", "assistant")
        if role == "ai":
            role = "assistant"
        content = getattr(msg, "content", str(msg))
        if isinstance(content, list):
            content = " ".join([str(c) for c in content if isinstance(c, str)])
        content_lines.append(f"{role.upper()}:\n{content}\n")

    context_str = "\n".join(content_lines)

    # Construct the final prompt with system instructions
    final_prompt = (
        f"{system_prompt}\n\n"
        f"--- EXPLORED CONTEXT ---\n{context_str}\n\n"
        "You have the full PR context above. Produce the structured code review.\n"
        "You MUST output ONLY valid JSON matching the exact schema."
    )

   
    logger.info("Executing Phase 2 via direct OpenRouter SDK structured output call")

    try:
        async with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY")) as client:
            response = await client.chat.send_async(
                model=model,
                messages=[{"role": "user", "content": final_prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"strict": True, "name": "AgentFindings", "schema": schema},
                },

            )
        content = response.choices[0].message.content
        findings = AgentFindings.model_validate_json(content)
        logger.info(f"AgentFindings: {findings}")
    except Exception as e:
        logger.error(f"Failed to parse AgentFindings or call OpenRouter: {e}")
        # Return empty findings on fatal error
        return AgentFindings(walk_through=[], issues=[], positive_findings=[]), 0

    return findings, 0


async def run_review(
    review_prompt: str,
    repo_id: str,
    pr_number: int,
    query_service: CodeSearchService,
) -> ReviewResults:
    """Two-phase PR review: explore context with tools, then reason and synthesize.

    Phase 1 — ReAct exploration (Explorer Agent):
        Explorer agent iteratively calls Neo4j tools to build context.
        Falls back to get_file_source when the graph has no indexed symbols.
        Capped at MAX_TOOL_ROUNDS so accumulated messages always return cleanly.
        Uses config.review_model (cheap/fast — optimised for many tool calls).

    Phase 2 — Review Agent (Reasoning Model):
        Receives all Explorer messages as context.
        Reasons deeply via the model's internal chain-of-thought/thinking.
        Can call tools (same set as Explorer) to verify specific doubts.
        Capped at config.review_agent_max_rounds (default 5).
        Uses config.synthesis_model (reasoning model, e.g. Gemini 2.5 Pro).
        Produces a validated AgentFindings via with_structured_output — no JSON
        text parsing, no regex, no manual extraction.
    """
    explore_model = config.review_model
    synthesis_model = config.synthesis_model

    logger.info(
        "Review start — %s#%s  explore_model=%s  synthesis_model=%s",
        repo_id,
        pr_number,
        explore_model,
        synthesis_model,
    )

    # ── Phase 1: Context exploration ─────────────────────────────────────────
    graph = build_review_explorer(
        query_service,
        system_prompt=REVIEW_EXPLORER_PROMPT,
        model=explore_model,
        repo_id=repo_id,
    )

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=review_prompt)], "tool_rounds": 0}
        )
        explored_messages = list(result.get("messages", []))
        tool_rounds_used = result.get("tool_rounds", 0)
    except Exception:
        logger.exception("Exploration phase failed — falling back to prompt only")
        explored_messages = [HumanMessage(content=review_prompt)]
        tool_rounds_used = 0

    logger.info(
        "Exploration complete: %d tool rounds, %d messages in context",
        tool_rounds_used,
        len(explored_messages),
    )

    # ── Phase 2: Review Agent (structured output) ─────────────────────────────
    logger.info("Review Agent start — model=%s", synthesis_model)

    findings = AgentFindings(walk_through=[], issues=[], positive_findings=[])
    review_agent_rounds_used = 0

    try:
        findings, review_agent_rounds_used = await _run_review_agent(
            model=synthesis_model,
            explored_messages=explored_messages,
        )
    except Exception:
        logger.exception("Review Agent failed critically.")
        raise

    logger.info(
        "Review Agent complete: %d tool rounds used",
        review_agent_rounds_used,
    )

    issues = findings.issues
    open_issues = [i for i in issues if i.status != "fixed"]
    critical = sum(1 for i in open_issues if i.category in ("bug", "security"))
    high = sum(1 for i in open_issues if i.category == "performance")

    logger.info(
        "Review complete: %d issues (%d open — %d critical, %d high)",
        len(issues),
        len(open_issues),
        critical,
        high,
    )

    summary = (
        "No open issues found. The code looks good."
        if not open_issues
        else f"{len(open_issues)} open issue(s) ({critical} critical, {high} high)."
    )

    return ReviewResults(
        summary=summary,
        issues=issues,
        positive_findings=findings.positive_findings,
        walk_through=findings.walk_through,
        raw_agent_json=findings.model_dump_json(indent=2),
        tool_rounds_used=tool_rounds_used,
        review_agent_rounds_used=review_agent_rounds_used,
    )
