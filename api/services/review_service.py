import asyncio
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
from code_review_agent.models.agent_schemas import ContextData, Issue, ReconciledReview
from common.debug_writer import make_review_dir, write_step
from common.diff_line_mapper import (
    get_valid_comment_lines,
    validate_issue_line,
)
from common.firebase_models import PRMetadata, PrReviewStatus, ReviewRunData
from common.github_client import get_github_client
from db.client import Neo4jClient, get_neo4j_client

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
    uid = firebase_service.find_project_owner_id(owner)
    repo_id = f"{owner}/{repo}"
    if neo4j is None:
        neo4j = get_neo4j_client()

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

        # Write raw AST for each file
        for pf in parsed_files:
            ast_content = [
                f"# AST for {pf.path}",
                f"# Language: {pf.language}",
                "",
                f"## Functions ({len(pf.functions)})",
            ]
            for fn in pf.functions:
                complexity = getattr(fn, "cyclomatic_complexity", "N/A")
                ast_content.append(
                    f"- {fn.name}() at line {fn.line_number} (complexity: {complexity})"
                )
                if fn.docstring:
                    ast_content.append(f"  Docstring: {fn.docstring[:100]}...")
                if fn.source:
                    ast_content.append("  ```python")
                    for line in fn.source.split("\n")[:50]:
                        ast_content.append(f"  {line}")
                    if len(fn.source.split("\n")) > 50:
                        ast_content.append("  # ... (truncated)")
                    ast_content.append("  ```")

            ast_content.append(f"\n## Classes ({len(pf.classes)})")
            for cls in pf.classes:
                bases = ", ".join(cls.bases) if cls.bases else "none"
                ast_content.append(f"- {cls.name} at line {cls.line_number} (bases: {bases})")
                if cls.docstring:
                    ast_content.append(f"  Docstring: {cls.docstring[:100]}...")
                if cls.source:
                    ast_content.append("  ```python")
                    for line in cls.source.split("\n")[:50]:
                        ast_content.append(f"  {line}")
                    if len(cls.source.split("\n")) > 50:
                        ast_content.append("  # ... (truncated)")
                    ast_content.append("  ```")

            ast_content.append(f"\n## Imports ({len(pf.imports)})")
            for imp in pf.imports:
                alias = f" as {imp.alias}" if imp.alias else ""
                ast_content.append(f"- {imp.name}{alias} at line {imp.line_number}")
                ast_content.append(f"  full_import_name: {imp.full_import_name}")

            ast_content.append(f"\n## Call Sites ({len(pf.call_sites)})")
            for call in pf.call_sites[:20]:
                ctx = call.context if call.context else "function"
                ast_content.append(
                    f"- {call.full_name}() at line {call.line_number} "
                    f"(in {call.class_context}.{ctx})"
                )

            write_step(
                review_dir,
                f"02_ast_{pf.path.replace('/', '_').replace('.', '_')}.md",
                "\n".join(ast_content),
            )

        class_definations = []
        function_definations = []

        # Extract symbols DEFINED IN changed files directly from parsed AST
        for pf in parsed_files:
            file_path = pf.path
            for fn in pf.functions:
                if fn.source:
                    function_definations.append(
                        {
                            "name": fn.name,
                            "source": fn.source,
                            "source_code": fn.source,
                            "docstring": fn.docstring or "",
                            "path": file_path,
                            "line_number": fn.line_number,
                            "type": "function",
                        }
                    )
            for cls in pf.classes:
                if cls.source:
                    class_definations.append(
                        {
                            "name": cls.name,
                            "source": cls.source,
                            "source_code": cls.source,
                            "docstring": cls.docstring or "",
                            "path": file_path,
                            "line_number": cls.line_number,
                            "type": "class",
                        }
                    )

        # Build map of imported symbols -> module file path
        import_module_paths: dict[str, str] = {}  # name -> file_path
        import_full_names: dict[str, str] = {}  # name -> full_import_name
        for pf in parsed_files:
            for imp in pf.imports:
                if imp.name:
                    import_full_names[imp.name] = imp.full_import_name
                    # Convert module path to file path
                    # e.g., app.services.finance_service -> app/services/finance_service.py
                    module_parts = imp.full_import_name.rsplit(".", 1)
                    if len(module_parts) == 2:
                        module_path = module_parts[0]
                        file_path = module_path.replace(".", "/") + ".py"
                        import_module_paths[imp.name] = file_path

        defined_names = {f.get("name") for f in function_definations}
        defined_names |= {c.get("name") for c in class_definations}

        # Unique import modules to fetch
        unique_import_paths = set(import_module_paths.values())
        paths_to_fetch = [p for p in unique_import_paths if p not in pr_files]

        # Fetch import module sources from GitHub
        if paths_to_fetch:
            logger.info(f"Fetching {len(paths_to_fetch)} import module files from GitHub")
            fetched_results = await asyncio.gather(
                *[gh.get_file_content(owner, repo, p, ref=head_sha) for p in paths_to_fetch],
                return_exceptions=True,
            )
            fetched_import_sources: dict[str, str] = {}
            for path, result in zip(paths_to_fetch, fetched_results):
                if not isinstance(result, Exception) and result:
                    fetched_import_sources[path] = result
                    logger.info(f"Fetched import module: {path}")
        else:
            fetched_import_sources = {}

        # Parse imported modules and extract symbols
        for imp_name, file_path in import_module_paths.items():
            if imp_name in defined_names:
                continue

            # Get source from pr_files (changed in PR) or fetched sources
            if file_path in pr_files:
                source = pr_files[file_path]
            elif file_path in fetched_import_sources:
                source = fetched_import_sources[file_path]
            else:
                logger.info(f"Import '{imp_name}' source not available: {file_path}")
                continue

            # Parse the import module
            parsed_import = _ast_parse_file_full(file_path, source)

            # Find the imported symbol in the parsed module
            for fn in parsed_import.functions:
                if fn.name == imp_name and fn.source:
                    function_definations.append(
                        {
                            "name": fn.name,
                            "source": fn.source,
                            "source_code": fn.source,
                            "docstring": fn.docstring or "",
                            "path": file_path,
                            "line_number": fn.line_number,
                            "type": "function",
                        }
                    )
                    defined_names.add(imp_name)
                    logger.info(f"Found imported function '{imp_name}' in {file_path}")
                    break

            if imp_name in defined_names:
                continue

            for cls in parsed_import.classes:
                if cls.name == imp_name and cls.source:
                    class_definations.append(
                        {
                            "name": cls.name,
                            "source": cls.source,
                            "source_code": cls.source,
                            "docstring": cls.docstring or "",
                            "path": file_path,
                            "line_number": cls.line_number,
                            "type": "class",
                        }
                    )
                    defined_names.add(imp_name)
                    logger.info(f"Found imported class '{imp_name}' in {file_path}")
                    break

        logger.info(
            f"Collected {len(function_definations)} function defs, "
            f"{len(class_definations)} class defs"
        )

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
            review_dir=review_dir,
            class_definations=class_definations,
            function_definations=function_definations,
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
            raw_agent_outputs=aggregated.raw_agent_outputs,
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
