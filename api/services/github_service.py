import logging

from github import Auth, Github, GithubException

logger = logging.getLogger(__name__)


class GitHubService:
    """Handles GitHub API calls authenticated with a user's OAuth access token."""

    def fetch_user_profile(self, token: str) -> dict:
        """
        Fetch GitHub user profile for the given OAuth access token.
        Returns empty dict on failure (non-critical for login flow).
        """
        try:
            g = Github(auth=Auth.Token(token))
            user = g.get_user()
            return {
                "login": user.login,
                "name": user.name,
                "email": user.email,
                "avatar_url": user.avatar_url,
            }
        except GithubException as exc:
            logger.warning("GitHub get_user failed: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error fetching GitHub profile: %s", exc)
        return {}

    def fetch_user_repos(self, token: str) -> list[dict]:
        """
        Fetch the authenticated user's GitHub repositories (owner affiliation,
        sorted by last updated).

        Returns a list of repo dicts.
        Raises ValueError on GitHub API errors.
        """
        try:
            g = Github(auth=Auth.Token(token))
            repos = g.get_user().get_repos(
                affiliation="owner",
                sort="updated",
            )

            result: list[dict] = []
            for repo in repos:
                result.append(
                    {
                        "name": repo.name,
                        "full_name": repo.full_name,
                        "description": repo.description,
                        "language": repo.language,
                        "stargazers_count": repo.stargazers_count,
                        "private": repo.private,
                        "default_branch": repo.default_branch,
                        "html_url": repo.html_url,
                    }
                )

            return result
        except GithubException as exc:
            logger.warning("GitHub get_repos failed: %s", exc)
            raise ValueError(f"GitHub API error: {exc.status}") from exc


github_service = GitHubService()
