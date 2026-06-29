from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepAgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    use_deepagent_review: bool = Field(
        default=False,
        description="Route @bugviper reviews to the sandboxed DeepAgent pipeline.",
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
