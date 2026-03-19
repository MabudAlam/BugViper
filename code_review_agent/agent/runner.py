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
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage

from code_review_agent.agent.review_graph import build_review_agent, build_review_explorer
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
    query_service: CodeSearchService,
    repo_id: str,
    max_rounds: int,
) -> tuple[AgentFindings, int]:
    """Phase 2: Run the Review Agent on the Explorer's gathered context.

    The Review Agent is a ReAct graph backed by a reasoning/thinking model.
    It receives all Explorer messages as its starting context, reasons about
    the code, optionally calls tools to verify doubts, then produces a
    validated AgentFindings instance via with_structured_output — no JSON
    text parsing involved.

    Returns:
        (findings, review_rounds_used)
    """
    system_prompt = REVIEW_AGENT_PROMPT.format(
        max_rounds=max_rounds,
        system_time=datetime.now(tz=UTC).isoformat(),
    )

    graph = build_review_agent(
        query_service,
        system_prompt=system_prompt,
        model=model,
        repo_id=repo_id,
        max_rounds=max_rounds,
    )

    # Seed the agent with the Explorer's accumulated context + a final instruction.
    seed_messages = [
        *explored_messages,
        HumanMessage(content=(
            "You have the full PR context above. Reason through the code carefully, "
            "use tools only if you have a specific unresolved doubt, then produce "
            "the structured code review."
        )),
    ]

    try:
        result = await graph.ainvoke(
            {"messages": seed_messages, "tool_rounds": 0, "findings": None}
        )
    except Exception:
        logger.exception("Review Agent phase failed — returning empty findings")
        return AgentFindings(walk_through=[], issues=[], positive_findings=[]), 0

    findings: AgentFindings | None = result.get("findings")
    review_rounds_used: int = result.get("tool_rounds", 0)

    if findings is None:
        logger.error("Review Agent terminated without producing findings — returning empty")
        findings = AgentFindings(walk_through=[], issues=[], positive_findings=[])

    return findings, review_rounds_used


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
    max_review_rounds = config.review_agent_max_rounds

    logger.info(
        "Review start — %s#%s  explore_model=%s  synthesis_model=%s",
        repo_id, pr_number, explore_model, synthesis_model,
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
        tool_rounds_used, len(explored_messages),
    )

    # ── Phase 2: Review Agent (structured output) ─────────────────────────────
    logger.info(
        "Review Agent start — model=%s  max_rounds=%d",
        synthesis_model, max_review_rounds,
    )

    findings = AgentFindings(walk_through=[], issues=[], positive_findings=[])
    review_agent_rounds_used = 0

    try:
        findings, review_agent_rounds_used = await _run_review_agent(
            model=synthesis_model,
            explored_messages=explored_messages,
            query_service=query_service,
            repo_id=repo_id,
            max_rounds=max_review_rounds,
        )
    except Exception:
        logger.exception("Review Agent failed — returning empty findings")

    logger.info(
        "Review Agent complete: %d tool rounds used",
        review_agent_rounds_used,
    )

    issues = findings.issues
    open_issues = [i for i in issues if i.status != "fixed"]
    critical = sum(1 for i in open_issues if i.category in ("bug", "security"))
    high     = sum(1 for i in open_issues if i.category == "performance")

    logger.info(
        "Review complete: %d issues (%d open — %d critical, %d high)",
        len(issues), len(open_issues), critical, high,
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
