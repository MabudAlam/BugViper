# ruff: noqa: E402
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from ingestion_service.routers import health, incremental, ingest

app = FastAPI(title="BugViper Ingestion Worker", version="0.1.0")

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(incremental.router)
