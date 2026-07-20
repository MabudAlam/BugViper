from __future__ import annotations

import logging
import os

from e2b import Sandbox

from static_code_review._shared import run_in_sandbox
from static_code_review.eslint_tool import run_eslint
from static_code_review.golangci_tool import run_golangci_lint
from static_code_review.ruff_tool import run_ruff

logger = logging.getLogger(__name__)


def _resolve_config_file(sbx: Sandbox, tool, repo_dir: str) -> str | None:
    if tool.config_file:
        cfg = f"{repo_dir}/{tool.config_file}"
        rc, _, _ = run_in_sandbox(sbx, f"test -f {cfg} && echo EXISTS")
        if rc == 0:
            return cfg
        logger.info("Configured config file %s not found, falling back to auto-detect", tool.config_file)

    for f in tool.config_files:
        cfg = f"{repo_dir}/{f}"
        rc, _, _ = run_in_sandbox(sbx, f"test -f {cfg} && echo EXISTS")
        if rc == 0:
            return cfg
    return None


RUNNER_REGISTRY: dict[str, callable] = {
    "ruff": run_ruff,
    "golangci_lint": run_golangci_lint,
    "eslint": run_eslint,
}


async def run_linters_in_sandbox(
    uid: str,
    owner: str,
    repo: str,
    sbx: Sandbox,
    changed_files: list[str],
    debug_dir: str | None = None,
) -> list[dict]:
    logger.info("run_linters_in_sandbox: uid=%s, %d changed files", uid, len(changed_files))
    for f in changed_files:
        logger.info("  changed: %s", f)

    from common.firebase_service import firebase_service

    tools_config = firebase_service.get_tools_config(uid)

    repo_dir = "/home/user/workspace/repo"
    all_findings: list[dict] = []
    any_enabled = False

    for tool_key, runner_fn in RUNNER_REGISTRY.items():
        tool = getattr(tools_config, tool_key, None)
        if tool and tool.enabled:
            any_enabled = True
            try:
                cfg = _resolve_config_file(sbx, tool, repo_dir)
                findings = runner_fn(sbx, repo_dir, cfg, changed_files, debug_dir=debug_dir)
                logger.info("%s: %d findings", tool_key, len(findings))
                all_findings.extend(findings)
            except Exception as exc:
                logger.warning("%s failed in sandbox: %s", tool_key, exc)

    if not any_enabled:
        logger.info("No linter tools enabled for uid=%s", uid)

    return all_findings


async def run_lint_only(
    owner: str,
    repo: str,
    pr_number: int,
    uid: str,
) -> None:
    from datetime import datetime, timezone

    from common.firebase_service import firebase_service
    from common.github_client import get_github_client
    from ai_code_review.config import config, ensure_env
    from ai_code_review.diff import get_changed_files
    from ai_code_review.sandbox import create_sandbox_with_repo, kill_sandbox

    ensure_env()
    os.environ["E2B_API_KEY"] = config.E2B_API_KEY

    # Check if any linter tools are enabled before creating sandbox
    tools_config = firebase_service.get_tools_config(uid)
    has_enabled = any(
        getattr(tools_config, k, None) and getattr(tools_config, k).enabled
        for k in ("ruff", "eslint", "golangci_lint")
    )
    if not has_enabled:
        logger.warning("No linter tools enabled for uid=%s — skipping lint", uid)
        gh = get_github_client()
        body = (
            "## 🔬 BugViper Static Analysis\n\n"
            "No linter tools are enabled. Configure them in the **Dashboard → Tools**."
        )
        await gh.post_comment(owner, repo, pr_number, body)
        return

    github = get_github_client()
    pr_data = await github.fetch_pr_data(owner, repo, pr_number)
    if not pr_data or not pr_data.difftext:
        logger.warning("Empty diff for %s/%s#%s — skipping lint", owner, repo, pr_number)
        return

    github_token = await github.get_installation_token(owner, repo)
    sbx = None
    try:
        logger.info("Creating sandbox for %s/%s#%s", owner, repo, pr_number)
        sbx = await create_sandbox_with_repo(
            owner=owner,
            repo=repo,
            head_sha=pr_data.head_sha,
            head_branch=pr_data.head_branch,
            github_token=github_token,
            timeout=config.DEEPAGENT_SANDBOX_TIMEOUT,
            template="bugviper-linter",
        )

        changed_files = get_changed_files(pr_data.difftext)
        logger.info("Sandbox ready, running linters on %d changed files", len(changed_files))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_dir = f"logs/lint/{owner}_{repo}_{pr_number}_{timestamp}"
        os.makedirs(debug_dir, exist_ok=True)
        findings = await run_linters_in_sandbox(uid, owner, repo, sbx, changed_files, debug_dir=debug_dir)
        if not findings:
            body = "## 🔬 BugViper Static Analysis\n\nNo issues found by linter tools."
            logger.info("Posting lint results to %s/%s#%s: %d chars", owner, repo, pr_number, len(body))
            gh = get_github_client()
            await gh.post_comment(owner, repo, pr_number, body)
        else:
            by_tool: dict[str, list[dict]] = {}
            for f in findings:
                by_tool.setdefault(f.get("tool", "lint"), []).append(f)

            gh = get_github_client()

            # 1. Summary comment — one line per tool
            summary = ["## 🔬 BugViper Static Analysis", ""]
            summary.append(f"*{len(findings)} finding(s) from {len(by_tool)} tool(s)*")
            summary.append("")
            for tool in sorted(by_tool):
                items = by_tool[tool]
                summary.append(f"- **{tool}** — {len(items)} issue(s)")
            summary.append("")
            summary.append("Expand the comments below for details.")
            await gh.post_comment(owner, repo, pr_number, "\n".join(summary))
            logger.info("Posted summary to %s/%s#%s", owner, repo, pr_number)

            def _build_fix_prompt(tool: str, items: list[dict]) -> str:
                prompt_lines = [f"Fix the {len(items)} issue(s) found by {tool} in these files:"]
                for it in items:
                    prompt_lines.append(
                        f"- {it.get('file', '')}:{it.get('line', 1)} "
                        f"[{it.get('rule', '')}] {it.get('message', '')}"
                    )
                prompt_lines.append("")
                prompt_lines.append("Apply the suggested fixes while keeping the existing code style.")
                prompt_text = "\n".join(prompt_lines)
                lines_prompt = [
                    "<details>",
                    "<summary>🔧 Prompt to Fix <em>(copy button below)</em></summary>",
                    "",
                    "```text",
                    prompt_text,
                    "```",
                    "</details>",
                ]
                return "\n".join(lines_prompt)

            # 2. One comment per tool with findings + fix prompt
            MAX_CHARS = 58000
            for tool in sorted(by_tool):
                items = sorted(by_tool[tool], key=lambda i: (i.get("file", ""), i.get("line", 0)))
                rows = ["| File | Line | Rule | Message |", "|------|------|------|---------|"]
                omitted = 0
                for item in items:
                    row = (
                        f"| `{item.get('file', '')}` | {item.get('line', 1)} "
                        f"| `{item.get('rule', '')}` | {item.get('message', '')} |"
                    )
                    if len("\n".join(rows)) + len(row) > MAX_CHARS:
                        omitted += 1
                    else:
                        rows.append(row)
                if omitted:
                    rows.append(f"| ... | ... | *+{omitted} more issue(s)* |")
                rows.append("")
                table = "\n".join(rows)
                prompt = _build_fix_prompt(tool, items[:omitted] if omitted else items)
                lines = [
                    f"<details>",
                    f"<summary><strong>{tool}</strong> — {len(items)} issue(s)</summary>",
                    "",
                    table,
                    "</details>",
                    "",
                    prompt,
                ]
                body = "\n".join(lines)
                await gh.post_comment(owner, repo, pr_number, body)
                logger.info("Posted %s findings to %s/%s#%s (%d issues, %d omitted)", tool, owner, repo, pr_number, len(items), omitted)

        logger.info("Posted all lint-only results to %s/%s#%s", owner, repo, pr_number)

    except Exception as exc:
        logger.exception("lint-only pipeline failed for %s/%s#%s: %s", owner, repo, pr_number, exc)
    finally:
        if sbx is not None:
            logger.info("Cleaning up sandbox %s", sbx.sandbox_id)
            kill_sandbox(sbx)


async def run_full_review(
    owner: str,
    repo: str,
    pr_number: int,
    uid: str,
    review_type: str = "full_review",
    comment_id: int | None = None,
) -> None:
    """Run static analysis first, then AI review. Both isolated, separate comments."""
    from ai_code_review.config import config, ensure_env

    ensure_env()
    os.environ["E2B_API_KEY"] = config.E2B_API_KEY

    logger.info("Full review for %s/%s#%s — starting static analysis", owner, repo, pr_number)
    await run_lint_only(owner, repo, pr_number, uid)

    logger.info("Full review for %s/%s#%s — starting AI review", owner, repo, pr_number)
    from ai_code_review import run_review_pipeline, run_deep_review_pipeline

    if config.DEEPAGENT_REVIEW_MODE == 'deep':
        await run_deep_review_pipeline(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_type=review_type,
            comment_id=comment_id,
            uid=uid,
        )
    else:
        await run_review_pipeline(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_type=review_type,
            comment_id=comment_id,
            uid=uid,
        )

    logger.info("Full review completed for %s/%s#%s", owner, repo, pr_number)
