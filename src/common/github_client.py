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
from typing import Any, Dict, List, Literal, Optional

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

        # Repo-scoped GitHub clients (installation token auth). We recreate
        # the client when the token rotates.
        self._repo_clients: Dict[str, tuple[str, GitHub]] = {}

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        # githubkit doesn't expose a public close API; it manages httpx clients
        # internally. Keeping the singleton alive is the intended usage.
        return None

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
            stderr = str(result.stderr or "").replace(token, "***")
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
        gh = await self._get_repo_gh(owner, repo)
        r = await gh.rest.pulls.async_get(owner, repo, pr_number)
        pr_obj = r.parsed_data
        dump = getattr(pr_obj, "model_dump", None)
        if callable(dump):
            return dump()
        elif isinstance(pr_obj, dict):
            return pr_obj
        else:
            return dict(pr_obj)

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> Dict[str, str]:
        d = await self._get_pr(owner, repo, pr_number)
        return {"title": d.get("title") or "", "body": d.get("body") or ""}

    async def get_pr_base_sha(self, owner: str, repo: str, pr_number: int) -> str:
        d = await self._get_pr(owner, repo, pr_number)
        return str(d["base"]["sha"])

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

    async def compare_two_shas(
        self,
        owner: str,
        repo: str,
        base_sha: str,
        head_sha: str,
    ) -> str:
        """Return the diff between two commits (base...head)."""
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
        await gh.rest.issues.async_create_comment(owner, repo, pr_number, body=body)

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"] = "COMMENT",
    ) -> None:
        gh = await self._get_repo_gh(owner, repo)
        await gh.rest.pulls.async_create_review(
            owner,
            repo,
            pr_number,
            commit_id=commit_sha,
            body=body,
            event=event,
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
        start_line: int | None = None,
    ) -> dict:
        """Post a single inline PR review comment with a 3-attempt retry.

        Returns {"success": True, "comment_id": int, "thread_id": int}
        or {"success": False, "comment_id": None, "thread_id": None}.


        1. Original ``start_line`` and ``line`` (multi-line if different).
        2. ``start_line = line`` — single line at the end position.
        3. ``line = start_line`` — single line at the start position.
        """
        gh = await self._get_repo_gh(owner, repo)

        attempts: list[dict[str, int]] = []
        if start_line is not None and start_line != line:
            attempts.append({"line": line, "start_line": start_line})
        attempts.append({"line": line, "start_line": line})
        if start_line is not None and start_line != line:
            attempts.append({"line": start_line})

        last_error: Exception | None = None
        for attempt in attempts:
            a_line = attempt["line"]
            for retry in range(3):
                try:
                    if "start_line" in attempt:
                        resp = await gh.rest.pulls.async_create_review_comment(
                            owner, repo, pr_number,
                            body=body, commit_id=commit_sha, path=path,
                            line=a_line, start_line=attempt["start_line"],
                            side="RIGHT",
                        )
                    else:
                        resp = await gh.rest.pulls.async_create_review_comment(
                            owner, repo, pr_number,
                            body=body, commit_id=commit_sha, path=path,
                            line=a_line, side="RIGHT",
                        )
                    comment_id = resp.parsed_data.id if resp.parsed_data else None
                    in_reply_to = (
                        getattr(resp.parsed_data, "in_reply_to_id", None) if resp.parsed_data else None
                    )
                    thread_id = in_reply_to or comment_id
                    return {"success": True, "comment_id": comment_id, "thread_id": thread_id}
                except Exception as exc:
                    if self._is_line_mismatch_error(exc) and attempt is not attempts[-1]:
                        logger.debug(
                            "Line mismatch on %s:%s (attempt line=%s start_line=%s) — retrying",
                            path,
                            commit_sha[:7],
                            attempt["line"],
                            attempt.get("start_line"),
                        )
                        last_error = exc
                        break  # try next attempt variant
                    if self._is_line_mismatch_error(exc):
                        logger.debug(
                            "Inline comment skipped for %s:%s — line not in diff "
                            "(line=%s, start_line=%s)",
                            path,
                            line,
                            attempt["line"],
                            attempt.get("start_line"),
                        )
                        return {"success": False, "comment_id": None, "thread_id": None}
                    # Connection/network errors — retry
                    logger.debug(
                        "GitHub API error on %s:%s (attempt %d): %s",
                        path, commit_sha[:7], retry + 1, exc,
                    )
                    last_error = exc
                    if retry < 2:
                        import asyncio
                        await asyncio.sleep(1 * (retry + 1))
                    else:
                        logger.warning(
                            "Inline comment failed after 3 retries for %s:%s: %s",
                            path, line, exc,
                        )
                        return {"success": False, "comment_id": None, "thread_id": None}

        if last_error is not None:
            logger.debug(
                "Inline comment skipped for %s:%s — all retries exhausted: %s",
                path,
                line,
                last_error,
            )
        return {"success": False, "comment_id": None, "thread_id": None}

    @staticmethod
    def _is_line_mismatch_error(exc: Exception) -> bool:
        """A 422 from GitHub is treated as a line mismatch (caller can retry)."""
        status = getattr(exc, "status_code", None)
        if status is None and hasattr(exc, "response"):
            status = getattr(exc.response, "status_code", None)
        if status is None:
            return False
        return 400 <= int(status) < 500

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
            body=new_body,
        )

    async def create_comment_reaction(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        reaction: Literal["+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"],
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
                content=reaction,  # type: ignore[arg-type]
            )
            return True
        except GitHubException as exc:
            logger.warning("Failed to add reaction: %s", exc)
            return False

    async def resolve_pr_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        comment_id: int,
        thread_id: int | str | None = None,
    ) -> bool:
        """Resolve a PR review comment thread via GitHub GraphQL API.

        The REST API has no resolve endpoint — only GraphQL.
        Mutation: resolveReviewThread(input: {threadId: "PRRT_..."})
        """
        try:
            gh = await self._get_repo_gh(owner, repo)
            node_id = self._coerce_graphql_thread_id(thread_id)
            if not node_id:
                node_id = await self._find_review_thread_node_id(
                    gh, owner, repo, pr_number, comment_id
                )

            if not node_id:
                logger.warning("Could not find thread ID for comment %s", comment_id)
                return False

            mutation = """
            mutation($threadId: ID!) {
              resolveReviewThread(input: {threadId: $threadId}) {
                thread { isResolved }
              }
            }
            """
            result = await self._graphql_query(gh, mutation, {"threadId": node_id}, owner, repo)
            if not result:
                logger.warning("Empty GraphQL response for comment %s", comment_id)
                return False
            if result.get("errors"):
                logger.warning(
                    "GraphQL errors resolving comment %s: %s",
                    comment_id,
                    result["errors"],
                )
                return False
            resolved = (
                result.get("data", {})
                .get("resolveReviewThread", {})
                .get("thread", {})
                .get("isResolved")
            )
            logger.info("Resolve result for comment %s: isResolved=%s", comment_id, resolved)
            return bool(resolved)
        except Exception as exc:
            logger.warning("Failed to resolve comment %s: %s", comment_id, exc)
            return False

    @staticmethod
    def _coerce_graphql_thread_id(thread_id: int | str | None) -> str | None:
        if thread_id is None:
            return None
        raw = str(thread_id).strip()
        if not raw or raw.isdigit():
            return None
        return raw

    async def _find_review_thread_node_id(
        self,
        gh: GitHub,
        owner: str,
        repo: str,
        pr_number: int,
        comment_id: int,
    ) -> str | None:
        query = """
        query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100, after: $cursor) {
                nodes {
                  id
                  isResolved
                  comments(first: 100) {
                    nodes {
                      databaseId
                      fullDatabaseId
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """
        cursor: str | None = None
        while True:
            response = await self._graphql_query(
                gh,
                query,
                {"owner": owner, "repo": repo, "pr": pr_number, "cursor": cursor},
                owner,
                repo,
            )
            if response.get("errors"):
                logger.warning(
                    "GraphQL errors fetching review threads for %s/%s#%s: %s",
                    owner,
                    repo,
                    pr_number,
                    response["errors"],
                )
                return None

            pr_data = response.get("data", {}).get("repository", {}).get("pullRequest")
            if not pr_data:
                logger.warning("PR %s not found via GraphQL", pr_number)
                return None

            review_threads = pr_data.get("reviewThreads", {})
            for thread in review_threads.get("nodes", []):
                for comment in thread.get("comments", {}).get("nodes", []):
                    database_ids = {
                        comment.get("databaseId"),
                        comment.get("fullDatabaseId"),
                    }
                    if comment_id in database_ids or str(comment_id) in {
                        str(value) for value in database_ids if value is not None
                    }:
                        node_id = thread.get("id")
                        logger.info(
                            "Found thread for comment %s: node_id=%s, pr=%s",
                            comment_id,
                            node_id,
                            pr_number,
                        )
                        return node_id

            page_info = review_threads.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")

    async def _graphql_query(
        self,
        gh: GitHub,
        query: str,
        variables: dict[str, Any],
        owner: str,
        repo: str,
    ) -> dict:
        """Execute a GraphQL query/mutation using httpx with installation token auth."""
        import json as _json

        import httpx

        token = await self._get_installation_token(owner, repo)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.github.com/graphql",
                headers=headers,
                json={"query": query, "variables": variables},
                timeout=30.0,
            )
        if r.status_code >= 400:
            logger.warning("GraphQL status=%s body=%s", r.status_code, r.text[:500])
            r.raise_for_status()
        logger.debug(
            "GraphQL status=%s body=%s",
            r.status_code,
            (r.text[:300] if r.text else ""),
        )
        return _json.loads(r.text) if r.text else {}


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
