"""GitHub App client.

This module is the single integration point for GitHub App authentication and
GitHub REST API calls.

Implementation notes:
- Uses githubkit for HTTP, retries, and endpoint wrappers.
- Maintains small, explicit caches (installation token + PR payload) to avoid
  redundant requests and races.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from githubkit import GitHub
from githubkit.auth import AppAuthStrategy, TokenAuthStrategy
from githubkit.exception import GitHubException

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    """Base exception for GitHub authentication errors."""


class GitHubAppAccessError(GitHubAuthError):
    """GitHub App does not have access to the repository."""


class GitCloneError(GitHubAuthError):
    """Git command failed."""


class GitHubClient:
    """Async GitHub App client.

    Auth:
    - Uses AppAuthStrategy to resolve installation IDs and mint installation tokens.
    - Uses TokenAuthStrategy with the installation token for repo-scoped operations.
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
    ) -> None:
        raw_id = app_id or os.getenv("GITHUB_APP_ID")
        if not raw_id:
            raise ValueError("GitHub App ID not provided")
        try:
            self._app_id = int(raw_id)
        except ValueError as exc:
            raise ValueError(f"GITHUB_APP_ID must be an integer, got: {raw_id!r}") from exc

        key_path = private_key_path or os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not key_path:
            raise ValueError("GitHub private key path not provided")
        with open(key_path, encoding="utf-8") as fh:
            self._private_key = fh.read()

        # GitHub App API (JWT-based)
        self._app_gh = GitHub(
            auth=AppAuthStrategy(app_id=self._app_id, private_key=self._private_key),
            user_agent="BugViper-App",
            rest_api_validate_body=False,
            auto_retry=True,
            http_cache=True,
        )

        # Installation token cache: repo_full_name → (token, monotonic_expiry)
        self._token_cache: Dict[str, tuple[str, float]] = {}
        self._token_locks: Dict[str, asyncio.Lock] = {}

        # PR payload cache: (owner, repo, pr_number) → payload dict
        self._pr_cache: Dict[tuple[str, str, int], dict] = {}
        self._pr_locks: Dict[tuple[str, str, int], asyncio.Lock] = {}

        # Repo-scoped GitHub clients (installation token auth). We recreate
        # the client when the token rotates.
        self._repo_clients: Dict[str, tuple[str, GitHub]] = {}

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        # githubkit doesn't expose a public close API; it manages httpx clients
        # internally. Keeping the singleton alive is the intended usage.
        return None

    def clear_pr_cache(self, owner: str, repo: str, pr_number: int) -> None:
        """Clear cached PR payload for a single PR.

        BugViper caches the PR payload to keep base/head SHA consistent across
        calls during one review run. If the PR is updated with new commits,
        callers should clear this cache before starting a new run.
        """

        self._pr_cache.pop((owner, repo, pr_number), None)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    async def _get_installation_token(self, owner: str, repo: str) -> str:
        """Return a valid installation token for owner/repo.

        Tokens are cached until 5 minutes before their expiry.
        """

        cache_key = f"{owner}/{repo}"
        if cache_key in self._token_cache:
            token, expires_mono = self._token_cache[cache_key]
            if time.monotonic() < expires_mono:
                return token

        if cache_key not in self._token_locks:
            self._token_locks[cache_key] = asyncio.Lock()
        async with self._token_locks[cache_key]:
            if cache_key in self._token_cache:
                token, expires_mono = self._token_cache[cache_key]
                if time.monotonic() < expires_mono:
                    return token

            try:
                inst = await self._app_gh.rest.apps.async_get_repo_installation(owner, repo)
            except GitHubException as exc:
                if getattr(exc, "status_code", None) == 404:
                    raise GitHubAppAccessError(
                        f"GitHub App is not installed on {owner}/{repo}. "
                        "Install the app and ensure it has the required permissions."
                    ) from exc
                raise

            installation_id = int(getattr(inst.parsed_data, "id"))

            tok = await self._app_gh.rest.apps.async_create_installation_access_token(
                installation_id
            )
            token = str(getattr(tok.parsed_data, "token"))

            # expires_at is an ISO string in GitHub responses
            exp_raw = str(getattr(tok.parsed_data, "expires_at"))
            exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
            ttl = (exp_dt - datetime.now(timezone.utc)).total_seconds()
            self._token_cache[cache_key] = (token, time.monotonic() + ttl - 300)

            logger.debug("Obtained installation token for %s (ttl=%.0fs)", cache_key, ttl)
            return token

    async def _get_repo_gh(self, owner: str, repo: str) -> GitHub:
        """Return a repo-scoped GitHub client authenticated as the installation."""

        cache_key = f"{owner}/{repo}"
        token = await self._get_installation_token(owner, repo)

        existing = self._repo_clients.get(cache_key)
        if existing and existing[0] == token:
            return existing[1]

        gh = GitHub(
            auth=TokenAuthStrategy(token),
            user_agent="BugViper-App",
            rest_api_validate_body=False,
            auto_retry=True,
            http_cache=True,
        )
        self._repo_clients[cache_key] = (token, gh)
        return gh

    # ------------------------------------------------------------------
    # Repository helpers
    # ------------------------------------------------------------------

    async def check_repository_access(self, owner: str, repo: str) -> bool:
        try:
            gh = await self._get_repo_gh(owner, repo)
            r = await gh.rest.repos.async_get(owner, repo)
            return r.status_code == 200
        except Exception:
            return False

    async def get_repository_info(self, owner: str, repo: str) -> Dict[str, Any]:
        gh = await self._get_repo_gh(owner, repo)
        r = await gh.rest.repos.async_get(owner, repo)
        d = r.parsed_data

        # githubkit returns datetimes for created_at/updated_at; our API/Firebase
        # models store these as strings for JSON friendliness.
        created_at = getattr(d, "created_at", None)
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        updated_at = getattr(d, "updated_at", None)
        if hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat()
        return {
            "name": getattr(d, "name", None),
            "full_name": getattr(d, "full_name", None),
            "description": getattr(d, "description", None),
            "private": getattr(d, "private", None),
            "default_branch": getattr(d, "default_branch", None),
            "language": getattr(d, "language", None),
            "size": getattr(d, "size", None),
            "stars": getattr(d, "stargazers_count", None),
            "forks": getattr(d, "forks_count", None),
            "topics": getattr(d, "topics", None) or [],
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def clone_repository(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
        clone_dir: Optional[Path] = None,
    ) -> Path:
        token = await self._get_installation_token(owner, repo)

        if clone_dir is None:
            clone_dir = Path(tempfile.gettempdir()) / owner / repo
        else:
            clone_dir = Path(clone_dir) / owner / repo

        if clone_dir.exists():
            import shutil

            shutil.rmtree(clone_dir)
        clone_dir.parent.mkdir(parents=True, exist_ok=True)

        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([clone_url, str(clone_dir)])

        logger.info("Cloning %s/%s ...", owner, repo)
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

        if result.returncode != 0:
            stderr = result.stderr.replace(token, "***")
            if "Repository not found" in stderr:
                raise GitHubAppAccessError(
                    f"Repository '{owner}/{repo}' not found via git clone. "
                    "Ensure the GitHub App has Contents: Read permission."
                )
            raise GitCloneError(f"git clone failed for {owner}/{repo}:\n{stderr}")

        logger.info("Cloned %s/%s → %s", owner, repo, clone_dir)
        return clone_dir

    # ------------------------------------------------------------------
    # PR data (consistent snapshot)
    # ------------------------------------------------------------------

    async def _get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        cache_key = (owner, repo, pr_number)
        if cache_key not in self._pr_locks:
            self._pr_locks[cache_key] = asyncio.Lock()

        async with self._pr_locks[cache_key]:
            if cache_key not in self._pr_cache:
                gh = await self._get_repo_gh(owner, repo)
                r = await gh.rest.pulls.async_get(owner, repo, pr_number)
                pr_obj = r.parsed_data
                dump = getattr(pr_obj, "model_dump", None)
                if callable(dump):
                    self._pr_cache[cache_key] = dump()
                elif isinstance(pr_obj, dict):
                    self._pr_cache[cache_key] = pr_obj
                else:
                    # Last-resort fallback; should be rare.
                    self._pr_cache[cache_key] = dict(pr_obj)
        return self._pr_cache[cache_key]

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> Dict[str, str]:
        d = await self._get_pr(owner, repo, pr_number)
        return {"title": d.get("title") or "", "body": d.get("body") or ""}

    async def get_pr_head_ref(self, owner: str, repo: str, pr_number: int) -> str:
        d = await self._get_pr(owner, repo, pr_number)
        return str(d["head"]["sha"])

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        d = await self._get_pr(owner, repo, pr_number)
        base_sha = str(d["base"]["sha"])
        head_sha = str(d["head"]["sha"])

        gh = await self._get_repo_gh(owner, repo)
        r = await gh.arequest(
            "GET",
            f"/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        return r.text

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
        gh = await self._get_repo_gh(owner, repo)
        out: List[Dict[str, Any]] = []
        page = 1
        while True:
            r = await gh.rest.pulls.async_list_files(
                owner,
                repo,
                pr_number,
                page=page,
                per_page=100,
            )
            batch = list(r.parsed_data)
            if not batch:
                break
            for f in batch:
                out.append(
                    {
                        "filename": getattr(f, "filename", None),
                        "status": getattr(f, "status", None),
                        "additions": getattr(f, "additions", 0),
                        "deletions": getattr(f, "deletions", 0),
                        "changes": getattr(f, "changes", 0),
                        "patch": getattr(f, "patch", None),
                    }
                )
            if len(batch) < 100:
                break
            page += 1
        return out

    async def get_pr_commits(self, owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
        gh = await self._get_repo_gh(owner, repo)
        out: List[Dict[str, Any]] = []
        page = 1
        while True:
            r = await gh.rest.pulls.async_list_commits(
                owner,
                repo,
                pr_number,
                page=page,
                per_page=100,
            )
            batch = list(r.parsed_data)
            if not batch:
                break
            for c in batch:
                commit = getattr(c, "commit", None)
                msg = getattr(commit, "message", "") if commit else ""
                date = None
                if commit and getattr(commit, "author", None):
                    date = getattr(commit.author, "date", None)
                out.append(
                    {
                        "sha": getattr(c, "sha", None),
                        "message": (msg.split("\n")[0] if msg else ""),
                        "date": date,
                    }
                )
            if len(batch) < 100:
                break
            page += 1
        return out

    async def has_open_pr_for_branch(self, owner: str, repo: str, branch: str) -> bool:
        """Return True if there is an open PR whose head is owner:branch.

        Used to avoid ingesting direct pushes to a branch that is already
        represented by an open PR (which would be an intermediate, unmerged state).
        """

        gh = await self._get_repo_gh(owner, repo)
        head = f"{owner}:{branch}"
        r = await gh.rest.pulls.async_list(
            owner,
            repo,
            state="open",
            head=head,
            per_page=1,
            page=1,
        )
        return bool(list(r.parsed_data))

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        gh = await self._get_repo_gh(owner, repo)
        params = {"ref": ref} if ref else None
        try:
            r = await gh.arequest(
                "GET",
                f"/repos/{owner}/{repo}/contents/{path}",
                headers={"Accept": "application/vnd.github.raw+json"},
                params=params,
            )
        except GitHubException as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        return r.text

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        gh = await self._get_repo_gh(owner, repo)
        await gh.rest.issues.async_create_comment(owner, repo, pr_number, data={"body": body})

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
    ) -> None:
        gh = await self._get_repo_gh(owner, repo)
        await gh.rest.pulls.async_create_review(
            owner,
            repo,
            pr_number,
            data={
                "commit_id": commit_sha,
                "body": body,
                "event": event,
            },
        )

    async def post_inline_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        path: str,
        line: int,
        body: str,
    ) -> bool:
        gh = await self._get_repo_gh(owner, repo)
        try:
            await gh.rest.pulls.async_create_review_comment(
                owner,
                repo,
                pr_number,
                data={
                    "body": body,
                    "commit_id": commit_sha,
                    "path": path,
                    "line": line,
                    "side": "RIGHT",
                },
            )
            return True
        except GitHubException as exc:
            if getattr(exc, "status_code", None) == 422:
                logger.debug("Inline comment skipped for %s:%s — line not in diff", path, line)
                return False
            raise

    async def update_pr_body(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        gh = await self._get_repo_gh(owner, repo)
        existing_pr = await self._get_pr(owner, repo, pr_number)
        existing_body = existing_pr.get("body") or ""

        if existing_body and "## Summary by BugViper" not in existing_body:
            new_body = f"{existing_body}\n\n{body}"
        else:
            new_body = body

        await gh.rest.pulls.async_update(
            owner,
            repo,
            pr_number,
            data={"body": new_body},
        )

    async def create_comment_reaction(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        reaction: str,
    ) -> bool:
        valid_reactions = {"+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"}
        if reaction not in valid_reactions:
            logger.warning("Invalid reaction: %s. Must be one of %s", reaction, valid_reactions)
            return False

        gh = await self._get_repo_gh(owner, repo)
        try:
            await gh.rest.reactions.async_create_for_issue_comment(
                owner,
                repo,
                comment_id,
                data={"content": reaction},
            )
            return True
        except GitHubException as exc:
            logger.warning("Failed to add reaction: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Module-level singleton — reuses caches across all review runs
# ---------------------------------------------------------------------------
_client: Optional[GitHubClient] = None


def get_github_client() -> GitHubClient:
    global _client
    if _client is None:
        _client = GitHubClient()
    return _client


# ---------------------------------------------------------------------------
# OAuth (user token) helpers
# ---------------------------------------------------------------------------


class GitHubOAuthService:
    """GitHub API calls authenticated with a user's OAuth token."""

    def fetch_user_profile(self, token: str) -> dict:
        """Fetch GitHub user profile for the given OAuth access token.

        Returns empty dict on failure (non-critical for login flow).
        """
        try:
            gh = GitHub(
                auth=TokenAuthStrategy(token),
                user_agent="BugViper-User",
                rest_api_validate_body=False,
                auto_retry=True,
                http_cache=True,
            )
            r = gh.rest.users.get_authenticated()
            user = r.parsed_data
            return {
                "login": getattr(user, "login", None),
                "name": getattr(user, "name", None),
                "email": getattr(user, "email", None),
                "avatar_url": getattr(user, "avatar_url", None),
            }
        except GitHubException as exc:
            logger.warning("GitHub get_authenticated failed: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error fetching GitHub profile: %s", exc)
        return {}

    def fetch_user_repos(self, token: str) -> list[dict]:
        """Fetch the authenticated user's repositories.

        Returns a list of repo dicts.
        Raises ValueError on GitHub API errors.
        """
        try:
            gh = GitHub(
                auth=TokenAuthStrategy(token),
                user_agent="BugViper-User",
                rest_api_validate_body=False,
                auto_retry=True,
                http_cache=True,
            )

            paginator = gh.rest.paginate(
                gh.rest.repos.list_for_authenticated_user,
                affiliation="owner",
                sort="updated",
                per_page=100,
            )

            result: list[dict] = []
            for repo in paginator:
                result.append(
                    {
                        "name": getattr(repo, "name", None),
                        "full_name": getattr(repo, "full_name", None),
                        "description": getattr(repo, "description", None),
                        "language": getattr(repo, "language", None),
                        "stargazers_count": getattr(repo, "stargazers_count", 0) or 0,
                        "private": getattr(repo, "private", False) or False,
                        "default_branch": getattr(repo, "default_branch", "main") or "main",
                        "html_url": getattr(repo, "html_url", None),
                    }
                )
            return result
        except GitHubException as exc:
            logger.warning("GitHub list_for_authenticated_user failed: %s", exc)
            raise ValueError(f"GitHub API error: {getattr(exc, 'status_code', 'unknown')}") from exc


github_oauth_service = GitHubOAuthService()
