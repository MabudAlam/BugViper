"""Health-check endpoint for Cloud Run."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "healthy"}
