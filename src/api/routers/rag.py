from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from langchain_core.messages import HumanMessage

from api.agent.graph import build_graph
from api.dependencies import get_current_user
from api.models.rag import AskRequest, AskResponse, ChatMessage, SessionHistory
from api.routers.query import get_query_service
from common.chat_history import FirestoreChatHistory
from db.code_serarch_layer import CodeSearchService

router = APIRouter()


@router.post("/answer", response_model=AskResponse)
async def answer(
    body: AskRequest,
    query_service: CodeSearchService = Depends(get_query_service),
    current_user: dict = Depends(get_current_user),
) -> AskResponse:
    """
    ReAct agent endpoint — reasons over the codebase using Neo4j tools.

    Session history is automatically scoped to the authenticated user + repo.
    """
    try:
        uid: str = current_user["uid"]
        history = FirestoreChatHistory(uid=uid, repo_id=body.repo_id)
        prior_messages = history.get_messages()

        agent = build_graph(query_service, repo_id=body.repo_id)
        result = await agent.ainvoke(
            {"messages": [*prior_messages, HumanMessage(content=body.question)]}
        )

        answer_text: str = result["messages"][-1].content
        sources = result.get("sources", [])

        history.append_turn(
            body.question,
            answer_text,
            sources=[s.model_dump() for s in sources],
        )

        return AskResponse(answer=answer_text, sources=sources)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-session", response_model=SessionHistory)
async def get_my_session(
    repo_id: str = Query(description="Repository ID in 'owner/repo' format"),
    current_user: dict = Depends(get_current_user),
) -> SessionHistory:
    """Return the conversation history for the current user + repo."""
    uid: str = current_user["uid"]
    history = FirestoreChatHistory(uid=uid, repo_id=repo_id)
    data = history.get_session_data()
    sid = repo_id.strip().replace("/", "_")

    def _extract_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    text_parts.append(block.text)
            return "\n".join(text_parts) if text_parts else ""
        return str(content) if content else ""

    messages = [
        ChatMessage(
            role=m["type"],
            content=_extract_content(m.get("content")),
            sources=m.get("sources", []),
        )
        for m in data.get("messages", [])
    ]
    return SessionHistory(session_id=sid, repo_id=repo_id, messages=messages)


@router.delete("/my-session", status_code=204)
async def clear_my_session(
    repo_id: str = Query(description="Repository ID in 'owner/repo' format"),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete all chat history for the given repo."""
    uid: str = current_user["uid"]
    FirestoreChatHistory(uid=uid, repo_id=repo_id).clear()
