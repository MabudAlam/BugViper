import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def ensure_env() -> None:
    if not os.getenv("OPENROUTER_API_KEY") and not os.getenv("MINIMAX_API_KEY"):
        from dotenv import load_dotenv

        load_dotenv(override=True)


class DeepAgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    E2B_API_KEY: str = Field(
        default="",
        description="E2B API key. https://e2b.dev/dashboard",
    )

    DEEPAGENT_CODE_REVIEW_MODEL: str = Field(
        default="MiniMax-M2.7",
        description=(
            "Model id for the orchestrator and subagents. "
            "MiniMax- prefix → MiniMax OpenAI-compatible API. "
            "Anything else → OpenRouter."
        ),
    )

    DEEPAGENT_CODE_REVIEW_MODEL_FALLBACK: list[str] = Field(
        default=["gpt-4o-mini"],
        description="Fallback models when the primary model fails.",
    )

    MODEL_RETRY_MAX_RETRIES: int = Field(
        default=3,
        description="Maximum retry attempts for model calls.",
    )

    MODEL_RETRY_BACKOFF_FACTOR: float = Field(
        default=2.0,
        description="Exponential backoff factor for retries.",
    )

    MODEL_RETRY_INITIAL_DELAY: float = Field(
        default=1.0,
        description="Initial delay in seconds before first retry.",
    )

    MODEL_RETRY_MAX_DELAY: float = Field(
        default=60.0,
        description="Maximum delay in seconds between retries.",
    )

    DEEPAGENT_SANDBOX_TIMEOUT: int = Field(
        default=1800,
        description="E2B sandbox lifetime in seconds (default 30 min).",
    )

    DEEPAGENT_MAX_TOOL_ROUNDS: int = Field(
        default=40,
        description="Soft cap on tool rounds for the orchestrator. Subagents inherit.",
    )

    DEEPAGENT_REVIEW_MODE: str = Field(
        default="normal",
        description=(
            "Review depth: "
            "'normal' (generalist, ~20 steps/lens), "
            "'deep' (3 specialized agents in parallel, ~100 steps each)."
        ),
    )

    MAX_INPUT_TOKENS: int = Field(
        default=128000,
        description=(
            "Maximum input tokens for the model (context window). "
            "Used for adaptive-fit profile selection and chunk budget computation. "
            "Thresholds: >=64K=full, 32-64K=compact, <32K=minimal."
        ),
    )

    E2B_SANDBOX_TEMPLATE_SMALL: str = Field(
        default="",
        description=(
            "E2B sandbox template name for small batches (<=5 files). "
            "Use 2 vCPU template for lightweight reviews."
        ),
    )

    E2B_SANDBOX_TEMPLATE_LARGE: str = Field(
        default="",
        description=(
            "E2B sandbox template name for large batches (>5 files). "
            "Use 4 vCPU template for resource-heavy reviews."
        ),
    )

    MAX_CONCURRENT_SANDBOXES: int = Field(
        default=4,
        description=(
            "Maximum number of concurrent E2B sandboxes. "
            "Prevents hitting E2B rate/resource limits. Default: 4"
        ),
    )

    VERIFIER_RUN_LIMIT_MULTIPLIER: int = Field(
        default=6,
        description="Multiplier for verifier run limit: min(findings * multiplier, max).",
    )

    VERIFIER_RUN_LIMIT_MAX: int = Field(
        default=50,
        description="Maximum run limit for the verifier agent.",
    )

    DEDUP_MODEL: str = Field(
        default="openai/gpt-4o-mini",
        description="Model ID for the deduplication step.",
    )

    DEDUP_CONTENT_THRESHOLD: float = Field(
        default=0.5,
        description="Minimum content similarity to consider findings as duplicates.",
    )

    VERIFIER_MODEL: str = Field(
        default="MiniMax-M2.7",
        description="Model ID for the verifier agent.",
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


config = DeepAgentConfig()
