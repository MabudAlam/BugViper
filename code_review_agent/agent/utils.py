"""Utility helpers for the BugViper agent."""

import os

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def load_chat_model(model: str, timeout: int = 180) -> BaseChatModel:
    """Load a chat model via OpenRouter.

    Passes the full model name (e.g. 'z-ai/glm-5', 'openai/gpt-4o') directly
    to ChatOpenAI with OpenRouter as the base URL. This works for any provider
    OpenRouter supports, regardless of whether LangChain knows the provider.

    ``timeout`` is the per-request timeout in seconds (default 180s / 3 min).
    """
    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
        timeout=timeout,
    )
