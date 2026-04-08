"""Agent configuration.

Reads configuration ONLY from .env file, not from system environment.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    review_model: str = Field(default="openai/gpt-4o-mini")
    synthesis_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Reasoning model used by the Review Agent (Phase 2). "
        "Set SYNTHESIS_MODEL in .env to override.",
    )

    enable_pr_description_update: bool = Field(
        default=True,
        description="Allow agent to update PR description with review summary.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings, dotenv_settings, file_secret_settings)


config = AgentConfig()
