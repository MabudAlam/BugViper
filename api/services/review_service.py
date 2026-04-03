"""Simplified review pipeline using 3-node agent directly.

Architecture:
    review_service.py (Gathers context, builds prompts, aggregates results)
        ↓
    context_builder.py (Builds file context markdown)
        ↓
    agent_executor.py (Runs 3-node agent)
        ↓
    ngraph.py (Builds graph & executes nodes)
        ↓
    Returns results to review_service.py
"""

import asyncio
import json
import logging
import traceback
from typing import List

from api.models.ast_results import ParsedFile
from api.services.firebase_service import firebase_service
from api.services.lint_service import run_lint
from api.services.parse_file_to_ast import _ast_parse_file_full
from api.utils.comment_formatter import (
    format_github_comment,
    format_inline_comment,
    format_review_summary,
)
from code_review_agent.agent_executor import execute_review_agent
from code_review_agent.config import config
from api.services.context_builder import (
    build_file_context,
    build_file_diff_from_patch,
    format_previous_issues,
)
from code_review_agent.models.agent_schemas import ContextData, Issue, ReconciledReview
from common.debug_writer import make_review_dir, write_step
from common.diff_line_mapper import (
    get_hunk_ranges,
    get_valid_comment_lines,
    is_line_in_hunk,
    validate_issue_line,
)
from common.firebase_models import PRMetadata, PrReviewStatus, ReviewRunData
from common.github_client import get_github_client
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


async def review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    neo4j: Neo4jClient | None = None,
) -> None:
    """Run code review on a PR using the 3-node agent.

    This function:
    1. Fetches PR data (diff, files, ASTs)
    2. Builds context for each file
    3. Runs 3-node agent on each file
    4. Aggregates results
    5. Posts GitHub comments
    """
    project_owner = firebase_service.find_project_owner_id(owner)
    logger.info(f"Reviewing {owner}/{repo}#{pr_number} (owner: {project_owner})")

    repo_id = f"{owner}/{repo}"
    if neo4j is None:
        neo4j = get_neo4j_client()

    # Validate PR number
    try:
        pr_number = int(pr_number)
        if pr_number <= 0:
            raise ValueError
    except (ValueError, TypeError):
        logger.error(f"Invalid pr_number {pr_number!r} — aborting")
        return

    review_dir = make_review_dir(owner, repo, pr_number)
    query_service = CodeSearchService(neo4j)

    # Set review status to RUNNING
    if project_owner:
        firebase_service.upsert_pr_metadata(
            project_owner,
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
        # Fetch PR data
        gh = get_github_client()
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )

        if not diff_text:
            logger.warning("Empty diff — skipping review")
            if project_owner:
                firebase_service.upsert_pr_metadata(
                    project_owner,
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
        file_diffs: dict[str, str] = {
            f["filename"]: f.get("patch") or "" for f in pr_files_data if f.get("patch")
        }
        files_changed = list(file_diffs.keys())

        # Fetch post-PR files
        pr_files_raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in files_changed],
            return_exceptions=True,
        )
        pr_files: dict[str, str] = {
            fp: content
            for fp, content in zip(files_changed, pr_files_raw)
            if not isinstance(content, Exception) and content is not None
        }

        # Parse ASTs
        parsed_files: List[ParsedFile] = [
            _ast_parse_file_full(fp, source) for fp, source in pr_files.items()
        ]

        write_step(
            review_dir,
            "02_parsed_files.json",
            json.dumps([pf.toDict() for pf in parsed_files], indent=2),
        )

        # Build code samples from Neo4j
        code_samples_by_file: dict[str, dict[str, list[dict]]] = {
            pf.path: {"classes": [], "functions": [], "imports": []} for pf in parsed_files
        }

        for pf in parsed_files:
            all_names: set[str] = set()

            for cls in pf.classes:
                all_names.add(cls.name)
            for fn in pf.functions:
                all_names.add(fn.name)
            for call in pf.call_sites:
                all_names.add(call.name)
            for imp in pf.imports:
                all_names.add(imp.name)

            for name in all_names:
                query_result = query_service.search_code(name)
                if not query_result:
                    continue

                for each_result in query_result:
                    result_path = each_result.get("path", pf.path)
                    result_type = each_result.get("type", "")
                    source_code = each_result.get("source_code", "")
                    docstring = each_result.get("docstring", "")

                    sample = {
                        "name": name,
                        "file": result_path,
                        "docstring": docstring,
                        "source_code": source_code,
                    }

                    if result_type == "class" or (
                        result_type not in ("function", "import")
                        and name in {c.name for c in pf.classes}
                    ):
                        code_samples_by_file[pf.path]["classes"].append(sample)
                    elif result_type == "function" or (
                        result_type not in ("class", "import")
                        and name in {fn.name for fn in pf.functions}
                    ):
                        code_samples_by_file[pf.path]["functions"].append(sample)
                    elif result_type == "import" or name in {i.name for i in pf.imports}:
                        code_samples_by_file[pf.path]["imports"].append(sample)
                    else:
                        code_samples_by_file[pf.path]["functions"].append(sample)

        # ─────────────────────────────────────────────────────────────────────────────
        # Run 3-node agent on each file
        # ─────────────────────────────────────────────────────────────────────────────

        all_issues = []
        all_positive_findings = []
        all_walkthroughs = []
        total_tool_rounds = 0

        for file_path in files_changed:
            content = pr_files.get(file_path, "")
            ast = next((pf for pf in parsed_files if pf.path == file_path), None)
            full_diff = build_file_diff_from_patch(file_path, file_diffs.get(file_path, ""))
            safe_filename = file_path.replace("/", "_").replace(".", "_")

            file_code_samples = (
                code_samples_by_file.get(file_path, {"classes": [], "functions": [], "imports": []})
                if code_samples_by_file
                else {"classes": [], "functions": [], "imports": []}
            )

            # Build file context
            file_context = build_file_context(
                file_path=file_path,
                full_diff=full_diff,
                full_file_content=content,
                file_ast=ast,
                previous_issues="No previous issues for this file.",
                explorer_context="",
                code_samples=file_code_samples,
            )

            # Write review prompt
            if review_dir:
                write_step(
                    review_dir,
                    f"04_review_prompt_{safe_filename}.md",
                    f"# Review Agent Prompt: {file_path}\n\n{file_context}",
                )

            # Run 3-node agent
            logger.info(f"Reviewing {file_path}...")
            result = await execute_review_agent(
                file_path=file_path,
                file_context=file_context,
                query_service=query_service,
                repo_id=repo_id,
                model=config.review_model,
                review_dir=review_dir,
                safe_filename=safe_filename,
            )

            # Check for errors
            if "error" in result:
                logger.error(f"Agent failed for {file_path}: {result['error']}")
                continue

            # Aggregate results
            for file_issue in result.get("file_based_issues", []):
                all_issues.append(file_issue)

            for finding in result.get("file_based_positive_findings", []):
                all_positive_findings.append(finding)

            for walk in result.get("file_based_walkthrough", []):
                all_walkthroughs.append(walk)

            total_tool_rounds += result.get("tool_rounds", 0)

        logger.info(
            f"Review complete: {len(all_issues)} total issues, "
            f"{len(all_positive_findings)} positive findings, "
            f"{total_tool_rounds} tool rounds"
        )

        # ─────────────────────────────────────────────────────────────────────────────
        # Run lint in parallel
        # ─────────────────────────────────────────────────────────────────────────────

        lint_issues_raw = await run_lint(pr_files)
        lint_issues = [i for i in lint_issues_raw if i.file in pr_files]

        # Convert to Issue format
        all_issues_formatted = []
        for file_issue in all_issues:
            for issue in file_issue.get("issues", []):
                all_issues_formatted.append(Issue(**issue))

        # Build walkthrough
        walk_through = []
        for walk in all_walkthroughs:
            for step in walk.get("walkthrough_steps", []):
                walk_through.append(step)

        for fp in files_changed:
            if not any(fp in entry for entry in walk_through):
                walk_through.append(f"{fp} — Modified")

        # Save to Firestore
        if project_owner:
            all_issues_flat = [issue.model_dump() for issue in all_issues_formatted]
            positive_findings_flat = []
            for finding in all_positive_findings:
                for pf in finding.get("positive_finding", []):
                    positive_findings_flat.append(pf)

            firebase_service.save_review_run(
                project_owner,
                owner,
                repo,
                pr_number,
                ReviewRunData(
                    issues=all_issues_flat + [i.model_dump() for i in lint_issues],
                    positive_findings=positive_findings_flat,
                    summary=f"Reviewed {len(files_changed)} files",
                    files_changed=files_changed,
                    repo_id=repo_id,
                    pr_number=pr_number,
                ),
            )

        # ─────────────────────────────────────────────────────────────────────────────
        # Post GitHub comments
        # ─────────────────────────────────────────────────────────────────────────────

        context_data = ContextData(
            files_changed=files_changed,
            modified_symbols=[],
            total_callers=0,
            risk_level="medium",
        )

        valid_comment_lines = get_valid_comment_lines(diff_text)
        hunk_ranges = get_hunk_ranges(diff_text)

        # Debug: dump diff parsing info
        debug_diff_info = {
            "valid_comment_lines": {k: sorted(list(v)) for k, v in valid_comment_lines.items()},
            "hunk_ranges": hunk_ranges,
        }
        write_step(review_dir, "00_diff_parsing_debug.json", json.dumps(debug_diff_info, indent=2))

        # High-confidence issues for inline comments
        inline_candidates = [
            i
            for i in all_issues_formatted
            if i.status in ("new", "still_open") and i.confidence >= 7
        ]
        has_blocking = any(i.category in ("bug", "security") for i in inline_candidates)
        review_event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

        # Separate issues into inline vs regular
        inline_to_post = []
        regular_comment_issues = []
        for issue in inline_candidates:
            file_hunks = hunk_ranges.get(issue.file, [])
            if is_line_in_hunk(issue.line_start, file_hunks):
                inline_to_post.append(issue)
            else:
                regular_comment_issues.append(issue)

        # Post inline comments
        inline_posted = inline_skipped = 0
        for issue in inline_to_post:
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

        # Post regular comments
        regular_comments_posted = 0
        for issue in regular_comment_issues:
            body = format_inline_comment(issue)
            ok = await gh.post_comment(owner, repo, pr_number, body)
            if ok:
                regular_comments_posted += 1

        # Post review summary
        reconciled = ReconciledReview(
            issues=all_issues_formatted,
            positive_findings=positive_findings_flat,
            summary=f"Reviewed {len(files_changed)} files",
        )

        debug_info = {
            "tool_rounds_used": total_tool_rounds,
            "lint_raw_count": len(lint_issues_raw),
            "lint_on_diff_count": len(lint_issues),
            "files_reviewed": len(files_changed),
        }

        review_body = format_review_summary(
            reconciled,
            context_data,
            pr_number,
            lint_issues=lint_issues,
            walk_through=walk_through,
            inline_posted=inline_posted,
            inline_skipped=inline_skipped,
            raw_agent_outputs={},
            debug_info=debug_info,
        )

        try:
            await gh.post_pr_review(owner, repo, pr_number, head_sha, review_body, review_event)
            logger.info(f"Posted review ({review_event}) on {owner}/{repo}#{pr_number}")
        except Exception:
            fallback = format_github_comment(
                reconciled,
                context_data,
                pr_number,
                lint_issues=lint_issues,
                walk_through=walk_through,
            )
            await gh.post_comment(owner, repo, pr_number, fallback)

        # Mark as COMPLETED
        if project_owner:
            firebase_service.upsert_pr_metadata(
                project_owner,
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
        logger.error(f"Review failed:\n{traceback.format_exc()}")
        try:
            gh = get_github_client()
            await gh.post_comment(
                owner,
                repo,
                pr_number,
                f"🚨 **BugViper Review Failed**\n\n`{error_msg}`",
            )
        except Exception:
            pass

        if project_owner:
            firebase_service.upsert_pr_metadata(
                project_owner,
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
