import asyncio
import importlib
import logging
import re
import traceback
from pathlib import Path
from typing import Dict, List

from api.models.ast_results import (
    ASTSummary,
    CallSite,
    ClassDef,
    FunctionDef,
    Import,
    ParsedFile,
    summarize_for_explorer,
)
from api.services.firebase_service import firebase_service
from api.services.lint_service import run_lint
from api.utils.comment_formatter import (
    format_github_comment,
    format_inline_comment,
    format_review_summary,
)
from api.services.parse_file_to_ast import _ast_parse_file_full
from code_review_agent.config import token_limits
from code_review_agent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview
from common.debug_writer import make_review_dir, write_step
from common.diff_line_mapper import (
    build_hunk_summary_for_prompt,
    get_valid_comment_lines,
    validate_issue_line,
)
from common.firebase_models import PRMetadata, PrReviewStatus, ReviewRunData
from common.github_client import get_github_client
from common.languages import EXT_TO_LANG, LANG_PARSER_REGISTRY
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


async def review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    neo4j: Neo4jClient | None = None,
) -> None:
    """Agentic per-file PR review using the new pipeline.

    Reuses existing infrastructure:
    - Already fetches post-PR files (lines 890-898)
    - Already parses ASTs (lines 902-904)
    - Already computes hunks

    Only adds:
    - Per-file review with guard detection
    - Parallel execution
    - Neo4j context per file
    """
    uid = firebase_service.lookup_uid_by_github_username(owner)
    repo_id = f"{owner}/{repo}"

    try:
        pr_number = int(pr_number)
        if pr_number <= 0:
            raise ValueError
    except (ValueError, TypeError):
        logger.error("Invalid pr_number %r — aborting", pr_number)
        return

    review_dir = make_review_dir(owner, repo, pr_number)

    if uid:
        firebase_service.upsert_pr_metadata(
            uid,
            owner,
            repo,
            pr_number,
            PRMetadata(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                repo_id=repo_id,
                review_status=PrReviewStatus.RUNNING,
            ),
        )

    try:
        gh = get_github_client()
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )

        if not diff_text:
            logger.warning("Empty diff — skipping review")
            if uid:
                firebase_service.upsert_pr_metadata(
                    uid,
                    owner,
                    repo,
                    pr_number,
                    PRMetadata(
                        owner=owner,
                        repo=repo,
                        pr_number=pr_number,
                        repo_id=repo_id,
                        review_status=PrReviewStatus.COMPLETED,
                    ),
                )
            return

        pr_title = pr_info.get("title", "")
        write_step(review_dir, "01_diff.md", f"# Diff\n{pr_title}\n```diff\n{diff_text}\n```")

        # Fetch file-specific diffs
        pr_files_data = await gh.get_pr_files(owner, repo, pr_number)
        file_diffs: Dict[str, str] = {
            f["filename"]: f.get("patch") or "" for f in pr_files_data if f.get("patch")
        }
        files_changed = list(file_diffs.keys())

        # Fetch post-PR files
        pr_files_raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in files_changed],
            return_exceptions=True,
        )
        pr_files: Dict[str, str] = {
            fp: content
            for fp, content in zip(files_changed, pr_files_raw)
            if not isinstance(content, Exception) and content is not None
        }

        # Parse ASTs
        parsed_files: List[ParsedFile] = [
            _ast_parse_file_full(fp, source) for fp, source in pr_files.items()
        ]

        # Run agentic review (lazy import to avoid circular dependency)
        from code_review_agent.agentic_pipeline import execute_agentic_review

        aggregated = await execute_agentic_review(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            neo4j=neo4j,
            pr_files=pr_files,
            parsed_files=parsed_files,
            diff_text=diff_text,
            pr_info=pr_info,
            file_diffs=file_diffs,
        )

        write_step(
            review_dir,
            "02_aggregated.md",
            f"# Aggregated\nSummary: {aggregated.summary}\n"
            f"Issues: {len(aggregated.issues)}\nFindings: {len(aggregated.positive_findings)}",
        )

        # Run lint in parallel
        lint_issues_raw = await run_lint(pr_files)
        lint_issues = [i for i in lint_issues_raw if i.file in pr_files]

        # Convert issues
        llm_issues = [Issue(**i) for i in aggregated.issues]

        # Build walkthrough
        walk_through = aggregated.walk_through or []
        for fp in files_changed:
            if not any(fp in entry for entry in walk_through):
                walk_through.append(f"{fp} — Modified")

        # Save to Firestore
        if uid:
            firebase_service.save_review_run(
                uid,
                owner,
                repo,
                pr_number,
                ReviewRunData(
                    issues=[i.model_dump() for i in lint_issues + llm_issues],
                    positive_findings=aggregated.positive_findings,
                    summary=aggregated.summary,
                    files_changed=files_changed,
                    repo_id=repo_id,
                    pr_number=pr_number,
                ),
            )

        # Context data
        context_data = ContextData(
            files_changed=files_changed,
            modified_symbols=[],
            total_callers=0,
            risk_level="medium",
        )

        # Valid comment lines
        valid_comment_lines = get_valid_comment_lines(diff_text)

        # High-confidence issues
        inline_candidates = [
            i for i in llm_issues if i.status in ("new", "still_open") and i.confidence >= 7
        ]
        has_blocking = any(i.category in ("bug", "security") for i in inline_candidates)
        review_event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

        # Post inline comments
        inline_posted = inline_skipped = 0
        for issue in inline_candidates:
            valid_start, valid_end = validate_issue_line(
                issue.file,
                issue.line_start,
                issue.line_end,
                valid_comment_lines,
            )
            if valid_start is None:
                inline_skipped += 1
                continue
            if valid_start != issue.line_start:
                issue.line_start = valid_start
                if issue.line_end and valid_end:
                    issue.line_end = valid_end
            body = format_inline_comment(issue)
            ok = await gh.post_inline_comment(
                owner,
                repo,
                pr_number,
                head_sha,
                issue.file,
                valid_start,
                body,
            )
            if ok:
                inline_posted += 1
            else:
                inline_skipped += 1

        # Post review comment
        reconciled = ReconciledReview(
            issues=llm_issues,
            positive_findings=aggregated.positive_findings,
            summary=aggregated.summary,
        )

        debug_info = {
            "tool_rounds_used": aggregated.total_tool_rounds,
            "lint_raw_count": len(lint_issues_raw),
            "lint_on_diff_count": len(lint_issues),
            "files_reviewed": aggregated.total_files_reviewed,
        }

        review_body = format_review_summary(
            reconciled,
            context_data,
            pr_number,
            lint_issues=lint_issues,
            walk_through=walk_through,
            inline_posted=inline_posted,
            inline_skipped=inline_skipped,
            raw_agent_json=aggregated.raw_agent_output,
            debug_info=debug_info,
        )

        try:
            await gh.post_pr_review(owner, repo, pr_number, head_sha, review_body, review_event)
            logger.info(
                "Posted AGENTIC review (%s) on %s/%s#%s", review_event, owner, repo, pr_number
            )
        except Exception:
            fallback = format_github_comment(
                reconciled,
                context_data,
                pr_number,
                lint_issues=lint_issues,
                walk_through=walk_through,
            )
            await gh.post_comment(owner, repo, pr_number, fallback)

        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.COMPLETED,
                ),
            )

    except Exception as e:
        error_msg = str(e) or type(e).__name__
        logger.error("Agentic review failed:\n%s", traceback.format_exc())
        try:
            gh = get_github_client()
            await gh.post_comment(
                owner,
                repo,
                pr_number,
                f"🚨 **BugViper Agentic Review Failed**\n\n`{error_msg}`",
            )
        except Exception:
            pass
        if uid:
            firebase_service.upsert_pr_metadata(
                uid,
                owner,
                repo,
                pr_number,
                PRMetadata(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    repo_id=repo_id,
                    review_status=PrReviewStatus.FAILED,
                    failed_reasons=[error_msg],
                ),
            )
