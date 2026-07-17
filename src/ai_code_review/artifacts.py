"""Debug artifact storage for review pipeline stages."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REVIEW_OUTPUT_DIR: Path | None = None
_REVIEW_STAGE: int = 0


def safe_serialize(obj: object) -> object:
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


def _ensure_output_dir(owner: str, repo: str, pr_number: int) -> Path:
    global _REVIEW_OUTPUT_DIR
    if _REVIEW_OUTPUT_DIR is not None:
        return _REVIEW_OUTPUT_DIR
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _REVIEW_OUTPUT_DIR = Path("output") / f"review-{owner}-{repo}-pr{pr_number}-{ts}"
    _REVIEW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _REVIEW_OUTPUT_DIR


def _save_stage(owner: str, repo: str, pr_number: int, stage: str, data: object) -> Path | None:
    global _REVIEW_STAGE
    try:
        out = _ensure_output_dir(owner, repo, pr_number)
        _REVIEW_STAGE += 1
        filename = f"{_REVIEW_STAGE:02d}_{stage}.json"
        path = out / filename
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False))
        logger.debug("Saved %s", path)
        return path
    except Exception as exc:
        logger.warning("Failed to save stage '%s': %s", stage, exc)
        return None


def _dump_debug_artifacts(
    owner: str,
    repo: str,
    pr_number: int,
    review_data: dict,
    review_diff_text: str,
) -> None:
    out = _ensure_output_dir(owner, repo, pr_number)

    try:
        (out / "diff.patch").write_text(review_diff_text)
    except Exception as exc:
        logger.warning("Failed to write diff debug artifact: %s", exc)

    try:
        (out / "raw_agent_output.json").write_text(json.dumps(review_data, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to write agent output debug artifact: %s", exc)

    try:
        raw_outputs = review_data.get("raw_agent_outputs") or {}
        for agent_name, raw_json in raw_outputs.items():
            (out / f"subagent_{agent_name}.json").write_text(
                raw_json if isinstance(raw_json, str) else json.dumps(raw_json, indent=2)
            )
    except Exception as exc:
        logger.warning("Failed to write subagent debug artifacts: %s", exc)

    logger.info("Debug artifacts dumped to %s", out)
