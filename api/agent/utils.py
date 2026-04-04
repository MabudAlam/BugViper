"""Utility helpers for the BugViper agent."""

import os

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def load_gemini_model(model: str) -> BaseChatModel:
    """Load a Gemini model via OpenRouter.

    Routes through OpenRouter to avoid langchain_google_genai / Google GenAI
    SDK v2 compatibility issues. OpenRouter supports all Gemini models.

    Args:
        model: Gemini model name (e.g. 'gemini-3.1-pro-preview').

    Returns:
        Configured ChatOpenAI instance pointing at OpenRouter.
    """
    return ChatOpenAI(
        model=f"google/{model}",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )


def load_chat_model(model: str) -> BaseChatModel:
    """Load a chat model via OpenRouter.

    Routes to the appropriate provider based on the model name prefix.
    - 'gemini/' prefix -> ChatOpenAI via OpenRouter (google/ prefix)
    - All others -> ChatOpenAI via OpenRouter as-is
    """
    if model.startswith("gemini/"):
        gemini_model_name = model.replace("gemini/", "", 1)
        return load_gemini_model(gemini_model_name)

    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )


def load_chat_model(model: str) -> BaseChatModel:
    """Load a chat model via OpenRouter.

    Routes to the appropriate provider based on the model name prefix.
    - 'gemini/' prefix -> ChatOpenAI via OpenRouter (google/ prefix)
    - All others -> ChatOpenAI via OpenRouter as-is
    """
    if model.startswith("gemini/"):
        gemini_model_name = model.replace("gemini/", "", 1)
        return load_gemini_model(gemini_model_name)

    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )
