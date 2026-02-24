"""PR review pipeline using a single pydantic-ai reviewer agent."""

import logging
import os

from deepagent.config import config

os.environ.setdefault("OPENROUTER_API_KEY", config.openrouter_api_key)

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402
from deepagent.models.agent_schemas import AgentFindings, ReviewResults
from deepagent.prompts import REVIEWER_PROMPT

CONFIDENCE_THRESHOLD = 7

logger = logging.getLogger(__name__)

MODEL = f"openrouter:{config.review_model}"

_MODEL_SETTINGS = ModelSettings(max_tokens=15000, timeout=180)

reviewer = Agent(
    MODEL,
    system_prompt=REVIEWER_PROMPT,
    output_type=AgentFindings,
    model_settings=_MODEL_SETTINGS,
    name="reviewer",
)

try:
    import logfire

    if config.enable_logfire and config.logfire_token:
        logfire.configure(token=config.logfire_token)
        logfire.instrument_pydantic_ai(reviewer)
except ImportError:
    pass


async def run_review(prompt: str, repo_id: str, pr_number: int) -> ReviewResults:
    """Run the reviewer agent on a pre-built prompt and return structured results."""
    logger.info(f"Starting review for {repo_id}#{pr_number}")

    try:
        result = await reviewer.run(prompt)
        findings: AgentFindings = result.output
    except Exception:
        logger.exception("Reviewer agent failed — returning empty findings")
        findings = AgentFindings(walk_through=[], issues=[], positive_findings=[])

    issues = [i for i in findings.issues if i.confidence >= CONFIDENCE_THRESHOLD]
    logger.info(f"Reviewer: {len(findings.issues)} raw → {len(issues)} after confidence filter (≥{CONFIDENCE_THRESHOLD})")

    critical = sum(1 for i in issues if i.severity == "critical")
    high = sum(1 for i in issues if i.severity == "high")

    summary = (
        "No significant issues found. The code looks good."
        if not issues
        else f"Found {len(issues)} issue(s) ({critical} critical, {high} high). Review the details below."
    )

    return ReviewResults(
        summary=summary,
        issues=issues,
        positive_findings=findings.positive_findings,
        walk_through=findings.walk_through,
    )
