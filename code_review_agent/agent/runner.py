"""LangGraph-powered PR review runner.

Two-phase pipeline:
  1. ReAct exploration  — LLM uses Neo4j tools to gather additional context
                          (capped at MAX_TOOL_ROUNDS to prevent runaway loops).
  2. Structured synthesis — plain LLM call with JSON schema in the prompt;
                          response is parsed robustly to handle any model on
                          OpenRouter regardless of structured-output support.
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from code_review_agent.agent.review_graph import build_review_explorer
from code_review_agent.agent.review_prompt import REVIEW_EXPLORER_PROMPT, REVIEW_SYNTHESIZER_PROMPT
from code_review_agent.agent.utils import load_chat_model
from code_review_agent.config import config
from code_review_agent.models.agent_schemas import AgentFindings, ReviewResults
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict:
    """Extract a JSON object from model output.

    Handles three common formats:
      1. Pure JSON
      2. JSON wrapped in ```json ... ``` or ``` ... ``` code fences
      3. JSON object embedded somewhere in prose
    """
    text = text.strip()

    fenced = re.sub(r"^```(?:json)?\s*", "", text)
    fenced = re.sub(r"\s*```$", "", fenced).strip()
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError(f"No JSON object found in model response (first 200 chars): {text[:200]!r}")


async def _synthesize(model: str, explored_messages: list) -> AgentFindings:
    """Call the LLM with schema-in-prompt and parse the JSON response manually."""
    llm = load_chat_model(model)

    synthesis_messages = [
        SystemMessage(content=REVIEW_SYNTHESIZER_PROMPT),
        *explored_messages,
        HumanMessage(content=(
            "Using the diff and all the context gathered above, "
            "output the JSON code review now. Remember: ONLY the JSON object, nothing else."
        )),
    ]

    response = await llm.ainvoke(synthesis_messages)
    raw = response.content if hasattr(response, "content") else str(response)

    try:
        data = _extract_json(raw)
        return AgentFindings.model_validate(data)
    except Exception as e:
        logger.error("JSON parse failed (%s). Raw output (first 500): %s", e, raw[:500])
        raise


async def run_review(
    review_prompt: str,
    repo_id: str,
    pr_number: int,
    query_service: CodeSearchService,
) -> ReviewResults:
    """LangGraph PR review: explore context with tools, then synthesize structured findings.

    Phase 1 — ReAct exploration:
        A tool-limited graph gathers additional context from Neo4j.
        The explorer now explicitly traces callers of changed functions
        for cross-file impact analysis.

    Phase 2 — JSON synthesis:
        A plain LLM call produces all issues at all confidence levels (0-10).
        Status ("new"/"still_open"/"fixed") is set by the LLM based on
        previous findings injected into the context by the caller.
    """
    model = config.review_model
    logger.info("LangGraph review start — %s#%s  model=%s", repo_id, pr_number, model)

    # ── Phase 1: ReAct exploration ───────────────────────────────────────────
    graph = build_review_explorer(
        query_service,
        system_prompt=REVIEW_EXPLORER_PROMPT,
        model=model,
        repo_id=repo_id,
    )

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=review_prompt)], "tool_rounds": 0}
        )
        explored_messages = list(result.get("messages", []))
        tool_rounds_used = result.get("tool_rounds", 0)
    except Exception:
        logger.exception("LangGraph exploration phase failed — falling back to prompt only")
        explored_messages = [HumanMessage(content=review_prompt)]
        tool_rounds_used = 0

    logger.info(
        "Exploration complete: %d tool rounds, %d messages in context",
        tool_rounds_used, len(explored_messages),
    )

    # ── Phase 2: Structured synthesis ────────────────────────────────────────
    try:
        findings = await _synthesize(model, explored_messages)
    except Exception:
        logger.exception("Structured synthesis failed — returning empty findings")
        findings = AgentFindings(walk_through=[], issues=[], positive_findings=[])

    issues = findings.issues  # all confidence levels, status set by LLM
    open_issues = [i for i in issues if i.status != "fixed"]
    critical = sum(1 for i in open_issues if i.severity == "critical")
    high = sum(1 for i in open_issues if i.severity == "high")

    logger.info(
        "Review complete: %d total issues (%d open: %d critical, %d high)",
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
    )
