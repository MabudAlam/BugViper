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

    e2b_api_key: str = Field(
        default="",
        description="E2B API key. https://e2b.dev/dashboard",
    )

    deepagent_model: str = Field(
        default="MiniMax-M2.7",
        description=(
            "Model id for the orchestrator and subagents. "
            "MiniMax- prefix → MiniMax OpenAI-compatible API. "
            "Anything else → OpenRouter."
        ),
    )

    deepagent_sandbox_timeout: int = Field(
        default=1800,
        description="E2B sandbox lifetime in seconds (default 30 min).",
    )

    deepagent_max_tool_rounds: int = Field(
        default=40,
        description="Soft cap on tool rounds for the orchestrator. Subagents inherit.",
    )

    deepagent_review_mode: str = Field(
        default="normal",
        description=(
            "Review depth: "
            "'normal' (generalist, ~20 steps/lens), "
            "'deep' (3 specialized agents in parallel, ~100 steps each)."
        ),
    )

    max_input_tokens: int = Field(
        default=128000,
        description=(
            "Maximum input tokens for the model (context window). "
            "Used for adaptive-fit profile selection and chunk budget computation. "
            "Thresholds: >=64K=full, 32-64K=compact, <32K=minimal."
        ),
    )

    e2b_sandbox_template_small: str = Field(
        default="",
        description=(
            "E2B sandbox template name for small batches (<=5 files). "
            "Use 2 vCPU template for lightweight reviews."
        ),
    )

    e2b_sandbox_template_large: str = Field(
        default="",
        description=(
            "E2B sandbox template name for large batches (>5 files). "
            "Use 4 vCPU template for resource-heavy reviews."
        ),
    )

    max_concurrent_sandboxes: int = Field(
        default=4,
        description=(
            "Maximum number of concurrent E2B sandboxes. "
            "Prevents hitting E2B rate/resource limits. Default: 4"
        ),
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
