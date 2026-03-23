from fastapi import APIRouter, Depends, HTTPException, Query
from langchain_core.messages import HumanMessage

from api.agent.graph import build_graph
from api.dependencies import get_current_user
from api.models.rag import AskRequest, AskResponse, ChatMessage, SessionHistory
from api.routers.query import get_query_service
from common.chat_history import FirestoreChatHistory
from db.code_serarch_layer import CodeSearchService

router = APIRouter()


def _session_id(uid: str, repo_id: str | None) -> str:
    """Deterministic session key scoped to user + repo.

    Format: ``{uid}__{owner}_{repo}``  or  ``{uid}__all``

    The repo_id (e.g. "owner/repo") has its slash replaced with "_" so the
    resulting string is a valid single-segment Firestore document ID — Firestore
    interprets "/" as a path separator and rejects IDs that contain it.


    """
    if repo_id and repo_id.strip():
        safe_repo = repo_id.strip().replace("/", "_")
        scope = safe_repo
    else:
        scope = "all"
    return f"{uid}__{scope}"


def _build_history(uid: str, repo_id: str | None) -> FirestoreChatHistory:
    sid = _session_id(uid, repo_id)
    return FirestoreChatHistory(session_id=sid, repo_id=repo_id)


@router.post("/answer", response_model=AskResponse)
async def answer(
    body: AskRequest,
    query_service: CodeSearchService = Depends(get_query_service),
    current_user: dict = Depends(get_current_user),
) -> AskResponse:
    """
    ReAct agent endpoint — reasons over the codebase using Neo4j tools.

    Session history is automatically scoped to the authenticated user + repo.
    No session token is needed from the client.
    """
    try:
        uid: str = current_user["uid"]
        history = _build_history(uid, body.repo_id)
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
    repo_id: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> SessionHistory:
    """Return the conversation history for the current user + repo (for UI reload)."""
    uid: str = current_user["uid"]
    sid = _session_id(uid, repo_id)
    data = FirestoreChatHistory(session_id=sid, repo_id=repo_id).get_session_data()
    messages = [
        ChatMessage(
            role=m["type"],
            content=m.get("content", ""),
            sources=m.get("sources", []),
        )
        for m in data.get("messages", [])
    ]
    return SessionHistory(session_id=sid, repo_id=repo_id, messages=messages)


@router.delete("/my-session", status_code=204)
async def clear_my_session(
    repo_id: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete the current user's session for the given repo (or all-repos view)."""
    uid: str = current_user["uid"]
    _build_history(uid, repo_id).clear()
