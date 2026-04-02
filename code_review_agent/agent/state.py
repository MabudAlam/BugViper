from __future__ import annotations

from typing import Optional

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated

from code_review_agent.models.file_review import FileReviewLLMOutput


def _merge_sources(current: list, update: list) -> list:
    seen = {(s.get("path"), s.get("line_number")) for s in current if isinstance(s, dict)}
    result = list(current)
    for s in update:
        if isinstance(s, dict):
            key = (s.get("path"), s.get("line_number"))
            if key not in seen:
                seen.add(key)
                result.append(s)
    return result


class ReviewState(dict):
    messages: Annotated[list[AnyMessage], add_messages]
    structured_response: Optional[FileReviewLLMOutput] = None
    sources: Annotated[list, _merge_sources] = []


class ReviewExplorerState(dict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int = 0
