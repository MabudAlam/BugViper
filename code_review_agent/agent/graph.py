"""Review Agent using LangGraph create_react_agent with structured output."""

import logging
from datetime import UTC, datetime

from langgraph.prebuilt import create_react_agent

from code_review_agent.agent.tools import get_tools
from code_review_agent.agent.utils import load_chat_model
from code_review_agent.config import config
from code_review_agent.models.file_review import FileReviewLLMOutput
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are BugViper, an expert code reviewer.
Find real bugs in the code changes.

## Your Task

Review the provided code changes and produce a structured analysis
using the FileReviewLLMOutput format.

## Bug Patterns to Check

**Correctness:**
- Division by zero, modulo by zero (check for guards)
- Null/None dereference WITHOUT guards
- Off-by-one in loops/indices
- Wrong operator (== vs =, and vs or)
- Missing return statements
- Mutable default arguments (def f(x=[]))

**Security:**
- SQL/command injection
- Path traversal
- Missing authentication/authorization
- Hardcoded secrets

**Performance:**
- N+1 query patterns
- O(n^2) where O(n) would work

**Error Handling:**
- Bare except: swallowing exceptions
- Empty except blocks

## Tools Available

You have tools to verify findings:
- **verify_finding** — read code around a line to verify potential issues
- **get_function_callers** — find who calls a function (impact analysis)
- **get_function_info** — get function source + complexity
- **find_function_in_file** — find a function in a specific file

ALWAYS use tools to verify facts before reporting issues.

## Previous Issues Tracking

If "Previous Issues" section exists:
1. Check if already fixed (guard clause added, default value, try/except added)
2. Mark status: "fixed" if resolved, "still_open" if not addressed
3. New issues discovered → status: "new"

## Output Format

When finished with your analysis, provide your findings in the
FileReviewLLMOutput structured format.

System time: {system_time}
"""


def build_review_graph(
    query_service: CodeSearchService,
    repo_id: str | None = None,
):
    """Build the Review Agent using create_react_agent with structured output.

    Uses response_format for automatic structured output handling.
    The agent's result['structured_response'] will contain FileReviewLLMOutput.

    Args:
        query_service: Neo4j query service for code search
        repo_id: Repository ID to scope queries

    Returns:
        Compiled ReAct agent that returns structured review output
    """
    code_tools = get_tools(query_service, repo_id=repo_id)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(system_time=datetime.now(tz=UTC).isoformat())

    if repo_id:
        system_prompt += (
            f"\n\nActive repository: **{repo_id}** — all tools are scoped to this repo."
        )

    agent = create_react_agent(
        model=load_chat_model(config.review_model),
        tools=code_tools,
        prompt=system_prompt,
        response_format=FileReviewLLMOutput,
    )

    return agent
