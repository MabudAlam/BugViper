import os

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def load_gemini_model(model: str) -> BaseChatModel:
    return ChatOpenAI(
        model=f"google/{model}",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )


def load_chat_model(model: str) -> BaseChatModel:
    if model.startswith("gemini/"):
        gemini_model_name = model.replace("gemini/", "", 1)
        return load_gemini_model(gemini_model_name)

    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=OPENROUTER_BASE_URL,
    )
