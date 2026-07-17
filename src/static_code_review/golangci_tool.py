from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from e2b import Sandbox

from static_code_review._shared import filter_changed_files, run_in_sandbox

logger = logging.getLogger(__name__)


def run_golangci_lint(
    sbx: Sandbox,
    repo_dir: str,
    config_file: str | None,
    changed_files: list[str],
    debug_dir: str | None = None,
) -> list[dict]:
    files = filter_changed_files(changed_files, (".go",))
    if not files:
        return []
    changed_set = set(files)
    cfg_flag = f"-c {config_file}" if config_file else ""

    packages = set()
    for f in files:
        d = str(Path(f).parent)
        packages.add(f"./{d}/..." if d else "./...")
    pkg_list = " ".join(sorted(packages))

    cmd = f"cd {repo_dir} && golangci-lint run --output.json.path=stdout {cfg_flag} {pkg_list} 2>/dev/null || true"
    logger.info("golangci-lint cmd: %s", cmd)
    rc, out, _ = run_in_sandbox(sbx, cmd, timeout=180)
    logger.info("golangci-lint rc=%d, stdout=%d bytes", rc, len(out or ""))

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        raw_path = os.path.join(debug_dir, "golangci_raw.json")
        with open(raw_path, "w") as f:
            f.write(out or "")
        logger.info("Saved raw output to %s", raw_path)

    if rc != 0 or not out.strip():
        return []
    findings = []
    try:
        decoder = json.JSONDecoder()
        raw, _ = decoder.raw_decode(out)
    except json.JSONDecodeError as e:
        logger.warning("golangci-lint JSON parse failed: %s — output=%s", e, out[:500])
        return findings

    all_issues = raw.get("Issues", [])
    logger.info("golangci-lint: %d total issues in result", len(all_issues))

    for r in all_issues:
        f = r.get("Pos", {}).get("Filename", "")
        if f not in changed_set:
            continue
        findings.append({
            "tool": "golangci-lint",
            "file": f,
            "line": r.get("Pos", {}).get("Line", 1),
            "col": r.get("Pos", {}).get("Column", 1),
            "rule": r.get("FromLinter", ""),
            "message": r.get("Text", ""),
            "severity": "error" if r.get("Severity") == "error" else "warning",
        })

    if debug_dir:
        filtered_path = os.path.join(debug_dir, "golangci_filtered.json")
        with open(filtered_path, "w") as f:
            json.dump(findings, f, indent=2)
        logger.info("Saved filtered findings to %s", filtered_path)

    logger.info("golangci-lint: %d findings after changed-file filter", len(findings))
    return findings
