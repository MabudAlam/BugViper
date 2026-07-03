# ruff: noqa: E402
from dotenv import load_dotenv

load_dotenv(override=True)

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.firebase_auth import FirebaseAuthMiddleware
from api.routers import auth, repository, support, webhook
from common.firebase_service import firebase_service  # noqa: F401 — init on import

logger = logging.getLogger(__name__)


def _load_allowed_origins() -> list[str]:
    raw = os.getenv("API_ALLOWED_ORIGINS", "").strip()
    if not raw:
        logger.warning(
            "API_ALLOWED_ORIGINS is not set — defaulting to localhost only. "
            "Set this variable in .env for production."
        )
        return ["http://localhost:3000", "http://localhost:8000"]
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    logger.info("CORS allowed origins: %s", origins)
    return origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    logger.info("Shutting down application...")


app = FastAPI(
    title="BugViper API",
    description="AI-powered code review and repository intelligence.",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_load_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(FirebaseAuthMiddleware)

app.include_router(webhook.router, prefix="/api/v1/webhook", tags=["Webhook"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(support.router, prefix="/api/v1/support", tags=["Support"])
app.include_router(repository.router, prefix="/api/v1/repos", tags=["Repositories"])


def run_server():
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    run_server()
