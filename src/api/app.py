# ruff: noqa: E402
from dotenv import load_dotenv

# Load environment variables BEFORE any imports that use them (e.g. Firebase)
# override=True makes local dev deterministic even if the shell exported old values.
load_dotenv(override=True)

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.firebase_auth import FirebaseAuthMiddleware
from api.routers import auth, ingestion, query, rag, repository, support, webhook
from api.services.firebase_service import firebase_service  # noqa: F401 — init on import

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
    """
    Application lifespan context manager.

    Handles startup and shutdown events.
    """
    # Startup
    yield
    # Shutdown - cleanup will be handled by new system


# Create FastAPI application
app = FastAPI(
    title="BugViper Code Ingestion API",
    description="""
    Advanced Multi-Language Code Analysis and Ingestion API.

    ## Features

    * **Multi-Language Support** - Python, TypeScript, JavaScript, Go, Rust, Java, C++, and more
    * **Advanced Code Analysis** - Complexity metrics, dead code detection, dependency analysis
    * **Intelligent Graph Storage** - Neo4j-based code relationships and structure
    * **Agent-Based Code Review** - AI-powered code analysis and review

    ## Getting Started

    1. Set up your Neo4j database credentials in environment variables
    2. Call `/api/v1/ingest/setup` to initialize the schema
    3. Use `/api/v1/ingest/repository` to ingest a repository
    4. Query your code using the various endpoints
    """,
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

# Firebase auth — runs on every request except OPTIONS + PUBLIC_PREFIXES.
# Note: Starlette middleware is LIFO — this runs BEFORE CORSMiddleware.
# OPTIONS preflight is bypassed inside the middleware so CORS can respond.
app.add_middleware(FirebaseAuthMiddleware)

# Include routers
app.include_router(ingestion.router, prefix="/api/v1/ingest", tags=["Ingestion"])

app.include_router(query.router, prefix="/api/v1/query", tags=["Query"])

app.include_router(repository.router, prefix="/api/v1/repos", tags=["Repository"])

app.include_router(webhook.router, prefix="/api/v1/webhook", tags=["Webhook"])

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])

app.include_router(rag.router, prefix="/api/v1/rag", tags=["Agent"])

app.include_router(support.router, prefix="/api/v1/support", tags=["Support"])


def run_server():
    """
    Run the API server.

    This function is used as an entry point for the CLI command.
    """
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    run_server()
