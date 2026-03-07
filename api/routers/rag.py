from fastapi import APIRouter, Depends, Request

from api.dependencies import get_neo4j_client
from api.models.chat import RagInput, RagOutput
from api.services.rag_service import run_rag
from db import CodeSearchService, Neo4jClient

router = APIRouter()


@router.post("/answer", response_model=RagOutput)
async def answer(body: RagInput, request: Request, neo4j: Neo4jClient = Depends(get_neo4j_client)):
    uid = request.state.user["uid"]

    search_service = CodeSearchService(neo4j)
    response = await run_rag(body.question, search_service)

    return RagOutput(answer=response)
