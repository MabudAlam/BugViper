"""Lint microservice — static analysis for all BugViper-supported languages.

POST /lint   { files: {rel_path: source}, languages: ["python", "go", ...] }
GET  /health
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from pydantic import BaseModel

from lint_service.runners import (
    run_bandit,
    run_cppcheck,
    run_eslint,
    run_gitleaks,
    run_golangci_lint,
    run_hlint,
    run_ktlint,
    run_phpcs,
    run_pmd,
    run_rubocop,
    run_ruff,
    run_semgrep,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="BugViper Lint Service", version="2.0.0")

Runner = Callable[[Path, dict[str, str]], list[dict]]

# ── Language → tool registry ──────────────────────────────────────────────────
# Maps canonical language name (matching common/languages.py) to its runners.
# Tools listed here run only when that language is present in the PR.
LANG_RUNNERS: dict[str, list[Runner]] = {
    "python": [run_ruff, run_bandit],
    "javascript": [run_eslint],
    "typescript": [run_eslint],
    "go": [run_golangci_lint],
    "ruby": [run_rubocop],
    "c": [run_cppcheck],
    "cpp": [run_cppcheck],
    "java": [run_pmd],
    "php": [run_phpcs],
    "kotlin": [run_ktlint],
    "haskell": [run_hlint],
    # rust: clippy needs full cargo project — skip
    # c_sharp: roslyn needs .csproj — skip
    # scala: needs sbt — skip
    # swift: swiftlint binary not on Linux — skip
}

# Security tools run on every PR regardless of language
SECURITY_RUNNERS: list[Runner] = [run_semgrep, run_gitleaks]


class LintRequest(BaseModel):
    files: dict[str, str]  # {repo-relative path: source code}
    languages: list[str]  # canonical language names from common/languages.py


class LintResponse(BaseModel):
    issues: list[dict]
    run_id: str
    files_checked: int
    tools_run: list[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/lint", response_model=LintResponse)
def lint(req: LintRequest) -> LintResponse:
    run_id = uuid.uuid4().hex[:8]
    tmp = Path(tempfile.mkdtemp(prefix=f"bv_{run_id}_"))

    try:
        for rel_path, source in req.files.items():
            dest = tmp / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(source, encoding="utf-8", errors="replace")

        langs = {lang.lower() for lang in req.languages}
        issues: list[dict] = []
        tools_run: list[str] = []

        # Language-specific tools — deduplicate runners (e.g. cppcheck for both c+cpp)
        seen: set[Runner] = set()
        for lang in sorted(langs):
            for runner in LANG_RUNNERS.get(lang, []):
                if runner in seen:
                    continue
                seen.add(runner)
                found = runner(tmp, req.files)
                if found:
                    issues += found
                    tools_run.append(runner.__name__.removeprefix("run_"))
                    logger.info("run_id=%s %s=%d", run_id, runner.__name__, len(found))

        # Security tools always run
        for runner in SECURITY_RUNNERS:
            found = runner(tmp, req.files)
            if found:
                issues += found
                tools_run.append(runner.__name__.removeprefix("run_"))
                logger.info("run_id=%s %s=%d", run_id, runner.__name__, len(found))

        logger.info("run_id=%s total=%d issues, tools=%s", run_id, len(issues), tools_run)
        return LintResponse(
            issues=issues,
            run_id=run_id,
            files_checked=len(req.files),
            tools_run=tools_run,
        )

    except Exception:
        logger.exception("Lint run %s failed", run_id)
        return LintResponse(issues=[], run_id=run_id, files_checked=0, tools_run=[])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
