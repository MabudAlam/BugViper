"""E2B sandbox lifecycle: create, clone repo, expose backend, kill.

The sandbox runs as an isolated Linux VM. We inject a scoped GitHub installation
token as an env var so the agent can git clone inside the sandbox — that gives
it full repo + git history (log, blame, diff) without uploading thousands of
files. The token is single-repo, ~1h, and the agent is ours (not untrusted),
which is the "best result" trade-off documented by LangChain.
"""

from __future__ import annotations

import logging
import shlex

from e2b import Sandbox

logger = logging.getLogger(__name__)


async def create_sandbox_with_repo(
    *,
    owner: str,
    repo: str,
    head_sha: str,
    head_branch: str,
    github_token: str,
    timeout: int,
) -> Sandbox:
    """Create an e2b sandbox with the repo cloned at the PR head.

    The token is passed via env var so the agent can use `git` inside the sandbox.
    Cleanup is the caller's responsibility — pair with `kill_sandbox()` in a
    try/finally or context manager.
    """
    sbx = Sandbox.create(
        envs={
            "GITHUB_TOKEN": github_token,
            "REPO_OWNER": owner,
            "REPO_NAME": repo,
            "REPO_HEAD_SHA": head_sha,
            "REPO_HEAD_BRANCH": head_branch,
        },
        timeout=timeout,
    )

    clone_url = _authenticated_clone_url(github_token, owner, repo)
    repo_dir = "/home/user/workspace/repo"
    diff_dir = "/home/user/review"
    cmds = [
        "set -e",
        f"mkdir -p {shlex.quote(repo_dir)} {shlex.quote(diff_dir)}",
        f"git clone --depth 200 {shlex.quote(clone_url)} {shlex.quote(repo_dir)}",
        f"cd {shlex.quote(repo_dir)} && git fetch --depth 200 origin {shlex.quote(head_sha)}",
        f"cd {shlex.quote(repo_dir)} && git checkout {shlex.quote(head_sha)}",
        f"cd {shlex.quote(repo_dir)} && git log --oneline -5",
    ]
    result = sbx.commands.run(" && ".join(cmds))
    if result.exit_code != 0:
        stderr = (result.stderr or "")[-2000:]
        sbx.kill()
        raise RuntimeError(f"Repo clone failed inside sandbox (exit {result.exit_code}): {stderr}")

    logger.info(
        "Sandbox %s ready: %s/%s @ %s",
        sbx.sandbox_id,
        owner,
        repo,
        head_sha[:7],
    )
    return sbx


def kill_sandbox(sbx: Sandbox | None) -> None:
    """Best-effort kill; swallow errors so the calling pipeline still completes."""
    if sbx is None:
        return
    try:
        sbx.kill()
        logger.info("Killed sandbox %s", sbx.sandbox_id)
    except Exception as exc:
        logger.warning("Failed to kill sandbox %s: %s", getattr(sbx, "sandbox_id", "?"), exc)


def _authenticated_clone_url(token: str, owner: str, repo: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
