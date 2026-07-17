"""Linter tools configuration API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_uid
from common.firebase_models import ToolsConfig
from common.firebase_service import firebase_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config", response_model=ToolsConfig)
async def get_tools_config(uid: str = Depends(get_current_uid)) -> ToolsConfig:
    """Return the user's linter tools configuration."""
    try:
        return firebase_service.get_tools_config(uid)
    except Exception as exc:
        logger.exception("Failed to read tools config: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read tools config")


@router.put("/config", response_model=ToolsConfig)
async def save_tools_config(
    config: ToolsConfig,
    uid: str = Depends(get_current_uid),
) -> ToolsConfig:
    """Save the user's linter tools configuration."""
    try:
        firebase_service.save_tools_config(uid, config)
        return config
    except Exception as exc:
        logger.exception("Failed to save tools config: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save tools config")
