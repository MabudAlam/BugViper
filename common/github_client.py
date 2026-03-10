"""GitHub App client — fully async via httpx.

Why httpx instead of PyGithub + asyncio.to_thread
--------------------------------------------------
1. Async-native: no thread-pool overhead, no asyncio.to_thread wrappers.
2. Single persistent AsyncClient with connection pooling — one TLS handshake
   per host, shared across all calls in a review run.
3. Custom SSL context with ssl.OP_IGNORE_UNEXPECTED_EOF: GitHub's load
   balancers sometimes drop the TCP connection without sending a TLS
   close_notify. Python 3.13 made this a hard error; this flag restores
   the Python 3.12 behaviour without weakening certificate validation.
4. JWT generation is done natively with PyJWT — no PyGithub Requester
   involved, so no requests/urllib3 SSL quirks.

Known failure modes and mitigations
------------------------------------
- Token cache race: asyncio.Lock ensures only one coroutine fetches a new
  token even when many calls run concurrently (e.g. asyncio.gather).
- Connection leak: use as an async context manager or call aclose(). A
  module-level singleton (get_github_client()) is the recommended pattern.
- Large PRs: get_pr_diff/get_pr_files paginate automatically up to 3000 files.
- Redundant PR fetches: _get_pr() caches the PR payload so get_pr_info and
  get_pr_head_ref share one network call per PR per client lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import jwt  # PyJWT

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
_MAX_PR_FILES = 3000  # GitHub hard cap


def _build_ssl_context() -> ssl.SSLContext:
    """Return a strict-but-compatible SSL context for api.github.com.

    ssl.OP_IGNORE_UNEXPECTED_EOF (Python 3.10+) tells OpenSSL to accept a
    TCP-level connection close as a valid end-of-stream even when no TLS
    close_notify was sent. GitHub's CDN does this under load. The flag does
    NOT disable certificate verification.
    """
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
    return ctx


class GitHubAuthError(Exception):
    """Base exception for GitHub authentication errors."""


class GitHubAppAccessError(GitHubAuthError):
    """GitHub App does not have access to the repository."""


class GitCloneError(GitHubAuthError):
    """Git command failed."""


class GitHubClient:
    """Async GitHub App client backed by a persistent httpx.AsyncClient.

    Use as an async context manager or call aclose() when done:

        async with GitHubClient() as gh:
            diff = await gh.get_pr_diff(owner, repo, pr_number)

    Or use the module-level singleton via get_github_client() so the
    connection pool is reused across multiple review runs on the same
    Cloud Run instance.
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
        except ValueError:
            raise ValueError(f"GITHUB_APP_ID must be an integer, got: {raw_id!r}")

        key_path = private_key_path or os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not key_path:
            raise ValueError("GitHub private key path not provided")
        with open(key_path) as fh:
            self._private_key = fh.read()

        # Installation token cache: repo_full_name → (token, monotonic_expiry)
        self._token_cache: Dict[str, tuple[str, float]] = {}
        # Per-repo lock prevents concurrent coroutines from each fetching a
        # fresh token when the cache is cold (e.g. asyncio.gather calls).
        self._token_locks: Dict[str, asyncio.Lock] = {}

        # PR payload cache: (owner, repo, pr_number) → payload dict
        # Avoids duplicate network calls when get_pr_info and get_pr_head_ref
        # are called for the same PR in the same client lifetime.
        self._pr_cache: Dict[tuple[str, str, int], dict] = {}

        self._http = httpx.AsyncClient(
            base_url=_GITHUB_API,
            headers={
                "Accept": _ACCEPT,
                "X-GitHub-Api-Version": _API_VERSION,
                "User-Agent": "BugViper-App",
            },
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0),
            verify=_build_ssl_context(),
        )

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _generate_app_jwt(self) -> str:
        """Create a short-lived GitHub App JWT (valid 9 minutes)."""
        now = int(time.time())
        payload = {
            "iat": now - 60,   # 60 s in the past to tolerate clock skew
            "exp": now + 540,  # 9 minutes (GitHub max is 10)
            "iss": str(self._app_id),
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _get_token(self, owner: str, repo: str) -> str:
        """Return a valid installation token for owner/repo.

        Tokens are cached until 5 minutes before their expiry. An asyncio.Lock
        per repo ensures only one coroutine fetches a new token at a time even
        when multiple coroutines call this concurrently (e.g. asyncio.gather).
        """
        cache_key = f"{owner}/{repo}"

        # Fast path — no lock needed for a cache hit
        if cache_key in self._token_cache:
            token, expires_mono = self._token_cache[cache_key]
            if time.monotonic() < expires_mono:
                return token

        # Slow path — acquire a per-repo lock so concurrent callers wait
        # instead of all fetching a fresh token simultaneously
        if cache_key not in self._token_locks:
            self._token_locks[cache_key] = asyncio.Lock()
        async with self._token_locks[cache_key]:
            # Re-check after acquiring the lock — another coroutine may have
            # already populated the cache while we were waiting
            if cache_key in self._token_cache:
                token, expires_mono = self._token_cache[cache_key]
                if time.monotonic() < expires_mono:
                    return token

            app_jwt = self._generate_app_jwt()
            auth_headers = {"Authorization": f"Bearer {app_jwt}"}

            # Step 1: resolve installation ID for this repo
            r = await self._http.get(
                f"/repos/{owner}/{repo}/installation",
                headers=auth_headers,
            )
            if r.status_code == 404:
                raise GitHubAppAccessError(
                    f"GitHub App is not installed on {owner}/{repo}. "
                    "Install the app and ensure it has the required permissions."
                )
            r.raise_for_status()
            installation_id = r.json()["id"]

            # Step 2: exchange for an installation access token
            r = await self._http.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers=auth_headers,
            )
            r.raise_for_status()
            data = r.json()

            token = data["token"]
            exp_dt = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            ttl = (exp_dt - datetime.now(timezone.utc)).total_seconds()
            self._token_cache[cache_key] = (token, time.monotonic() + ttl - 300)

            logger.debug("Obtained installation token for %s/%s (ttl=%.0fs)", owner, repo, ttl)
            return token

    def _auth_headers(self, token: str) -> Dict[str, str]:
        return {"Authorization": f"token {token}"}

    # ------------------------------------------------------------------
    # Paginated fetch helper
    # ------------------------------------------------------------------

    async def _get_paginated(
        self,
        token: str,
        path: str,
        max_items: int = _MAX_PR_FILES,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[dict]:
        """Fetch all pages of a list endpoint up to max_items."""
        results: List[dict] = []
        page = 1
        while len(results) < max_items:
            r = await self._http.get(
                path,
                headers=self._auth_headers(token),
                params={**(params or {}), "per_page": 100, "page": page},
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break  # last page
            page += 1
        return results[:max_items]

    # ------------------------------------------------------------------
    # Repository helpers
    # ------------------------------------------------------------------

    async def check_repository_access(self, owner: str, repo: str) -> bool:
        try:
            token = await self._get_token(owner, repo)
            r = await self._http.get(
                f"/repos/{owner}/{repo}",
                headers=self._auth_headers(token),
            )
            return r.status_code == 200
        except Exception:
            return False

    async def get_repository_info(self, owner: str, repo: str) -> Dict[str, Any]:
        token = await self._get_token(owner, repo)
        r = await self._http.get(
            f"/repos/{owner}/{repo}",
            headers=self._auth_headers(token),
        )
        r.raise_for_status()
        d = r.json()
        return {
            "name": d["name"],
            "full_name": d["full_name"],
            "description": d.get("description"),
            "private": d["private"],
            "default_branch": d["default_branch"],
            "language": d.get("language"),
            "size": d.get("size"),
            "stars": d.get("stargazers_count"),
            "forks": d.get("forks_count"),
            "topics": d.get("topics", []),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }

    async def clone_repository(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
        clone_dir: Optional[Path] = None,
    ) -> Path:
        """Clone a repository using a short-lived installation token."""
        token = await self._get_token(owner, repo)

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
    # PR data — shared payload fetch to avoid duplicate calls
    # ------------------------------------------------------------------

    async def _get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        """Fetch and cache the PR payload. Shared by info + head_ref lookups."""
        cache_key = (owner, repo, pr_number)
        if cache_key not in self._pr_cache:
            token = await self._get_token(owner, repo)
            r = await self._http.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self._auth_headers(token),
            )
            r.raise_for_status()
            self._pr_cache[cache_key] = r.json()
        return self._pr_cache[cache_key]

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> Dict[str, str]:
        d = await self._get_pr(owner, repo, pr_number)
        return {"title": d.get("title") or "", "body": d.get("body") or ""}

    async def get_pr_head_ref(self, owner: str, repo: str, pr_number: int) -> str:
        d = await self._get_pr(owner, repo, pr_number)
        return d["head"]["sha"]

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Build a unified diff string, paginating through all changed files."""
        token = await self._get_token(owner, repo)
        files = await self._get_paginated(
            token, f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        )
        parts: List[str] = []
        for f in files:
            if f.get("patch"):
                parts.append(f"diff --git a/{f['filename']} b/{f['filename']}")
                parts.append(f"--- a/{f['filename']}")
                parts.append(f"+++ b/{f['filename']}")
                parts.append(f["patch"])
        return "\n".join(parts)

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
        token = await self._get_token(owner, repo)
        files = await self._get_paginated(
            token, f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        )
        return [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "changes": f.get("changes", 0),
                "patch": f.get("patch"),
            }
            for f in files
        ]

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        token = await self._get_token(owner, repo)
        params = {"ref": ref} if ref else {}
        r = await self._http.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            headers={**self._auth_headers(token), "Accept": "application/vnd.github.raw+json"},
            params=params,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        token = await self._get_token(owner, repo)
        r = await self._http.post(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._auth_headers(token),
            json={"body": body},
        )
        r.raise_for_status()

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
    ) -> None:
        token = await self._get_token(owner, repo)
        r = await self._http.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers=self._auth_headers(token),
            json={"commit_id": commit_sha, "body": body, "event": event},
        )
        r.raise_for_status()

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
        """Post an inline review comment. Returns False if the line is not in the diff."""
        token = await self._get_token(owner, repo)
        r = await self._http.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            headers=self._auth_headers(token),
            json={
                "body": body,
                "commit_id": commit_sha,
                "path": path,
                "line": line,
                "side": "RIGHT",
            },
        )
        if r.status_code == 422:
            logger.debug("Inline comment skipped for %s:%s — line not in diff", path, line)
            return False
        r.raise_for_status()
        return True

    async def create_issue(self, owner: str, repo: str, title: str, body: str) -> int:
        """Create a new issue. Returns the issue number."""
        token = await self._get_token(owner, repo)
        r = await self._http.post(
            f"/repos/{owner}/{repo}/issues",
            headers=self._auth_headers(token),
            json={"title": title, "body": body},
        )
        r.raise_for_status()
        return r.json()["number"]


# ---------------------------------------------------------------------------
# Module-level singleton — reuses connection pool across all review runs
# on the same Cloud Run instance. Constructed lazily on first use.
# ---------------------------------------------------------------------------
_client: Optional[GitHubClient] = None


def get_github_client() -> GitHubClient:
    """Return the shared GitHubClient singleton for this process."""
    global _client
    if _client is None:
        _client = GitHubClient()
    return _client
