"""Per-tool lint runners covering all BugViper-supported languages.

Each runner:
  - Returns [] immediately if the tool is not installed (graceful degradation)
  - Returns [] if no matching files exist in the PR
  - Never raises — all errors are logged and swallowed
  - Uses JSON output where available, falls back to XML/text parsing otherwise

Tool coverage:
  Python       → ruff, bandit
  JS/TS        → eslint
  Go           → golangci-lint
  Ruby         → rubocop
  C/C++        → cppcheck
  Java         → pmd
  PHP          → phpcs
  Kotlin       → ktlint
  Haskell      → hlint
  Security     → semgrep (multi-language), gitleaks (secrets)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _available(tool: str) -> bool:
    return shutil.which(tool) is not None


def _rel(path: str, tmp: Path) -> str:
    try:
        return Path(path).relative_to(tmp).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


# ── Python ────────────────────────────────────────────────────────────────────


def run_ruff(tmp: Path, files: dict[str, str]) -> list[dict]:
    py_files = [f for f in files if f.endswith((".py", ".ipynb"))]
    if not py_files or not _available("ruff"):
        return []
    try:
        r = _run(["ruff", "check", "--output-format=json", "--no-cache", *py_files], tmp)
        raw = json.loads(r.stdout or "[]")
    except Exception as e:
        logger.warning("ruff failed: %s", e)
        return []
    return [
        {
            "file": _rel(f["filename"], tmp),
            "line": f["location"]["row"],
            "col": f["location"]["column"],
            "rule": f.get("code") or "ruff",
            "message": f["message"],
            "severity": "error" if (f.get("code") or "")[:1] == "E" else "warning",
            "tool": "ruff",
            "url": f.get("url") or "",
        }
        for f in raw
    ]


def run_bandit(tmp: Path, files: dict[str, str]) -> list[dict]:
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files or not _available("bandit"):
        return []
    try:
        r = _run(["bandit", "-f", "json", "-q", "--exit-zero", *py_files], tmp)
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("bandit failed: %s", e)
        return []
    return [
        {
            "file": _rel(f["filename"], tmp),
            "line": f["line_number"],
            "col": 0,
            "rule": f["test_id"],
            "message": f["issue_text"],
            "severity": f["issue_severity"].lower(),
            "tool": "bandit",
            "url": f.get("more_info") or "",
        }
        for f in raw.get("results", [])
    ]


# ── JavaScript / TypeScript ───────────────────────────────────────────────────

_JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def run_eslint(tmp: Path, files: dict[str, str]) -> list[dict]:
    js_files = [f for f in files if Path(f).suffix in _JS_EXTS]
    if not js_files or not _available("eslint"):
        return []
    try:
        r = _run(
            ["eslint", "--format=json", "--no-error-on-unmatched-pattern", *js_files],
            tmp,
        )
        raw = json.loads(r.stdout or "[]")
    except Exception as e:
        logger.warning("eslint failed: %s", e)
        return []
    issues = []
    for file_result in raw:
        rel = _rel(file_result["filePath"], tmp)
        for msg in file_result.get("messages", []):
            issues.append(
                {
                    "file": rel,
                    "line": msg.get("line") or 1,
                    "col": msg.get("column") or 0,
                    "rule": msg.get("ruleId") or "eslint",
                    "message": msg["message"],
                    "severity": "error" if msg.get("severity") == 2 else "warning",
                    "tool": "eslint",
                    "url": "",
                }
            )
    return issues


# ── Go ────────────────────────────────────────────────────────────────────────


def run_golangci_lint(tmp: Path, files: dict[str, str]) -> list[dict]:
    go_files = [f for f in files if f.endswith(".go")]
    if not go_files or not _available("golangci-lint"):
        return []
    # golangci-lint needs a go.mod to resolve the module
    if not (tmp / "go.mod").exists():
        (tmp / "go.mod").write_text("module bv_lint_temp\n\ngo 1.21\n")
    try:
        r = _run(
            [
                "golangci-lint",
                "run",
                "--out-format=json",
                "--no-config",
                "--timeout=45s",
                "--allow-parallel-runners",
                "./...",
            ],
            tmp,
            timeout=60,
        )
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("golangci-lint failed: %s", e)
        return []
    issues = []
    for issue in raw.get("Issues") or []:
        pos = issue.get("Pos", {})
        issues.append(
            {
                "file": _rel(pos.get("Filename", ""), tmp),
                "line": pos.get("Line", 1),
                "col": pos.get("Column", 0),
                "rule": issue.get("FromLinter", "golangci-lint"),
                "message": issue.get("Text", ""),
                "severity": "warning",
                "tool": "golangci-lint",
                "url": "",
            }
        )
    return issues


# ── Ruby ──────────────────────────────────────────────────────────────────────


def run_rubocop(tmp: Path, files: dict[str, str]) -> list[dict]:
    rb_files = [f for f in files if f.endswith(".rb")]
    if not rb_files or not _available("rubocop"):
        return []
    try:
        r = _run(["rubocop", "--format=json", "--no-color", *rb_files], tmp)
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("rubocop failed: %s", e)
        return []
    issues = []
    for file_result in raw.get("files", []):
        rel = _rel(file_result["path"], tmp)
        for offense in file_result.get("offenses", []):
            loc = offense.get("location", {})
            issues.append(
                {
                    "file": rel,
                    "line": loc.get("start_line", 1),
                    "col": loc.get("start_column", 0),
                    "rule": offense.get("cop_name", "rubocop"),
                    "message": offense.get("message", ""),
                    "severity": "error"
                    if offense.get("severity") in ("error", "fatal")
                    else "warning",
                    "tool": "rubocop",
                    "url": "",
                }
            )
    return issues


# ── C / C++ ───────────────────────────────────────────────────────────────────

_C_EXTS = {".c", ".cpp", ".h", ".hpp", ".cc", ".cxx"}


def run_cppcheck(tmp: Path, files: dict[str, str]) -> list[dict]:
    c_files = [f for f in files if Path(f).suffix in _C_EXTS]
    if not c_files or not _available("cppcheck"):
        return []
    try:
        r = _run(
            [
                "cppcheck",
                "--xml",
                "--xml-version=2",
                "--enable=warning,style,performance,portability",
                "--error-exitcode=0",
                *c_files,
            ],
            tmp,
        )
        root = ET.fromstring(r.stderr or "<results/>")
    except Exception as e:
        logger.warning("cppcheck failed: %s", e)
        return []
    issues = []
    for error in root.findall(".//error"):
        eid = error.get("id", "cppcheck")
        msg = error.get("verbose") or error.get("msg", "")
        sev = error.get("severity", "style")
        for loc in error.findall("location"):
            issues.append(
                {
                    "file": _rel(loc.get("file", ""), tmp),
                    "line": int(loc.get("line", 1)),
                    "col": int(loc.get("column", 0)),
                    "rule": eid,
                    "message": msg,
                    "severity": "error" if sev == "error" else "warning",
                    "tool": "cppcheck",
                    "url": "",
                }
            )
    return issues


# ── Java ──────────────────────────────────────────────────────────────────────


def run_pmd(tmp: Path, files: dict[str, str]) -> list[dict]:
    java_files = [f for f in files if f.endswith(".java")]
    if not java_files or not _available("pmd"):
        return []
    try:
        r = _run(
            [
                "pmd",
                "check",
                "-f",
                "json",
                "-R",
                "rulesets/java/quickstart.xml",
                "--no-cache",
                "-d",
                str(tmp),
            ],
            tmp,
            timeout=90,
        )
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("pmd failed: %s", e)
        return []
    issues = []
    for v in raw.get("violations") or []:
        issues.append(
            {
                "file": _rel(v.get("filename", ""), tmp),
                "line": v.get("beginline", 1),
                "col": v.get("begincolumn", 0),
                "rule": v.get("rule", "pmd"),
                "message": v.get("description", ""),
                # PMD priority: 1-2 = high, 3-4 = medium, 5 = low
                "severity": "error" if int(v.get("priority", 5)) <= 2 else "warning",
                "tool": "pmd",
                "url": "",
            }
        )
    return issues


# ── PHP ───────────────────────────────────────────────────────────────────────


def run_phpcs(tmp: Path, files: dict[str, str]) -> list[dict]:
    php_files = [f for f in files if f.endswith(".php")]
    if not php_files or not _available("phpcs"):
        return []
    try:
        r = _run(
            [
                "phpcs",
                "--report=json",
                "--standard=PSR12",
                "--error-severity=1",
                "--warning-severity=1",
                *php_files,
            ],
            tmp,
        )
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("phpcs failed: %s", e)
        return []
    issues = []
    for file_path, file_data in raw.get("files", {}).items():
        rel = _rel(file_path, tmp)
        for msg in file_data.get("messages", []):
            issues.append(
                {
                    "file": rel,
                    "line": msg.get("line", 1),
                    "col": msg.get("column", 0),
                    "rule": msg.get("source", "phpcs"),
                    "message": msg.get("message", ""),
                    "severity": "error" if msg.get("type") == "ERROR" else "warning",
                    "tool": "phpcs",
                    "url": "",
                }
            )
    return issues


# ── Kotlin ────────────────────────────────────────────────────────────────────


def run_ktlint(tmp: Path, files: dict[str, str]) -> list[dict]:
    kt_files = [f for f in files if Path(f).suffix in {".kt", ".kts"}]
    if not kt_files or not _available("ktlint"):
        return []
    try:
        r = _run(["ktlint", "--reporter=json", *kt_files], tmp)
        raw = json.loads(r.stdout or "[]")
    except Exception as e:
        logger.warning("ktlint failed: %s", e)
        return []
    issues = []
    for file_result in raw:
        rel = _rel(file_result.get("file", ""), tmp)
        for error in file_result.get("errors", []):
            issues.append(
                {
                    "file": rel,
                    "line": error.get("line", 1),
                    "col": error.get("column", 0),
                    "rule": error.get("rule", "ktlint"),
                    "message": error.get("message", ""),
                    "severity": "warning",
                    "tool": "ktlint",
                    "url": "",
                }
            )
    return issues


# ── Haskell ───────────────────────────────────────────────────────────────────


def run_hlint(tmp: Path, files: dict[str, str]) -> list[dict]:
    hs_files = [f for f in files if f.endswith(".hs")]
    if not hs_files or not _available("hlint"):
        return []
    try:
        r = _run(["hlint", "--json", *hs_files], tmp)
        raw = json.loads(r.stdout or "[]")
    except Exception as e:
        logger.warning("hlint failed: %s", e)
        return []
    return [
        {
            "file": _rel(f.get("file", ""), tmp),
            "line": f.get("startLine", 1),
            "col": f.get("startColumn", 0),
            "rule": f.get("hint", "hlint"),
            "message": f.get("hint", ""),
            "severity": "error" if f.get("severity") == "Error" else "warning",
            "tool": "hlint",
            "url": "",
        }
        for f in raw
    ]


# ── Security — multi-language ─────────────────────────────────────────────────


def run_semgrep(tmp: Path, files: dict[str, str]) -> list[dict]:
    if not files or not _available("semgrep"):
        return []
    try:
        r = _run(
            [
                "semgrep",
                "--config=p/security-audit",
                "--config=p/secrets",
                "--json",
                "--quiet",
                "--no-rewrite-rule-ids",
                str(tmp),
            ],
            tmp,
            timeout=120,
        )
        raw = json.loads(r.stdout or "{}")
    except Exception as e:
        logger.warning("semgrep failed: %s", e)
        return []
    issues = []
    for result in raw.get("results", []):
        extra = result.get("extra", {})
        refs = (extra.get("metadata") or {}).get("references") or []
        issues.append(
            {
                "file": _rel(result.get("path", ""), tmp),
                "line": result.get("start", {}).get("line", 1),
                "col": result.get("start", {}).get("col", 0),
                "rule": result.get("check_id", "semgrep"),
                "message": extra.get("message", ""),
                "severity": "error" if extra.get("severity") in ("ERROR", "WARNING") else "warning",
                "tool": "semgrep",
                "url": refs[0] if refs else "",
            }
        )
    return issues


def run_gitleaks(tmp: Path, files: dict[str, str]) -> list[dict]:
    if not files or not _available("gitleaks"):
        return []
    report_path = tmp / "gitleaks_report.json"
    try:
        _run(
            [
                "gitleaks",
                "detect",
                "--source",
                str(tmp),
                "--report-format",
                "json",
                "--report-path",
                str(report_path),
                "--no-git",
                "--exit-code",
                "0",
            ],
            tmp,
            timeout=30,
        )
        raw = json.loads(report_path.read_text()) if report_path.exists() else []
    except Exception as e:
        logger.warning("gitleaks failed: %s", e)
        return []
    return [
        {
            "file": _rel(f.get("File", ""), tmp),
            "line": f.get("StartLine", 1),
            "col": 0,
            "rule": f.get("RuleID", "gitleaks"),
            "message": f"Secret detected: {f.get('Description', '')}",
            "severity": "error",
            "tool": "gitleaks",
            "url": "",
        }
        for f in (raw or [])
    ]
