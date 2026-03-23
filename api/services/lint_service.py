"""Lint service client.

Calls the lint microservice (local Docker or Cloud Run) to run static analysis
on PR file contents. Returns Issue objects ready to merge with LLM findings.

Environment:
  LINT_SERVICE_URL — if set, POSTs to that URL (Docker locally / Cloud Run in prod).
                     if not set, linting is skipped and an empty list is returned.
"""

from __future__ import annotations

import logging
import os

import httpx

from code_review_agent.models.agent_schemas import Issue

logger = logging.getLogger(__name__)

LINT_SERVICE_URL = os.getenv("LINT_SERVICE_URL", "").rstrip("/")

# Maps file extensions → canonical language names (mirrors common/languages.py)
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ipynb": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".java": "java",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".hs": "haskell",
    # rust/scala/swift/c_sharp: no standalone tool in the lint service yet
}

# Security tools in the lint service always run; classify them appropriately
_SECURITY_TOOLS = {"bandit", "semgrep", "gitleaks"}


# Ruff rule prefix → issue category
def _ruff_category(rule: str) -> str:
    if rule.startswith("S"):
        return "security"  # flake8-bandit
    if rule.startswith(("E", "W", "N")):
        return "style"
    if rule.startswith(("F", "B")):
        return "bug"  # pyflakes / bugbear
    return "style"


def _to_issues(raw: list[dict]) -> list[Issue]:
    issues: list[Issue] = []
    for f in raw:
        tool = f.get("tool", "lint")
        rule = f.get("rule") or tool

        # Category
        if tool in _SECURITY_TOOLS:
            category = "security"
        elif tool == "ruff":
            category = _ruff_category(rule)
        else:
            category = "style"

        # issue_type — human-readable label, no severity signals
        if tool in _SECURITY_TOOLS:
            issue_type = "Security concern"
        elif category == "bug":
            issue_type = "Potential issue"
        else:
            issue_type = "Code quality"

        url_note = f"  See: {f['url']}" if f.get("url") else ""
        issues.append(
            Issue(
                issue_type=issue_type,
                category=category,
                title=f"[{tool}] {rule}: {f['message'][:80]}",
                file=f["file"],
                line_start=max(int(f.get("line") or 1), 1),
                description=f"`{rule}` — {f['message']}{url_note}",
                confidence=10,  # linters are deterministic — always confidence 10
                status="new",
            )
        )
    return issues


async def run_lint(file_contents: dict[str, str]) -> list[Issue]:
    """Run static analysis on PR file contents.

    Returns an empty list (silently) if:
    - LINT_SERVICE_URL is not configured
    - The lint service is unreachable
    - No supported file types are present
    """
    if not LINT_SERVICE_URL:
        logger.info("LINT_SERVICE_URL not set — skipping lint")
        return []

    languages = list(
        {
            _EXT_TO_LANG[f".{p.rsplit('.', 1)[-1]}"]
            for p in file_contents
            if "." in p and f".{p.rsplit('.', 1)[-1]}" in _EXT_TO_LANG
        }
    )
    if not languages:
        logger.debug("No lintable files in PR — skipping lint")
        return []

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{LINT_SERVICE_URL}/lint",
                json={"files": file_contents, "languages": languages},
            )
            resp.raise_for_status()
            data = resp.json()

        raw_issues = data.get("issues", [])
        issues = _to_issues(raw_issues)
        logger.info(
            "Lint service returned %d issues (%s) — converted to %d Issue objects",
            len(raw_issues),
            ", ".join(data.get("tools_run", [])),
            len(issues),
        )
        return issues

    except Exception as e:
        logger.warning("Lint service call failed (%s) — continuing without lint results", e)
        return []
