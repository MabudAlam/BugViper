"""E2B sandbox lifecycle: create, clone repo, expose backend, kill."""

from __future__ import annotations

import json
import logging
import shlex

from e2b import Sandbox
from e2b.sandbox.commands.command_handle import CommandExitException

logger = logging.getLogger(__name__)

_UV_INSTALL_SCRIPT = "https://astral.sh/uv/install.sh"


def _run(sbx: Sandbox, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        result = sbx.commands.run(cmd, timeout=timeout)
        return (result.exit_code, result.stdout or "", result.stderr or "")
    except CommandExitException as exc:
        return (exc.exit_code, exc.stdout or "", exc.stderr or "")
    except Exception as exc:
        logger.error("sandbox command failed: %s — cmd=%s", exc, cmd[:200])
        return (-1, "", str(exc))


async def create_sandbox_with_repo(
    *,
    owner: str,
    repo: str,
    head_sha: str,
    head_branch: str,
    github_token: str,
    timeout: int,
    template: str | None = None,
) -> Sandbox:
    """Create an e2b sandbox with the repo cloned at the PR head."""
    sbx = Sandbox.create(
        template=template,
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
    cmds = [
        "set -e",
        f"mkdir -p {shlex.quote(repo_dir)}",
        f"git clone --depth 200 {shlex.quote(clone_url)} {shlex.quote(repo_dir)}",
        f"cd {shlex.quote(repo_dir)} && git fetch --depth 200 origin {shlex.quote(head_sha)}",
        f"cd {shlex.quote(repo_dir)} && git checkout {shlex.quote(head_sha)}",
        f"cd {shlex.quote(repo_dir)} && git log --oneline -5",
    ]
    rc, out, err = _run(sbx, " && ".join(cmds), timeout=300)
    if rc != 0:
        stderr = err[-2000:]
        logger.error("Clone failed (exit %d): %s", rc, err[-500:])
        sbx.kill()
        raise RuntimeError(f"Repo clone failed inside sandbox (exit {rc}): {stderr}")

    logger.info("Clone OK, installing uv...")
    _ensure_uv(sbx)

    logger.info(
        "Sandbox %s ready: %s/%s @ %s",
        sbx.sandbox_id,
        owner,
        repo,
        head_sha[:7],
    )
    return sbx


def _ensure_uv(sbx: Sandbox) -> None:
    """Ensure uv is available in the sandbox for running Python tools."""
    rc, out, _ = _run(sbx, "which uv || echo MISSING")
    if rc == 0 and "MISSING" not in (out or ""):
        logger.info("uv already available in sandbox")
        return

    logger.info("Installing uv in sandbox...")
    install_cmd = f"curl -LsSf {_UV_INSTALL_SCRIPT} | sh && export PATH=$HOME/.local/bin:$PATH"
    rc, _, err = _run(sbx, install_cmd, timeout=60)
    if rc != 0:
        logger.warning("uv installation failed: %s", err or "")
        return

    rc, out, _ = _run(sbx, "export PATH=$HOME/.local/bin:$PATH && uv --version")
    if rc == 0:
        logger.info("uv installed: %s", out or "")


def kill_sandbox(sbx: Sandbox | None) -> None:
    """Best-effort kill; swallow errors so the calling pipeline still completes."""
    if sbx is None:
        return
    try:
        sbx.kill()
        logger.info("Killed sandbox %s", sbx.sandbox_id)
    except Exception as exc:
        logger.warning("Failed to kill sandbox %s: %s", getattr(sbx, "sandbox_id", "?"), exc)


def inject_diff(sbx: Sandbox, diff_text: str) -> None:
    """Write the unified diff to the sandbox for subagents to read."""
    review_dir = "/home/user/review"
    _run(sbx, f"mkdir -p {review_dir}")
    sbx.files.write(f"{review_dir}/diff.patch", diff_text)
    logger.info("Injected diff.patch into sandbox (%d chars)", len(diff_text))


def inject_call_graph(sbx: Sandbox, call_graph_json: str, callgraph_md: str) -> None:
    """Write call graph files to the sandbox for subagents to read."""
    review_dir = "/home/user/review"
    _run(sbx, f"mkdir -p {review_dir}")
    sbx.files.write(f"{review_dir}/call_graph.json", call_graph_json)
    sbx.files.write(f"{review_dir}/blast_radius.md", callgraph_md)
    logger.info(
        "Injected call_graph.json (%d chars) and blast_radius.md (%d chars) into sandbox",
        len(call_graph_json),
        len(callgraph_md),
    )


def _authenticated_clone_url(token: str, owner: str, repo: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
