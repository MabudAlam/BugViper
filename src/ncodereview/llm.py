import os

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"


def load_gemini_model(model: str) -> BaseChatModel:
    llm = ChatOpenAI(
        model=f"google/{model}",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )
    llm.profile = {"max_input_tokens": 128000}
    return llm


def load_minimax_model(model: str) -> BaseChatModel:
    """Load a model via MiniMax's OpenAI-compatible endpoint.

    Requires `MINIMAX_API_KEY` in the environment.
    """
    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set")
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=MINIMAX_BASE_URL,
    )
    llm.profile = {"max_input_tokens": 128000}
    return llm


def load_chat_model(model: str) -> BaseChatModel:
    if model.startswith("gemini/"):
        return load_gemini_model(model.replace("gemini/", "", 1))
    if model.startswith("MiniMax-") or model.startswith("MiniMax/"):
        return load_minimax_model(model)

    llm = ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )
    llm.profile = {"max_input_tokens": 128000}
    return llm
