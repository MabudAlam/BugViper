from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GithubPrFiles(BaseModel):
    filename: str
    fileContent: str


class GithubPrMeta(BaseModel):
    prTitle: str
    prBody: str


class GithubPrDetails(BaseModel):
    difftext: str
    prMeta: GithubPrMeta
    head_sha: str
    base_sha: str
    head_branch: str
    files: list[GithubPrFiles]


class RepoDetails(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    description: Optional[str] = None
    private: Optional[bool] = None
    default_branch: Optional[str] = None
    language: Optional[str] = None
    size: Optional[int] = None
    stars: Optional[int] = None
    forks: Optional[int] = None
    topics: list[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
