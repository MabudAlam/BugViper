from typing import List, Optional

from pydantic import BaseModel, Field


class GitHubIngestRequest(BaseModel):
    owner: str
    repo_name: str
    branch: Optional[str] = None
    clear_existing: bool = False


class IngestRequest(BaseModel):
    """Request schema for repository ingestion."""

    repo_url: str = Field(..., description="Git repository URL")
    username: Optional[str] = Field(None, description="Owner/user name")
    repo_name: Optional[str] = Field(None, description="Repository name")
    clear_existing: bool = Field(False, description="Clear existing data")
    languages: Optional[List[str]] = Field(None, description="Languages to process")


class IngestResponse(BaseModel):
    """Response schema for repository ingestion."""

    message: str
    status: str


class QueryResponse(BaseModel):
    """Basic response schema for queries."""

    message: str
    data: Optional[dict] = None


class IngestionJobResponse(BaseModel):
    """Returned immediately when an ingestion job is created."""

    job_id: str
    status: str
    message: str
    poll_url: str


class JobStatusResponse(BaseModel):
    """Full job status returned by the polling endpoint."""

    job_id: str
    owner: str
    repo_name: str
    branch: Optional[str] = None
    status: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stats: Optional[dict] = None
    error_message: Optional[str] = None
