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
        if not isinstance(f, dict):
            logger.warning("Skipping non-dict item in lint issues: %s", type(f))
            continue

        tool = f.get("tool", "lint")
        rule = f.get("rule") or tool
        file_path = f.get("file")
        message = f.get("message")

        # Skip if required fields are missing
        if not file_path or not message:
            logger.warning("Skipping lint issue with missing file or message: %s", f)
            continue

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

        # Parse line number safely
        try:
            line_num = int(f.get("line") or 1)
            line_num = max(line_num, 1)  # Ensure at least 1
        except (ValueError, TypeError):
            line_num = 1

        url_note = f"  See: {f['url']}" if f.get("url") else ""
        issues.append(
            Issue(
                issue_type=issue_type,
                category=category,
                title=f"[{tool}] {rule}: {str(message)[:80]}",
                file=str(file_path),
                line_start=line_num,
                description=f"`{rule}` — {str(message)}{url_note}",
                confidence=10,  # linters are deterministic — always confidence 10
                status="new",
            )
        )
    return issues


# Maximum file size (1MB) to prevent memory issues
MAX_FILE_SIZE = 1 * 1024 * 1024

# Maximum total request size (10MB)
MAX_REQUEST_SIZE = 10 * 1024 * 1024


def _validate_file_size(file_contents: dict[str, str]) -> dict[str, str]:
    """Filter files by size limit and log warnings for oversized files."""
    validated = {}
    oversized_files = []

    total_size = 0
    for path, content in file_contents.items():
        content_size = len(content.encode("utf-8"))
        total_size += content_size

        if content_size > MAX_FILE_SIZE:
            oversized_files.append(path)
        else:
            validated[path] = content

    if oversized_files:
        logger.warning(
            "Skipping %d oversized files in lint: %s",
            len(oversized_files),
            oversized_files,
        )

    if total_size > MAX_REQUEST_SIZE:
        logger.warning(
            "Total lint request size (%d bytes) exceeds limit (%d bytes) — truncating",
            total_size,
            MAX_REQUEST_SIZE,
        )
        # Keep only first files until under limit
        truncated = {}
        current_size = 0
        for path, content in validated.items():
            if current_size + len(content.encode("utf-8")) <= MAX_REQUEST_SIZE:
                truncated[path] = content
                current_size += len(content.encode("utf-8"))
            else:
                break
        validated = truncated

    return validated


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

    if not file_contents:
        logger.debug("No files to lint")
        return []

    # Validate and filter file sizes
    file_contents = _validate_file_size(file_contents)

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
        if not isinstance(raw_issues, list):
            logger.warning(
                "Lint service returned non-list issues: %s — skipping",
                type(raw_issues),
            )
            raw_issues = []

        issues = _to_issues(raw_issues)
        logger.info(
            "Lint service returned %d issues (%s) — converted to %d Issue objects",
            len(raw_issues),
            ", ".join(data.get("tools_run", [])),
            len(issues),
        )
        return issues

    except httpx.TimeoutException:
        logger.warning("Lint service timed out after 90s — continuing without lint results")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Lint service returned HTTP %d (%s) — continuing without lint results",
            e.response.status_code,
            e,
        )
        return []
    except Exception as e:
        logger.warning("Lint service call failed (%s) — continuing without lint results", e)
        return []
