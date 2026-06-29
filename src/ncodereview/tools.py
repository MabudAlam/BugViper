"""Host-side tools exposed to the DeepAgent.

These run on the API server, not inside the e2b sandbox, so credentials never
leave the host. The DeepAgent invokes them via its `task` tool — subagents
return structured JSON, the orchestrator aggregates, then calls `submit_review`
exactly once.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.tools import tool

from api.services.lint_service import run_lint
from api.utils.comment_formatter import format_inline_comment, format_review_summary
from common.github_client import GitHubClient

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _coerce_json(raw: str) -> Any:
    """Parse JSON, tolerating ```json ... ``` fences the LLM sometimes adds."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    return json.loads(text)


def _normalize_issues(file_issues_json: list | dict) -> list[dict]:
    """Accept either a flat list of issues or wrapped FileBasedIssues dicts.

    The orchestrator sometimes flattens subagent output into a bare array of
    issues; the original pipeline spec wraps them as [{file: ..., issues: [...]}].
    Either way we return a flat list of issue dicts.
    """
    if isinstance(file_issues_json, dict):
        file_issues_json = [file_issues_json]

    flat: list[dict] = []
    for entry in file_issues_json:
        if not isinstance(entry, dict):
            continue
        if "issues" in entry and isinstance(entry["issues"], list):
            for issue in entry["issues"]:
                if isinstance(issue, dict):
                    flat.append(issue)
        elif "file" in entry and ("line_start" in entry or "title" in entry):
            flat.append(entry)
    return flat


def _clamp_confidence(value: Any) -> int:
    """Issue.confidence is constrained to [0, 10]; clamp anything the LLM emits."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 8
    return max(0, min(10, n))


def build_posting_tools(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    diff_text: str,
    pr_files: dict[str, str],
    gh: GitHubClient,
    model_label: str = "deepagent",
) -> list[Any]:
    """Return the host-side tools the orchestrator can call.

    `submit_review` is the single tool the agent uses to finalize; it formats
    the review with the existing comment formatter and posts via the GitHub client.
    Lint runs inside `submit_review` to stay in sync with the legacy pipeline.
    """
    posted_lock = {"done": False}

    @tool
    async def submit_review(
        summary: str,
        file_issues_json: str,
        positives_json: str = "[]",
        walkthrough_json: str = "[]",
    ) -> str:
        """Finalize and post the PR review. CALL THIS EXACTLY ONCE.

        Args:
            summary: 1-3 paragraph overall review summary (Markdown).
            file_issues_json: JSON array of issues. Either format works:
                A) Flat list — [{"file": "...", "line_start": 10, "title": "...",
                                  "severity": "high", "category": "bug",
                                  "description": "...", "suggestion": "...",
                                  "code_snippet": "...", "confidence": 9, ...}, ...]
                B) Wrapped — [{"file": "...", "issues": [{...}, {...}]}, ...]
                Each issue needs: file, line_start, severity, category, title.
                Optional: line_end, description, suggestion, impact, code_snippet, confidence.
            positives_json: JSON array of {"file_path": str, "positive_finding": [str, ...]}.
            walkthrough_json: JSON array of {"file": str, "summary": str}.

        Returns:
            Human-readable status of what was posted.
        """
        if posted_lock["done"]:
            return "Error: submit_review was already called — refusing to double-post."

        try:
            file_issues = _coerce_json(file_issues_json)
            positives = _coerce_json(positives_json)
            walkthrough = _coerce_json(walkthrough_json)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return f"Error: invalid JSON payload — {exc}"

        all_issues_raw = _normalize_issues(file_issues)

        all_issues: list = []
        for issue in all_issues_raw:
            issue["status"] = issue.get("status", "new")
            if issue["status"] not in ("new", "still_open", "fixed"):
                issue["status"] = "new"
            issue["confidence"] = _clamp_confidence(issue.get("confidence", 8))
            if not isinstance(issue.get("line_start"), int) or issue["line_start"] < 1:
                continue
            issue.setdefault("file", "")
            issue.setdefault("title", "Untitled issue")
            issue.setdefault("category", "bug")
            issue.setdefault("severity", "medium")
            issue.setdefault("issue_type", "Potential issue")
            issue.setdefault("description", "")
            issue.setdefault("suggestion", "")
            issue.setdefault("impact", "")
            issue.setdefault("code_snippet", "")
            issue.setdefault("line_end", None)
            all_issues.append(issue)

        from code_review_agent.models.agent_schemas import Issue, ReconciledReview

        try:
            issue_models = [Issue(**i) for i in all_issues]
        except Exception as exc:
            return f"Error: failed to construct Issue models — {exc}"

        reconciled = ReconciledReview(
            issues=issue_models,
            positive_findings=[
                pf for finding in positives for pf in finding.get("positive_finding", [])
            ],
            summary=summary,
        )

        try:
            lint_results = await run_lint(pr_files)
            lint_issues = [i for i in lint_results if i.file in pr_files]
        except Exception as exc:
            logger.warning("Lint failed during deepagent review: %s", exc)
            lint_issues = []

        walkthrough_lines = [f"{w.get('file', '?')} — {w.get('summary', '')}" for w in walkthrough]

        from code_review_agent.config import config as legacy_config

        original_model = legacy_config.synthesis_model
        legacy_config.synthesis_model = model_label
        try:
            body = format_review_summary(
                reconciled,
                None,
                pr_number,
                lint_issues=lint_issues,
                walk_through=walkthrough_lines,
                inline_posted=0,
                inline_skipped=0,
                raw_agent_outputs={},
                debug_info={"deepagent": True, "head_sha": head_sha},
            )
        finally:
            legacy_config.synthesis_model = original_model

        has_blocking = any(i.category in ("bug", "security") for i in issue_models)
        event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

        from common.diff_line_mapper import get_hunk_ranges, is_line_in_hunk

        hunk_ranges = get_hunk_ranges(diff_text)

        inline_posted = inline_skipped = 0
        for issue in issue_models:
            if issue.confidence < 7 or issue.status not in ("new", "still_open"):
                continue
            file_hunks = hunk_ranges.get(issue.file, [])
            if not is_line_in_hunk(issue.line_start, file_hunks):
                continue
            ok = await gh.post_inline_comment(
                owner,
                repo,
                pr_number,
                head_sha,
                issue.file,
                issue.line_start,
                format_inline_comment(issue),
            )
            if ok:
                inline_posted += 1
            else:
                inline_skipped += 1

        await gh.post_pr_review(owner, repo, pr_number, head_sha, body, event)
        posted_lock["done"] = True

        return (
            f"Posted review ({event}): {len(issue_models)} issues, "
            f"{inline_posted} inline comments posted, {inline_skipped} skipped, "
            f"{len(lint_issues)} lint findings included."
        )

    return [submit_review]
