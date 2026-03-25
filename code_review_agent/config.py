"""Agent configuration with OpenRouter support.

Reads configuration ONLY from .env file, not from system environment.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TokenLimitsConfig(BaseSettings):
    """Token/character limits for context building.

    These limits control how much context is included in prompts.
    Adjust based on your model's context window.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Diff truncation
    diff_max_chars: int = Field(
        default=120_000,
        description="Max characters for diff text. Large PRs may need more.",
    )

    # File content limits
    file_max_chars: int = Field(
        default=40_000,
        description="Max characters per file in review prompt.",
    )

    # Imported symbol limits
    imported_symbol_max_chars: int = Field(
        default=6_000,
        description="Max characters per imported symbol source.",
    )

    # AST context limits
    function_source_max_chars: int = Field(
        default=4_000,
        description="Max characters for function source in AST context.",
    )
    class_source_max_chars: int = Field(
        default=6_000,
        description="Max characters for class source in AST context.",
    )

    # Docstring limits
    docstring_max_chars: int = Field(
        default=500,
        description="Max characters for docstring preview.",
    )

    # External calls limits
    external_calls_max_count: int = Field(
        default=100,
        description="Max number of external calls to include in context.",
    )
    external_callers_max_count: int = Field(
        default=10,
        description="Max callers to show per external symbol.",
    )

    # High-usage symbol threshold
    high_usage_call_threshold: int = Field(
        default=3,
        description="Min calls to pre-fetch external symbol source.",
    )


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
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings, dotenv_settings, file_secret_settings)


config = AgentConfig()
token_limits = TokenLimitsConfig()
