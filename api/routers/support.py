"""Customer support router — public endpoint (no auth required)."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, field_validator

from api.services.firebase_service import firebase_service

logger = logging.getLogger(__name__)

router = APIRouter()


class SupportQueryRequest(BaseModel):
    name: str
    email: EmailStr
    subject: str
    category: str
    message: str
    priority: str = "medium"

    @field_validator("name", "subject", "message")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        allowed = {"bug_report", "feature_request", "general_inquiry", "billing", "other"}
        if v not in allowed:
            raise ValueError(f"category must be one of {sorted(allowed)}")
        return v

    @field_validator("priority")
    @classmethod
    def valid_priority(cls, v: str) -> str:
        allowed = {"low", "medium", "high"}
        if v not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return v


class SupportQueryResponse(BaseModel):
    query_id: str
    message: str


@router.post("/query", response_model=SupportQueryResponse)
async def submit_support_query(body: SupportQueryRequest) -> SupportQueryResponse:
    """
    Submit a customer support query.

    Stored in the Firestore ``customer_queries`` top-level collection.
    This endpoint is public — no Firebase auth token required.
    """
    try:
        query_id = firebase_service.save_customer_query(
            name=body.name,
            email=body.email,
            subject=body.subject,
            category=body.category,
            message=body.message,
            priority=body.priority,
        )
    except Exception as exc:
        logger.exception("Failed to save customer query: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to submit support query. Please try again."
        )

    return SupportQueryResponse(
        query_id=query_id,
        message="Your query has been submitted. We'll get back to you soon.",
    )
