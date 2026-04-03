"""Agent configuration with OpenRouter support.

Reads configuration ONLY from .env file, not from system environment.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TokenLimitsConfig(BaseSettings):
    """Token/character limits for context building.

    These limits control how much context is included in prompts.
    Optimized for cost-efficiency while maintaining review quality.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Diff truncation - REDUCED for cost
    diff_max_chars: int = Field(
        default=15_000,
        description="Max characters for diff text. Optimized for cost.",
    )

    # File content limits - REDUCED for cost
    file_max_chars: int = Field(
        default=8_000,
        description="Max characters per file in review prompt.",
    )

    # Imported symbol limits - REDUCED for cost
    imported_symbol_max_chars: int = Field(
        default=1_500,
        description="Max characters per imported symbol source.",
    )

    # AST context limits - REDUCED for cost
    function_source_max_chars: int = Field(
        default=1_500,
        description="Max characters for function source in AST context.",
    )
    class_source_max_chars: int = Field(
        default=2_000,
        description="Max characters for class source in AST context.",
    )

    # Docstring limits
    docstring_max_chars: int = Field(
        default=300,
        description="Max characters for docstring preview.",
    )

    # External calls limits - REDUCED for cost
    external_calls_max_count: int = Field(
        default=15,
        description="Max number of external calls to include in context.",
    )
    external_callers_max_count: int = Field(
        default=3,
        description="Max callers to show per external symbol.",
    )

    # High-usage symbol threshold
    high_usage_call_threshold: int = Field(
        default=3,
        description="Min calls to pre-fetch external symbol source.",
    )

    # NEW: Explorer context cap
    explorer_context_max_chars: int = Field(
        default=5_000,
        description="Max characters for Explorer agent output.",
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
        default="openai/gpt-4o-mini",
        description="Reasoning model used by the Review Agent (Phase 2). "
        "Set SYNTHESIS_MODEL in .env to override.",
    )
    review_agent_max_rounds: int = Field(
        default=5,
        description="Max tool-call rounds the Review Agent may use for targeted verification.",
    )
    enable_logfire: bool = Field(default=False)
    logfire_token: Optional[str] = Field(default=None)

    # NEW: Explorer toggle
    enable_explorer: bool = Field(
        default=False,
        description="Enable Explorer agent for context gathering. "
        "Disable for fast/simple reviews (saves ~50% cost).",
    )
    enable_explorer_threshold: int = Field(
        default=3,
        description="Auto-enable Explorer if files_changed >= threshold. "
        "Set to 0 to always disable, 1 to always enable.",
    )

    # NEW: File content thresholds
    max_file_content_lines: int = Field(
        default=300,
        description="Only send full file if line count > this threshold.",
    )
    send_full_file_threshold: int = Field(
        default=30,
        description="Send full file if changed_lines > this threshold.",
    )

    # NEW: Use 3-node agent (Explorer → Reviewer → Summarizer)
    use_3node_agent: bool = Field(
        default=True,
        description="Use the new 3-node agent architecture (recommended). "
        "Set to False to use the old 2-node agent.",
    )

    # NEW: Concurrency limits
    max_concurrent_files_small: int = Field(
        default=2,
        description="Max concurrent files for small PRs (1-2 files).",
    )
    max_concurrent_files_medium: int = Field(
        default=2,
        description="Max concurrent files for medium PRs (3-5 files).",
    )
    max_concurrent_files_large: int = Field(
        default=3,
        description="Max concurrent files for large PRs (6+ files).",
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
token_limits = TokenLimitsConfig()
