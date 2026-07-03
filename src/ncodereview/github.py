"""GitHub operations for code review pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.diff_parser import split_diff_by_file
from common.github_client import GitHubClient, get_github_client

logger = logging.getLogger(__name__)


class ReviewGitHub:
    """GitHub operations for a single review run."""

    def __init__(self, gh: GitHubClient | None = None) -> None:
        self._gh = gh

    @property
    def gh(self) -> GitHubClient:
        if self._gh is None:
            self._gh = get_github_client()
        return self._gh

    async def fetch_pr_data(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> tuple[str, dict, str, str, str, dict[str, str]]:
        diff_text, pr_info, head_sha, base_sha = await asyncio.gather(
            self.gh.get_pr_diff(owner, repo, pr_number),
            self.gh.get_pr_info(owner, repo, pr_number),
            self.gh.get_pr_head_ref(owner, repo, pr_number),
            self.gh.get_pr_base_sha(owner, repo, pr_number),
        )
        head_branch = await self._get_head_branch(owner, repo, pr_number)
        changed = list(split_diff_by_file(diff_text).keys())
        pr_files_raw = await asyncio.gather(
            *[self.gh.get_file_content(owner, repo, f, ref=head_sha) for f in changed],
            return_exceptions=True,
        )
        pr_files = {
            fp: content
            for fp, content in zip(changed, pr_files_raw)
            if not isinstance(content, Exception) and content is not None
        }
        return diff_text, pr_info, head_sha, base_sha, head_branch, pr_files

    async def _get_head_branch(self, owner: str, repo: str, pr_number: int) -> str:
        try:
            pr = await self.gh._get_pr(owner, repo, pr_number)
            return pr.get("head", {}).get("ref", "") or "main"
        except Exception:
            return "main"

    async def get_incremental_diff(
        self,
        owner: str,
        repo: str,
        base_sha: str,
        head_sha: str,
    ) -> str | None:
        try:
            return await self.gh.compare_two_shas(owner, repo, base_sha, head_sha)
        except Exception:
            return None

    async def get_repository_info(self, owner: str, repo: str) -> dict[str, Any]:
        return await self.gh.get_repository_info(owner, repo)

    async def get_installation_token(self, owner: str, repo: str) -> str:
        return await self.gh._get_installation_token(owner, repo)

    async def post_failure_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        reason: str,
    ) -> None:
        try:
            await self.gh.post_comment(
                owner, repo, pr_number,
                f"BugViper Review Failed\n\n`{reason}`",
            )
        except Exception as exc:
            logger.warning("Could not post failure comment: %s", exc)
