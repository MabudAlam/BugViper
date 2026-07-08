"""Auth router — thin HTTP layer delegating to firebase_service and GitHub."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_user
from common.firebase_service import firebase_service
from common.github_client import GitHubClient

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    github_access_token: str


class UserProfile(BaseModel):
    uid: str
    email: Optional[str] = None
    displayName: Optional[str] = None
    githubUsername: Optional[str] = None
    photoURL: Optional[str] = None
    createdAt: Optional[str] = None
    githubInstallationId: Optional[int] = None
    accountType: Optional[str] = None
    repositorySelection: Optional[str] = None


class GitHubRepo(BaseModel):
    name: str
    full_name: str
    description: Optional[str] = None
    language: Optional[str] = None
    stargazers_count: int = 0
    private: bool = False
    default_branch: str = "main"
    html_url: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=UserProfile)
def login(body: LoginRequest, user: dict = Depends(get_current_user)):
    """Sign-in: fetch GitHub profile, then create/update Firestore user doc."""
    try:
        gh_profile = GitHubClient.fetch_user_profile(body.github_access_token)
        profile = firebase_service.create_or_update_user(
            uid=user["uid"],
            github_access_token=body.github_access_token,
            github_profile=gh_profile,
            firebase_claims=user,
        )
        return UserProfile(**profile.model_dump(by_alias=True))
    except Exception as exc:
        logger.exception("Login failed for uid=%s", user.get("uid"))
        raise HTTPException(status_code=500, detail="Login failed") from exc


@router.post("/ensure", response_model=UserProfile)
def ensure_user(user: dict = Depends(get_current_user)):
    """Ensure user doc exists for returning sessions (no GitHub token needed)."""
    try:
        profile = firebase_service.ensure_user(
            uid=user["uid"],
            firebase_claims=user,
        )
        return UserProfile(**profile.model_dump(by_alias=True))
    except Exception as exc:
        logger.exception("Ensure user failed for uid=%s", user.get("uid"))
        raise HTTPException(status_code=500, detail="Failed to ensure user") from exc


@router.get("/me", response_model=UserProfile)
def get_me(user: dict = Depends(get_current_user)):
    """Return current user profile from Firestore."""
    profile = firebase_service.get_user(uid=user["uid"])
    if profile is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfile(**profile.model_dump(by_alias=True))


@router.get("/github/repos", response_model=list[GitHubRepo])
def get_github_repos(user: dict = Depends(get_current_user)):
    """Fetch the authenticated user's GitHub repositories using stored token."""
    uid = user["uid"]
    token = firebase_service.get_github_token(uid)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="No GitHub token found. Please sign in with GitHub first.",
        )
    try:
        repos = GitHubClient.fetch_user_repos(token)
        return [GitHubRepo(**r) for r in repos]
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to fetch GitHub repos for uid=%s", uid)
        raise HTTPException(status_code=500, detail="Failed to fetch repositories") from exc


class InstallationStatus(BaseModel):
    installationId: Optional[int] = None
    githubUsername: Optional[str] = None
    linked: bool = False
    settingsUrl: Optional[str] = None


@router.get("/installation", response_model=InstallationStatus)
def get_installation_status(user: dict = Depends(get_current_user)):
    """Return GitHub App installation status for the current user."""
    uid = user["uid"]
    installation_id = firebase_service.get_user_installation(uid)
    github_username = firebase_service.get_user_github_username(uid)

    if installation_id:
        return InstallationStatus(
            installationId=installation_id,
            githubUsername=github_username,
            linked=True,
            settingsUrl=f"https://github.com/settings/installations/{installation_id}",
        )

    # Fallback: retry linking from pending installation
    if github_username:
        try:
            firebase_service._link_pending_installation(uid, github_username)
            installation_id = firebase_service.get_user_installation(uid)
            if installation_id:
                return InstallationStatus(
                    installationId=installation_id,
                    githubUsername=github_username,
                    linked=True,
                    settingsUrl=f"https://github.com/settings/installations/{installation_id}",
                )
        except Exception as exc:
            logger.exception("Failed to retry linking pending installation for uid=%s", uid)

    return InstallationStatus(
        githubUsername=github_username,
        linked=False,
    )
