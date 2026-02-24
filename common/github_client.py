
import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

from github import Github, GithubIntegration, Auth, GithubException


class GitHubAuthError(Exception):
    """Base exception for GitHub authentication errors."""


class GitHubAppAccessError(GitHubAuthError):
    """GitHub App does not have access to the repository."""


class GitCloneError(GitHubAuthError):
    """Git command failed."""


class GitHubClient:
    """
    GitHub API client using GitHub App authentication via PyGithub.

    Provides async interface wrapping PyGithub's synchronous methods.
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        private_key_path: Optional[str] = None
    ):
        """
        Initialize GitHub client.

        Args:
            app_id: GitHub App ID (defaults to GITHUB_APP_ID env var)
            private_key_path: Path to .pem file (defaults to GITHUB_PRIVATE_KEY_PATH env var)
        """
        # Safely resolve app_id
        raw_app_id = app_id if app_id is not None else os.getenv("GITHUB_APP_ID")
        if not raw_app_id:
            raise ValueError("GitHub App ID not provided")

        try:
            self.app_id = int(raw_app_id)
        except ValueError:
            raise ValueError(f"GitHub App ID must be an integer, got: {raw_app_id}")

        # Safely resolve private_key_path
        self.private_key_path = private_key_path or os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not self.private_key_path:
            raise ValueError("GitHub private key path not provided")

        # Load private key
        with open(self.private_key_path, "r") as f:
            self.private_key = f.read()

        # Create GitHub App integration (PyGithub 2.x API)
        app_auth = Auth.AppAuth(self.app_id, self.private_key)
        self.integration = GithubIntegration(auth=app_auth)
        self._github_cache: Dict[str, Dict[str, Any]] = {}

    def _get_github_instance(self, owner: str, repo: str) -> Github:
        """
        Get authenticated Github instance for a specific repository.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Authenticated Github instance
        """
        cache_key = f"{owner}/{repo}"

        if cache_key in self._github_cache:
            cache_entry = self._github_cache[cache_key]
            token_expires_at = cache_entry.get('expires_at')

            # Check if token is still valid (with some buffer time)
            if token_expires_at:
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                buffer = datetime.timedelta(minutes=5)  # 5-minute buffer
                if now < (token_expires_at - buffer):
                    return cache_entry['github']

        # Get installation ID for this repository
        installation = self.integration.get_repo_installation(owner, repo)

        # Get installation access token
        auth = self.integration.get_access_token(installation.id)

        # Create authenticated Github instance
        github = Github(auth=Auth.Token(auth.token))

        # Cache with token expiry information
        self._github_cache[cache_key] = {
            'github': github,
            'expires_at': auth.expires_at
        }

        return github

    async def clone_repository(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
        clone_dir: Optional[Path] = None
    ) -> Path:
        """
        Clone a repository using GitHub App authentication.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch to clone (optional)
            clone_dir: Directory to clone into (optional, defaults to temp)

        Returns:
            Path to the cloned repository
        """

        # ----------------------------
        # 1. Verify GitHub App access
        # ----------------------------
        try:
            github = self._get_github_instance(owner, repo)
            github.get_repo(f"{owner}/{repo}")
        except GithubException as e:
            if e.status == 404:
                raise GitHubAppAccessError(
                    f"GitHub App is not installed or lacks access to "
                    f"{owner}/{repo}. Ensure the app is installed on this repository "
                    f"and has 'Contents: Read' permission."
                ) from e
            raise GitHubAuthError(
                f"GitHub API error while accessing {owner}/{repo}: {e.data}"
            ) from e

        # ----------------------------
        # 2. Get installation token
        # ----------------------------
        try:
            installation = self.integration.get_repo_installation(
                owner, repo
            )
            token = self.integration.get_access_token(
                installation.id
            ).token
        except GithubException as e:
            raise GitHubAuthError(
                f"Failed to obtain GitHub App installation token for "
                f"{owner}/{repo}: {e.data}"
            ) from e

        # ----------------------------
        # 3. Prepare clone directory
        # ----------------------------
        if clone_dir is None:
            clone_dir = Path(tempfile.gettempdir()) / owner / repo
        else:
            clone_dir = Path(clone_dir) / owner / repo

        if clone_dir.exists():
            import shutil
            shutil.rmtree(clone_dir)

        clone_dir.parent.mkdir(parents=True, exist_ok=True)

        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        # ----------------------------
        # 4. Run git clone
        # ----------------------------
        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([clone_url, str(clone_dir)])

        print(f"Cloning {owner}/{repo}...")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

        if result.returncode != 0:
            stderr = result.stderr.replace(token, "***REDACTED***")

            if "Repository not found" in stderr:
                raise GitHubAppAccessError(
                    f"Repository '{owner}/{repo}' not found via git clone. "
                    f"This almost always means the GitHub App does not have access."
                )

            raise GitCloneError(
                f"Git clone failed for {owner}/{repo}:\n{stderr}"
            )

        print(f"✓ Cloned to: {clone_dir}")
        return clone_dir

    async def check_repository_access(self, owner: str, repo: str) -> bool:
        """
        Check if the GitHub App has access to a repository.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            True if access is available, False otherwise
        """
        try:
            github = self._get_github_instance(owner, repo)
            github.get_repo(f"{owner}/{repo}")
            return True
        except GithubException:
            return False
        except Exception:
            return False

    async def get_repository_info(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get basic repository information.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Dict with repository metadata
        """
        def _get_repo_info():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")

            return {
                "name": repository.name,
                "full_name": repository.full_name,
                "description": repository.description,
                "private": repository.private,
                "default_branch": repository.default_branch,
                "language": repository.language,
                "size": repository.size,
                "stars": repository.stargazers_count,
                "forks": repository.forks_count,
                "topics": repository.get_topics(),
                "created_at": repository.created_at.isoformat() if repository.created_at else None,
                "updated_at": repository.updated_at.isoformat() if repository.updated_at else None,
            }

        return await asyncio.to_thread(_get_repo_info)

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> Dict[str, str]:
        """
        Get title and body of a pull request.

        Returns:
            Dict with keys 'title' and 'body' (body may be empty string).
        """
        def _get_info():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            pr = repository.get_pull(pr_number)
            return {
                "title": pr.title or "",
                "body": pr.body or "",
            }

        return await asyncio.to_thread(_get_info)

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """
        Get the unified diff for a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number

        Returns:
            Unified diff string
        """
        def _get_diff():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            pr = repository.get_pull(pr_number)
            # PyGithub doesn't expose raw diff directly; fetch via files
            files = pr.get_files()
            parts = []
            for f in files:
                if f.patch:
                    parts.append(f"diff --git a/{f.filename} b/{f.filename}")
                    parts.append(f"--- a/{f.filename}")
                    parts.append(f"+++ b/{f.filename}")
                    parts.append(f.patch)
            return "\n".join(parts)

        return await asyncio.to_thread(_get_diff)

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
        """
        Get list of changed files in a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number

        Returns:
            List of dicts with filename, status, patch info
        """
        def _get_files():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            pr = repository.get_pull(pr_number)
            return [
                {
                    "filename": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "changes": f.changes,
                    "patch": f.patch,
                }
                for f in pr.get_files()
            ]

        return await asyncio.to_thread(_get_files)

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str = None) -> Optional[str]:
        """
        Get the content of a file from the repository.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path relative to repo root
            ref: Branch, tag, or commit SHA (defaults to repo default branch)

        Returns:
            File content as string, or None if not found
        """
        def _get_content():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            try:
                content_file = repository.get_contents(path, ref=ref)
                return content_file.decoded_content.decode("utf-8")
            except GithubException:
                return None

        return await asyncio.to_thread(_get_content)

    async def get_pr_head_ref(self, owner: str, repo: str, pr_number: int) -> str:
        """Get the head SHA of a pull request."""
        def _get_ref():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            pr = repository.get_pull(pr_number)
            return pr.head.sha

        return await asyncio.to_thread(_get_ref)

    async def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        """
        Post a comment on a pull request (via issue comments API).

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            body: Comment body (markdown)
        """
        def _post():
            github = self._get_github_instance(owner, repo)
            repository = github.get_repo(f"{owner}/{repo}")
            issue = repository.get_issue(pr_number)
            issue.create_comment(body)

        await asyncio.to_thread(_post)
