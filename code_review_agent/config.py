"""Agent configuration with OpenRouter support.

Reads configuration ONLY from .env file, not from system environment.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openrouter_api_key: str = Field(default="sk-or-v1-placeholder")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")
    review_model: str = Field(default="openai/gpt-4o-mini")
    synthesis_model: str = Field(
        default="google/gemini-2.5-pro-preview-03-25",
        description="Reasoning model used by the Review Agent (Phase 2). "
                    "Set SYNTHESIS_MODEL in .env to override.",
    )
    review_agent_max_rounds: int = Field(
        default=5,
        description="Max tool-call rounds the Review Agent may use for targeted verification.",
    )
    enable_logfire: bool = Field(default=False)
    logfire_token: Optional[str] = Field(default=None)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings,
    ):
        return (init_settings, dotenv_settings, file_secret_settings)


config = AgentConfig()
