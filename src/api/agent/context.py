"""Define the configurable parameters for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Annotated

from api.agent import prompts


@dataclass(kw_only=True)
class Context:
    """The context for the agent."""

    system_prompt: str = field(
        default=prompts.SYSTEM_PROMPT,
        metadata={
            "description": "The system prompt to use for the agent's interactions. "
            "This prompt sets the context and behavior for the agent."
        },
    )

    model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default="openai/gpt-4o-mini",
        metadata={
            "description": "The language model to use in 'provider/model-name' format. "
            "Routed through OpenRouter."
        },
    )

    max_search_results: int = field(
        default=10,
        metadata={"description": "The maximum number of search results to return per query."},
    )

    def __post_init__(self) -> None:
        """Fetch env vars for attributes that were not passed as args."""
        for f in fields(self):
            if not f.init:
                continue
            if getattr(self, f.name) == f.default:
                if f.name == "model":
                    setattr(
                        self,
                        f.name,
                        os.environ.get("RAG_MODEL") or os.environ.get("MODEL") or f.default,
                    )
                else:
                    setattr(self, f.name, os.environ.get(f.name.upper()) or f.default)
