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
from api.services.context_builder import (
    build_file_context,
    build_file_diff_from_patch,
    build_pr_symbol_map,
    format_previous_issues,
)
from api.services.firebase_service import firebase_service
from api.services.lint_service import run_lint
from api.services.parse_file_to_ast import _ast_parse_file_full
from api.utils.comment_formatter import (
    format_github_comment,
    format_inline_comment,
    format_pr_description,
    format_review_summary,
)
from code_review_agent.agent_executor import execute_review_agent
from code_review_agent.config import config
from code_review_agent.models.agent_schemas import ContextData, Issue, ReconciledReview
from common.debug_writer import make_review_dir, write_step
from common.diff_line_mapper import (
    get_hunk_ranges,
    get_valid_comment_lines,
    is_line_in_hunk,
    validate_issue_line,
)
from common.diff_parser import split_diff_by_file
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
    review_type: str = "incremental_review",
    comment_id: int | None = None,
) -> None:
    """Run code review on a PR using the 3-node agent.

    This function:
    1. Fetches PR data (diff, files, ASTs)
    2. Builds context for each file
    3. Runs 3-node agent on each file
    4. Aggregates results
    5. Posts GitHub comments
    6. Adds checkmark reaction to triggering comment
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
        gh = get_github_client()

        # Ensure we don't reuse a stale cached PR payload across runs.
        # PRs can be updated between runs; we always want the latest head SHA.
        try:
            gh.clear_pr_cache(owner, repo, pr_number)
        except Exception:
            # Cache clearing is best-effort; if the method isn't available for some reason,
            # we still proceed with a fresh fetch attempt.
            pass

        # Fetch previous review run for incremental review
        previous_issues_by_file: dict[str, list[dict]] = {}
        if review_type == "incremental_review" and project_owner:
            try:
                last_run = firebase_service.get_last_review_run(
                    project_owner, owner, repo, pr_number
                )
                if last_run and last_run.get("issues"):
                    for issue in last_run.get("issues", []):
                        file_path = issue.get("file", "")
                        if file_path:
                            if file_path not in previous_issues_by_file:
                                previous_issues_by_file[file_path] = []
                            previous_issues_by_file[file_path].append(issue)
            except Exception as e:
                logger.warning(f"Failed to fetch previous review run: {e}")

        # Both modes use the full PR diff — the difference is in context, not scope
        diff_text, pr_info, head_sha = await asyncio.gather(
            gh.get_pr_diff(owner, repo, pr_number),
            gh.get_pr_info(owner, repo, pr_number),
            gh.get_pr_head_ref(owner, repo, pr_number),
        )

        if head_sha:
            write_step(review_dir, "00_pr_head_sha.txt", head_sha)
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

        file_diffs: dict[str, str] = split_diff_by_file(diff_text)
        files_changed_all = list(file_diffs.keys())

        # Fetch post-PR files
        pr_files_raw = await asyncio.gather(
            *[gh.get_file_content(owner, repo, f, ref=head_sha) for f in files_changed_all],
            return_exceptions=True,
        )
        pr_files: dict[str, str] = {
            fp: content
            for fp, content in zip(files_changed_all, pr_files_raw)
            if not isinstance(content, Exception) and content is not None
        }

        missing_post_pr_files = [fp for fp in files_changed_all if fp not in pr_files]
        if missing_post_pr_files:
            # Common cases: deleted files (PR removed them), renamed files, binary/too-large files,
            # or insufficient GitHub App permissions.
            logger.warning(
                "Skipping %s file(s) with no POST-PR content at head_sha=%s: %s",
                len(missing_post_pr_files),
                head_sha,
                missing_post_pr_files,
            )

        # Only review files we can actually fetch at the PR head.
        files_changed = [fp for fp in files_changed_all if fp in pr_files]
        if not files_changed:
            logger.warning("No reviewable files (all missing POST-PR content) — skipping review")
            return

        # Parse ASTs
        parsed_files: List[ParsedFile] = [
            _ast_parse_file_full(fp, pr_files[fp]) for fp in files_changed
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

            seen_samples: set[tuple[str, str]] = set()

            for name in all_names:
                query_result = query_service.search_code(name, repo_id=repo_id)
                if not query_result:
                    continue

                # Only take top 3 most relevant results per symbol
                for each_result in query_result[:3]:
                    result_path = each_result.get("path", pf.path)
                    result_type = each_result.get("type", "")
                    source_code = each_result.get("source_code", "")
                    docstring = each_result.get("docstring", "")

                    sample_key = (name, result_path)
                    if sample_key in seen_samples:
                        continue
                    seen_samples.add(sample_key)

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
        # Phase 1: Build file contexts, then run all per-file agents in parallel
        # ─────────────────────────────────────────────────────────────────────────────

        # Step 1: Build PR symbol map for cross-file context
        pr_symbol_map = build_pr_symbol_map(parsed_files)

        # Step 2: Build markdown context for each changed file (sequential, fast)
        file_review_tasks: list[dict] = []

        for file_path in files_changed:
            file_content = pr_files.get(file_path, "")
            file_ast = next((pf for pf in parsed_files if pf.path == file_path), None)
            file_diff = build_file_diff_from_patch(file_path, file_diffs.get(file_path, ""))
            safe_filename = file_path.replace("/", "_").replace(".", "_")

            # Get Neo4j code samples for this file
            file_code_samples = code_samples_by_file.get(
                file_path, {"classes": [], "functions": [], "imports": []}
            )

            # Get previous issues for this file (for incremental review)
            file_previous_issues = previous_issues_by_file.get(file_path, [])
            prev_issues_str = format_previous_issues(file_previous_issues)

            # Build the full markdown prompt that the agent will receive
            file_context = build_file_context(
                file_path=file_path,
                full_diff=file_diff,
                full_file_content=file_content,
                file_ast=file_ast,
                previous_issues=prev_issues_str,
                explorer_context="",
                code_samples=file_code_samples,
                review_type=review_type,
                pr_symbol_map=pr_symbol_map,
            )

            # Save the prompt to debug output
            if review_dir:
                write_step(
                    review_dir,
                    f"04_review_prompt_{safe_filename}.md",
                    f"# Review Agent Prompt: {file_path}\n\n{file_context}",
                )

            file_review_tasks.append(
                {
                    "file_path": file_path,
                    "file_context": file_context,
                    "file_content": file_content,
                    "previous_issues": file_previous_issues,
                    "safe_filename": safe_filename,
                }
            )

        # Step 2: Run all file agents AND lint at the same time (parallel)
        # Lint doesn't depend on agent results, so run it alongside them.
        # asyncio.gather launches them all and waits for all to finish.
        logger.info(f"Launching {len(file_review_tasks)} file agents + lint in parallel...")

        async def _run_single_file_review(task: dict) -> dict:
            """Run the 3-node agent on one file and tag the result with its path."""
            result = await execute_review_agent(
                file_path=task["file_path"],
                file_context=task["file_context"],
                query_service=query_service,
                repo_id=repo_id,
                model=config.review_model,
                file_content=task.get("file_content", ""),
                previous_issues=task.get("previous_issues"),
                review_dir=review_dir,
                safe_filename=task["safe_filename"],
            )
            result["_file_path"] = task["file_path"]
            return result

        # Launch all agents + lint simultaneously and wait for all to complete
        agent_results = await asyncio.gather(
            *[_run_single_file_review(task) for task in file_review_tasks],
            run_lint(pr_files),
            return_exceptions=True,
        )

        # Split results: last item is lint, rest are file agents
        lint_raw_result = agent_results[-1]
        file_agent_results = agent_results[:-1]

        # Handle lint result
        if isinstance(lint_raw_result, Exception):
            logger.warning(f"Lint failed: {lint_raw_result}")
            lint_issues: list = []
            lint_raw_count = 0
        else:
            all_lint_results = lint_raw_result
            lint_raw_count = len(all_lint_results)
            lint_issues = [issue for issue in all_lint_results if issue.file in pr_files]

        # Step 3: Collect results from all agents
        all_issues: list[dict] = []
        all_positive_findings: list[dict] = []
        all_walkthroughs: list[dict] = []
        validated_fixed_issues: list[dict] = []
        total_tool_rounds = 0
        failed_file_paths: list[str] = []
        raw_agent_outputs: dict[str, str] = {}

        for result in file_agent_results:
            # Handle crashed agents (network errors, LLM failures, etc.)
            if isinstance(result, Exception):
                logger.error(f"Agent crashed: {result}")
                failed_file_paths.append("unknown")
                continue

            # Handle agents that returned an error dict
            if "error" in result:
                file_path = result.get("_file_path", "unknown")
                logger.error(f"Agent failed for {file_path}: {result['error']}")
                failed_file_paths.append(file_path)
                continue

            # Aggregate successful results
            file_path = result.get("_file_path", "unknown")
            for file_issue in result.get("file_based_issues", []):
                all_issues.append(file_issue)
            for finding in result.get("file_based_positive_findings", []):
                all_positive_findings.append(finding)
            for walkthrough in result.get("file_based_walkthrough", []):
                all_walkthroughs.append(walkthrough)

            # Build set of validated issue keys for deduplication
            validated_keys = set()
            for v in result.get("validated_previous_issues", []):
                key = (v.get("file"), v.get("title"))
                validated_keys.add(key)

            # Merge validated previous issues that are still_open or partially_fixed
            for validated in result.get("validated_previous_issues", []):
                if validated.get("status") in ("still_open", "partially_fixed"):
                    all_issues.append(
                        {
                            "file": validated.get("file"),
                            "issues": [
                                {
                                    "issue_type": "Bug",
                                    "category": validated.get("category", "bug"),
                                    "severity": validated.get("severity", "medium"),
                                    "title": validated.get("title"),
                                    "file": validated.get("file"),
                                    "line_start": validated.get("line_start"),
                                    "line_end": validated.get("line_end"),
                                    "status": validated.get("status"),
                                    "description": validated.get("description", ""),
                                    "suggestion": validated.get("suggestion", ""),
                                    "impact": validated.get("impact", ""),
                                    "code_snippet": validated.get("code_snippet", ""),
                                    "confidence": validated.get("confidence", 8),
                                }
                            ],
                        }
                    )
                    logger.info(
                        f"Re-adding {validated.get('status')} issue: "
                        f"{validated.get('title')} in {validated.get('file')}"
                    )

            # Collect fixed issues for GitHub comment (not all_issues, only for display)
            for validated in result.get("validated_previous_issues", []):
                if validated.get("status") == "fixed":
                    validated_fixed_issues.append(
                        {
                            "issue_type": "Bug",
                            "category": validated.get("category", "bug"),
                            "severity": validated.get("severity", "medium"),
                            "title": validated.get("title"),
                            "file": validated.get("file"),
                            "line_start": validated.get("line_start"),
                            "line_end": validated.get("line_end"),
                            "status": "fixed",
                            "description": validated.get("description", ""),
                            "suggestion": validated.get("suggestion", ""),
                            "impact": validated.get("impact", ""),
                            "code_snippet": validated.get("code_snippet", ""),
                            "confidence": validated.get("confidence", 10),
                        }
                    )

            total_tool_rounds += result.get("tool_rounds", 0)

            # Capture raw agent output for debug section
            if result.get("raw_output_json"):
                raw_agent_outputs[file_path] = result["raw_output_json"]

        if failed_file_paths:
            logger.warning(
                f"{len(failed_file_paths)}/{len(file_review_tasks)} files failed: "
                f"{failed_file_paths}"
            )

        # Collect all validated previous issues for Firebase update
        all_validated_issues: list[dict] = []
        for result in file_agent_results:
            if isinstance(result, Exception) or "error" in result:
                continue
            all_validated_issues.extend(result.get("validated_previous_issues", []))

        # Update previous run in Firebase to mark fixed issues
        if project_owner and all_validated_issues:
            fixed_count = sum(1 for v in all_validated_issues if v.get("status") == "fixed")
            if fixed_count > 0:
                try:
                    firebase_service.update_previous_run_issues(
                        project_owner,
                        owner,
                        repo,
                        pr_number,
                        all_validated_issues,
                    )
                    logger.info(f"Marked {fixed_count} issues as fixed in previous run")
                except Exception as e:
                    logger.warning(f"Failed to update previous run issues: {e}")

        logger.info(
            f"Review complete: {len(all_issues)} issues, "
            f"{len(all_positive_findings)} positives, "
            f"{total_tool_rounds} tool rounds"
        )

        # Build set of FIXED issue keys for deduplication
        # Only fixed issues should be skipped (not still_open which need to be shown)
        fixed_issue_keys = set()
        for validated in all_validated_issues:
            if validated.get("status") == "fixed":
                key = (validated.get("file"), validated.get("title"))
                fixed_issue_keys.add(key)

        # Convert to Issue format, skipping duplicates of FIXED issues
        # still_open issues are NOT skipped - they need to be shown as actionable
        all_issues_formatted = []
        for file_issue in all_issues:
            for issue in file_issue.get("issues", []):
                issue_key = (issue.get("file"), issue.get("title"))
                if issue_key in fixed_issue_keys:
                    logger.info(
                        f"Skipping duplicate issue: {issue.get('title')} in {issue.get('file')}"
                    )
                    continue
                all_issues_formatted.append(Issue(**issue))

        # Add fixed issues from validator (for display in GitHub comment)
        for fixed_issue in validated_fixed_issues:
            all_issues_formatted.append(Issue(**fixed_issue))

        # Build walkthrough - now single sentence per file
        walk_through = []
        for walk in all_walkthroughs:
            summary = walk.get("summary", "")
            file_path = walk.get("file", "")
            if summary:
                walk_through.append(f"{file_path} — {summary}")

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
        nitpick_issues = [
            i
            for i in all_issues_formatted
            if i.status in ("new", "still_open") and i.confidence < 7
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

        # Post regular comments (high-confidence out-of-diff + all nitpicks)
        regular_comments_posted = 0
        for issue in regular_comment_issues + nitpick_issues:
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
            "lint_raw_count": lint_raw_count,
            "lint_on_diff_count": len(lint_issues),
            "files_reviewed": len(files_changed),
            "head_sha": head_sha,
        }

        review_body = format_review_summary(
            reconciled,
            context_data,
            pr_number,
            lint_issues=lint_issues,
            walk_through=walk_through,
            inline_posted=inline_posted,
            inline_skipped=inline_skipped,
            raw_agent_outputs=raw_agent_outputs,
            debug_info=debug_info,
        )

        try:
            await gh.post_pr_review(owner, repo, pr_number, head_sha, review_body, review_event)
            logger.info(f"Posted review ({review_event}) on {owner}/{repo}#{pr_number}")

            # Update PR description with summary
            if config.enable_pr_description_update:
                try:
                    pr_description = format_pr_description(reconciled, walk_through)
                    await gh.update_pr_body(owner, repo, pr_number, pr_description)
                    logger.info(f"Updated PR description for {owner}/{repo}#{pr_number}")
                except Exception as e:
                    logger.warning(f"Failed to update PR description: {e}")
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
