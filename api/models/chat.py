from typing import Optional

from pydantic import BaseModel


class RagInput(BaseModel):
    question: str
    repoName: Optional[str] = None
    repoOwner: Optional[str] = None


class SemanticHit(BaseModel):
    name: Optional[str] = None
    type: str
    path: Optional[str] = None
    line_number: Optional[int] = None
    source_code: Optional[str] = None
    docstring: Optional[str] = None
    score: float


class SemanticSearchResponse(BaseModel):
    results: list[SemanticHit]
    total: int
