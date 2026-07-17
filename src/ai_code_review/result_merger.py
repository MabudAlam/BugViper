"""Result merging logic for batched PR reviews."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        "batches_total": len(results),
        "batches_failed": 0,
    }

    for result in results:
        if not _is_valid_result(result):
            merged["batches_failed"] += 1
            logger.warning("Skipping invalid batch result: %s", result)
            continue
        _merge_issues(merged, result)
        _merge_positives(merged, result)
        _merge_walkthrough(merged, result)
        for key in ("summary", "raw_agent_outputs"):
            if key in result and key not in merged:
                merged[key] = result[key]

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
    issues = result.get("file_based_issues") or result.get("issues") or []
    for issue in issues:
        merged["file_based_issues"].append(issue)


def _merge_positives(merged: dict, result: dict) -> None:
    positives = result.get("file_based_positive_findings") or result.get("positives") or []
    for positive in positives:
        merged["file_based_positive_findings"].append(positive)


def _merge_walkthrough(merged: dict, result: dict) -> None:
    walkthrough = result.get("file_based_walkthrough") or result.get("walkthrough") or {}
    if isinstance(walkthrough, dict):
        merged["file_based_walkthrough"].update(walkthrough)
    elif isinstance(walkthrough, list):
        for entry in walkthrough:
            if isinstance(entry, dict) and "file" in entry:
                merged["file_based_walkthrough"][entry["file"]] = entry.get("summary", "")


def _error_result(reason: str = "Review failed") -> dict[str, Any]:
    return {
        "file_based_issues": [],
        "file_based_positive_findings": [],
        "file_based_walkthrough": {},
        "error_reason": reason,
    }
