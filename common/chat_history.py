from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from firebase_admin import firestore
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from common.firebase_init import _initialize_firebase

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

COLLECTION = "chat_sessions"
MAX_MESSAGES = 40  # 20 conversation turns (human + ai per turn)


class FirestoreChatHistory:
    """Read/write conversation history from Firestore for a single session."""

    def __init__(self, session_id: str, repo_id: str | None = None) -> None:
        self._session_id = session_id
        self._repo_id = repo_id
        db = _initialize_firebase()
        self._ref = db.collection(COLLECTION).document(session_id)

    def get_messages(self) -> list[AnyMessage]:
        """Return the stored message list as LangChain message objects."""
        doc = self._ref.get()
        if not doc.exists:
            return []
        raw: list[dict] = (doc.to_dict() or {}).get("messages", [])
        return _deserialize(raw)

    def get_session_data(self) -> dict:
        """Return the raw Firestore document for UI display (includes sources)."""
        doc = self._ref.get()
        if not doc.exists:
            return {"messages": [], "repo_id": self._repo_id}
        data = doc.to_dict() or {}
        return {"messages": data.get("messages", []), "repo_id": data.get("repo_id")}

    def append_turn(self, question: str, answer: str, sources: list[dict] | None = None) -> None:
        """Append a human+ai pair and persist.  Trims to MAX_MESSAGES."""
        doc = self._ref.get()
        if doc.exists:
            messages: list[dict] = (doc.to_dict() or {}).get("messages", [])
        else:
            messages = []

        messages.append({"type": "human", "content": question})
        messages.append({"type": "ai", "content": answer, "sources": sources or []})

        # Sliding window — keep only the most recent entries
        if len(messages) > MAX_MESSAGES:
            messages = messages[-MAX_MESSAGES:]

        self._ref.set(
            {
                "messages": messages,
                "repo_id": self._repo_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        logger.debug(
            "chat_history: saved turn for session=%s (total=%d)", self._session_id, len(messages)
        )

    def clear(self) -> None:
        """Delete the session document (resets history)."""
        self._ref.delete()
        logger.info("chat_history: cleared session=%s", self._session_id)


def _deserialize(raw: list[dict]) -> list[AnyMessage]:
    messages: list[AnyMessage] = []
    for entry in raw:
        msg_type = entry.get("type")
        content = entry.get("content", "")
        if msg_type == "human":
            messages.append(HumanMessage(content=content))
        elif msg_type == "ai":
            messages.append(AIMessage(content=content))
    return messages
