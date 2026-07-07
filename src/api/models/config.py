from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel


class LLMProvider(Enum):
    MINIMAX = "minimax"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"


class ModelConfig(BaseModel):
    model_name: str
    key: str
    provider: LLMProvider


class ModelProvider(ABC):
    @abstractmethod
    def set_model(self, model_name: str, key: str): ...

    @abstractmethod
    def get_model(self) -> ModelConfig | None: ...


class MiniMaxModelProvider(ModelProvider):
    def __init__(self):
        self._config: ModelConfig | None = None

    def set_model(self, model_name: str, key: str):
        self._config = ModelConfig(
            model_name=model_name,
            key=key,
            provider=LLMProvider.MINIMAX,
        )

    def get_model(self) -> ModelConfig | None:
        return self._config
