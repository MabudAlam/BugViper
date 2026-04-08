from pydantic import BaseModel


class Source(BaseModel):
    path: str
    line_number: int | None = None
    name: str | None = None
    type: str | None = None  # "function" | "class" | "variable" | "file" | etc.


class AskRequest(BaseModel):
    question: str
    repo_id: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source] = []


# ── History fetch (GET /sessions/{id}) ──────────────────────────────────────


class ChatMessage(BaseModel):
    role: str  # "human" | "ai"
    content: str
    sources: list[Source] = []


class SessionHistory(BaseModel):
    session_id: str
    repo_id: str | None = None
    messages: list[ChatMessage] = []
