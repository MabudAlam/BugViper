"""Result merging logic for batched PR reviews."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get(result: dict, *keys: str, default: Any = None) -> Any:
    """Try multiple keys in order, return first found value."""
    for key in keys:
        if key in result:
            return result[key]
    return default


def merge_batch_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        logger.warning("No batch results to merge - marking as failed")
        return _error_result("No batch results returned")

    if len(results) == 1:
        if _is_valid_result(results[0]):
            return results[0]
        logger.warning("Single batch returned invalid result - marking as failed")
        return _error_result("Batch returned invalid result")

    merged: dict[str, Any] = {
        "file_based_issues": [],
        "file_based_positive_findings": [],
        "file_based_walkthrough": {},
        "judge_verdict": {
            "total_issues": 0,
            "total_positives": 0,
            "verdict": "passed",
            "issues_by_severity": {},
            "issues_by_category": {},
            "batches_total": len(results),
            "batches_failed": 0,
        },
    }

    for result in results:
        if not _is_valid_result(result):
            merged["judge_verdict"]["verdict"] = "failed"
            merged["judge_verdict"]["batches_failed"] = (
                merged["judge_verdict"].get("batches_failed", 0) + 1
            )
            logger.warning("Skipping invalid batch result: %s", result)
            continue
        _merge_issues(merged, result)
        _merge_positives(merged, result)
        _merge_walkthrough(merged, result)
        _update_verdict(merged, result)
        # Carry through fields needed downstream
        for key in ("summary", "raw_agent_outputs", "judge_verdict"):
            if key in result and key not in merged:
                merged[key] = result[key]
            elif key in result and key == "judge_verdict":
                # Merge verdicts lists from multiple batches
                existing = merged.get("judge_verdict", {})
                existing_verdicts = existing.get("verdicts", [])
                batch_verdicts = result["judge_verdict"].get("verdicts", [])
                existing["verdicts"] = existing_verdicts + batch_verdicts

    if merged["judge_verdict"].get("batches_failed", 0) > 0:
        merged["judge_verdict"]["verdict"] = "failed"

    # Normalize: ensure both old-style (file_based_*) and new-style (issues) keys exist
    merged.setdefault("issues", merged["file_based_issues"])
    merged.setdefault("positives", merged["file_based_positive_findings"])
    merged.setdefault(
        "walkthrough",
        [{"file": fp, "summary": s} for fp, s in merged["file_based_walkthrough"].items()],
    )

    return merged


def _is_valid_result(result: dict[str, Any]) -> bool:
    if not result or not isinstance(result, dict):
        return False
    if isinstance(result.get("file_based_issues"), list):
        return True
    if isinstance(result.get("issues"), list):
        return True
    return False


def _merge_issues(merged: dict, result: dict) -> None:
    issues = _get(result, "file_based_issues", "issues", default=[])
    for issue in issues:
        merged["file_based_issues"].append(issue)


def _merge_positives(merged: dict, result: dict) -> None:
    positives = _get(result, "file_based_positive_findings", "positives", default=[])
    for positive in positives:
        merged["file_based_positive_findings"].append(positive)


def _merge_walkthrough(merged: dict, result: dict) -> None:
    walkthrough = _get(result, "file_based_walkthrough", "walkthrough", default={})
    if isinstance(walkthrough, dict):
        merged["file_based_walkthrough"].update(walkthrough)
    elif isinstance(walkthrough, list):
        for entry in walkthrough:
            if isinstance(entry, dict) and "file" in entry:
                merged["file_based_walkthrough"][entry["file"]] = entry.get("summary", "")


def _update_verdict(merged: dict, result: dict) -> None:
    batch_verdict = result.get("judge_verdict", {})
    if not batch_verdict:
        return

    merged_verdict = merged["judge_verdict"]

    merged_verdict["total_issues"] += batch_verdict.get("total_issues", 0)
    merged_verdict["total_positives"] += batch_verdict.get("total_positives", 0)

    if batch_verdict.get("verdict") == "failed":
        merged_verdict["verdict"] = "failed"

    severity = batch_verdict.get("issues_by_severity", {})
    for sev, count in severity.items():
        merged_verdict["issues_by_severity"][sev] = (
            merged_verdict["issues_by_severity"].get(sev, 0) + count
        )

    category = batch_verdict.get("issues_by_category", {})
    for cat, count in category.items():
        merged_verdict["issues_by_category"][cat] = (
            merged_verdict["issues_by_category"].get(cat, 0) + count
        )


def _error_result(reason: str = "Review failed") -> dict[str, Any]:
    return {
        "file_based_issues": [],
        "file_based_positive_findings": [],
        "file_based_walkthrough": {},
        "judge_verdict": {
            "total_issues": 0,
            "total_positives": 0,
            "verdict": "failed",
            "issues_by_severity": {},
            "issues_by_category": {},
            "error_reason": reason,
        },
    }
