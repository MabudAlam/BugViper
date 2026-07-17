from __future__ import annotations

import json
import logging

from e2b import Sandbox

from static_code_review._shared import filter_changed_files, run_in_sandbox

logger = logging.getLogger(__name__)


def run_ruff(
    sbx: Sandbox, repo_dir: str, config_file: str | None, changed_files: list[str],
    debug_dir: str | None = None,
) -> list[dict]:
    files = filter_changed_files(changed_files, (".py", ".ipynb"))
    if not files:
        return []
    cfg_flag = f"--config {config_file}" if config_file else ""
    file_list = " ".join(files[:200])
    cmd = f"cd {repo_dir} && ruff check --output-format json {cfg_flag} {file_list} 2>/dev/null || true"
    rc, out, _ = run_in_sandbox(sbx, cmd)
    if rc != 0 or not out.strip():
        return []
    findings = []
    try:
        raw = json.loads(out)
    except json.JSONDecodeError:
        return findings
    for r in raw:
        findings.append({
            "tool": "ruff",
            "file": r.get("filename", ""),
            "line": r.get("location", {}).get("row", 1),
            "col": r.get("location", {}).get("column", 1),
            "rule": r.get("code", ""),
            "message": r.get("message", ""),
            "severity": "error" if r.get("fix") is None else "warning",
        })
    return findings
