from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from code_review_agent.agent.review_prompt import REVIEW_AGENT_PROMPT
from code_review_agent.agent.tools import get_tools
from code_review_agent.agent.utils import load_chat_model
from code_review_agent.config import config
from code_review_agent.models.file_review import FileReviewLLMOutput
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


def build_review_agent(
    query_service: CodeSearchService,
    repo_id: str | None = None,
):
    """Build the Review Agent using LangChain's create_agent.

    Paradigm: State + Tools + Graph + Prompt

    Args:
        query_service: Neo4j query service for code search
        repo_id: Repository ID to scope queries

    Returns:
        AgentExecutor with the compiled review graph
    """
    tools: list[BaseTool] = get_tools(query_service, repo_id=repo_id)
    model: BaseChatModel = load_chat_model(config.review_model)

    system_prompt = REVIEW_AGENT_PROMPT.format(system_time=datetime.now(tz=UTC).isoformat())

    agent = create_agent(
        model=model,
        tools=tools,
        response_format=ToolStrategy(
            schema=FileReviewLLMOutput,
            tool_message_content="Code review complete. Here is the structured review result.",
        ),
        system_prompt=system_prompt,
    )

    return agent
