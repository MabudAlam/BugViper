from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Optional

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import MessagesState
from langgraph.prebuilt import create_react_agent

from code_review_agent.agent.tools import get_tools
from code_review_agent.agent.utils import load_chat_model
from code_review_agent.config import config
from code_review_agent.models.file_review import FileReviewLLMOutput
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are BugViper, a senior code reviewer. Find real bugs in the diff — logic errors, security issues, production problems.

---

## PREVIOUS ISSUES TRACKING

If a "Previous Review Findings" section exists in the prompt:

1. **READ THE CURRENT CODE IN THE DIFF** to see if the issue is already handled
2. Mark `status: fixed` if:
   - The code NOW has a guard clause (`if x:`, `if x else default`)
   - The code NOW has a default value (`x or 0`, `x or []`)
   - The code NOW has try/except handling
   - A validation was added
3. Mark `status: still_open` ONLY if the problem is still NOT addressed
4. New issues you discover → `status: new`

---

## BUG PATTERNS TO CHECK

**BEFORE reporting any issue, check if the code already handles it:**
- Look for `or 0`, `if x else`, `if x is not None`, `try/except` blocks
- If the edge case IS already handled, DO NOT report it

**Correctness:**
- Division by zero, modulo by zero (NOT already guarded by if/else)
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

---

## TOOLS AVAILABLE

You have 4 tools to verify code:
- **verify_finding** — read code around a line to verify a potential issue
- **get_function_callers** — find who calls a function (impact analysis)
- **get_function_info** — get function source + complexity score
- **find_function_in_file** — find a function in a specific file

**ALWAYS use tools to verify facts before reporting issues.**

---

## FINAL OUTPUT

When you have finished your investigation, call the `submit_review` tool with your findings.

IMPORTANT: After calling `submit_review`, do NOT add any additional text. Just return the structured data.

System time: {system_time}
"""


class ReviewState(MessagesState):
    remaining_steps: int = 10
    structured_response: Optional[FileReviewLLMOutput] = None
    sources: list = []


def post_model_hook(state: ReviewState) -> dict:
    """Extract structured response from submit_review tool call."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and hasattr(last, "tool_calls") and last.tool_calls:
        for tool_call in last.tool_calls:
            if tool_call["name"] == "submit_review":
                return {"structured_response": FileReviewLLMOutput(**tool_call["args"])}
    return {}


def create_submit_review_tool() -> BaseTool:
    """Create the submit_review tool from the Pydantic model."""

    @tool("submit_review")
    def submit_review(
        walk_through: list[str],
        issues: list[dict],
        positive_findings: list[str],
    ) -> dict:
        """Submit your final code review findings.

        Call this tool when you have finished investigating the code and are ready
        to submit your review. This should be your FINAL action.

        Args:
            walk_through: List of one-sentence descriptions of what changed in each file.
            issues: List of issues found. Each issue must have: issue_type, category,
                    title, file, line_start, line_end, description, suggestion, impact,
                    code_snippet, confidence (5-9), ai_fix, ai_agent_prompt, status.
            positive_findings: List of 3-6 good patterns found in the code.

        Returns:
            The structured review data.
        """
        return {
            "walk_through": walk_through,
            "issues": issues,
            "positive_findings": positive_findings,
        }

    return submit_review


def build_review_graph(
    query_service: CodeSearchService,
    repo_id: str | None = None,
):
    """Build the Review Agent using create_react_agent with Pydantic tool.

    The agent uses the submit_review tool to provide structured output.
    This avoids the extra LLM call that response_format requires.

    Args:
        query_service: Neo4j query service for code search
        repo_id: Repository ID to scope queries

    Returns:
        Compiled ReAct agent with structured output via tool
    """
    code_tools: list[BaseTool] = get_tools(query_service, repo_id=repo_id)
    submit_tool = create_submit_review_tool()

    all_tools = code_tools + [submit_tool]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(system_time=datetime.now(tz=UTC).isoformat())

    if repo_id:
        system_prompt += (
            f"\n\nActive repository: **{repo_id}** — all tools are scoped to this repo."
        )

    agent = create_react_agent(
        model=load_chat_model(config.review_model),
        tools=all_tools,
        prompt=system_prompt,
        state_schema=ReviewState,
        post_model_hook=post_model_hook,
    )

    return agent
