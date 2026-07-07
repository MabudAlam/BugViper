"""E2B sandbox lifecycle: create, clone repo, expose backend, kill."""

from __future__ import annotations

import json
import logging
import shlex

from e2b import Sandbox

logger = logging.getLogger(__name__)

_UV_INSTALL_SCRIPT = "https://astral.sh/uv/install.sh"


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
    result = sbx.commands.run(" && ".join(cmds))
    if result.exit_code != 0:
        stderr = (result.stderr or "")[-2000:]
        sbx.kill()
        raise RuntimeError(f"Repo clone failed inside sandbox (exit {result.exit_code}): {stderr}")

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
    check = sbx.commands.run("which uv || echo MISSING")
    if check.exit_code == 0 and "MISSING" not in (check.stdout or ""):
        logger.info("uv already available in sandbox")
        return

    logger.info("Installing uv in sandbox...")
    install_cmd = f"curl -LsSf {_UV_INSTALL_SCRIPT} | sh && export PATH=$HOME/.local/bin:$PATH"
    result = sbx.commands.run(install_cmd, timeout=60)
    if result.exit_code != 0:
        logger.warning("uv installation failed: %s", result.stderr or "")
        return

    verify = sbx.commands.run("export PATH=$HOME/.local/bin:$PATH && uv --version")
    if verify.exit_code == 0:
        logger.info("uv installed: %s", verify.stdout or "")


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
    sbx.commands.run(f"mkdir -p {review_dir}")
    sbx.files.write(f"{review_dir}/diff.patch", diff_text)
    logger.info("Injected diff.patch into sandbox (%d chars)", len(diff_text))


def inject_call_graph(sbx: Sandbox, call_graph_json: str, callgraph_md: str) -> None:
    """Write call graph files to the sandbox for subagents to read."""
    review_dir = "/home/user/review"
    sbx.commands.run(f"mkdir -p {review_dir}")
    sbx.files.write(f"{review_dir}/call_graph.json", call_graph_json)
    sbx.files.write(f"{review_dir}/call_graph.md", callgraph_md)
    logger.info(
        "Injected call_graph.json (%d chars) and call_graph.md (%d chars) into sandbox",
        len(call_graph_json),
        len(callgraph_md),
    )


def inject_files(sbx: Sandbox, file_list: list[str], blast_radius_md: str) -> None:
    """Write batch-specific files to the sandbox.

    Args:
        sbx: E2B sandbox instance
        file_list: List of file paths in this batch
        blast_radius_md: Filtered blast radius markdown for this batch
    """
    review_dir = "/home/user/review"
    sbx.commands.run(f"mkdir -p {review_dir}")

    file_list_json = json.dumps(file_list)
    sbx.files.write(f"{review_dir}/batch_files.json", file_list_json)
    sbx.files.write(f"{review_dir}/blast_radius.md", blast_radius_md)

    logger.info(
        "Injected batch: %d files, blast_radius.md (%d chars)",
        len(file_list),
        len(blast_radius_md),
    )


def _authenticated_clone_url(token: str, owner: str, repo: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
