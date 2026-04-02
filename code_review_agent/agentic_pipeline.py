"""Main agentic review pipeline."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from api.models.ast_results import ParsedFile
from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.review_graph import build_review_explorer
from code_review_agent.agent.review_prompt import REVIEW_EXPLORER_PROMPT
from code_review_agent.config import config, token_limits
from code_review_agent.models.file_review import (
    AggregatedReviewResult,
    FileReviewLLMOutput,
    FileReviewResult,
)
from common.debug_writer import write_step
from common.diff_line_mapper import (
    format_file_with_line_numbers,
    get_hunk_ranges,
    get_valid_comment_lines,
)
from common.languages import EXT_TO_LANG
from db.client import Neo4jClient, get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


def _get_file_language(file_path: str) -> str:
    """Get the language identifier for syntax highlighting."""
    ext = Path(file_path).suffix.lower()
    return EXT_TO_LANG.get(ext, "text")


def _build_file_diff_from_patch(file_path: str, patch: str) -> str:
    """Build a unified diff string from a patch."""
    if not patch:
        return f"(No diff for {file_path})"
    header = f"diff --git a/{file_path} b/{file_path}\n--- a/{file_path}\n+++ b/{file_path}\n"
    return header + patch


def _build_hunk_ranges_section(diff_text: str) -> str:
    """Build a section describing which line ranges are part of the diff hunks."""
    hunk_ranges = get_hunk_ranges(diff_text)
    valid_lines = get_valid_comment_lines(diff_text)

    if not hunk_ranges:
        return ""

    lines = ["## Diff Hunk Line Ranges", ""]
    lines.append("Line ranges in the POST-PR file that are part of the diff:")

    for file_path, ranges in sorted(hunk_ranges.items()):
        range_parts = [str(s) if s == e else f"{s}-{e}" for s, e in ranges]
        lines.append(f"- `{file_path}` hunk ranges: {', '.join(range_parts)}")

        # Also show valid comment lines for this file
        file_valid = valid_lines.get(file_path, set())
        if file_valid:
            sorted_valid = sorted(file_valid)
            lines.append(f"  Valid comment lines: {sorted_valid}")

    lines.append("")
    lines.append("**Within hunk ranges → inline comment. Outside hunk ranges → regular comment.**")

    return "\n".join(lines)


def _build_ast_summary_for_file(file_path: str, content: str, ast: Any) -> str:
    """Build a text summary of the AST for a file."""
    if not ast or ast.error:
        return f"Could not parse AST for {file_path}: {ast.error if ast else 'Unknown error'}"

    lines = [f"# {file_path}"]

    if ast.functions:
        lines.append(f"\n## Functions ({len(ast.functions)})")
        for f in ast.functions[:20]:
            complexity = f.cyclomatic_complexity if hasattr(f, "cyclomatic_complexity") else 1
            risk = "HIGH" if complexity > 10 else "MEDIUM" if complexity > 5 else "LOW"
            lines.append(
                f"- `{f.name}()` at line {f.line_number} (complexity: {complexity}, risk: {risk})"
            )
            if hasattr(f, "docstring") and f.docstring:
                lines.append(f"  Doc: {f.docstring[:100]}")

    if ast.classes:
        lines.append(f"\n## Classes ({len(ast.classes)})")
        for c in ast.classes[:10]:
            lines.append(f"- `{c.name}` at line {c.line_number}")
            if c.bases:
                lines.append(f"  Inherits: {', '.join(c.bases)}")

    if ast.imports:
        lines.append(f"\n## Imports ({len(ast.imports)})")
        for imp in ast.imports[:10]:
            alias_note = f" as {imp.alias}" if imp.alias else ""
            lines.append(f"- `{imp.name}`{alias_note} at line {imp.line_number}")

    if ast.call_sites:
        lines.append(f"\n## Call Sites ({len(ast.call_sites)})")
        for call in ast.call_sites[:15]:
            ctx = call.context if call.context else "function"
            lines.append(
                f"- `{call.full_name}()` at line {call.line_number} (in {call.class_context}.{ctx})"
            )

    return "\n".join(lines[:100])


def _format_previous_issues(previous_issues: List[dict]) -> str:
    """Format previous issues for the prompt."""
    if not previous_issues:
        return "No previous issues for this file."

    lines = []
    for i, issue in enumerate(previous_issues, 1):
        title = issue.get("title", "Unknown issue")
        file = issue.get("file", "")
        line = issue.get("line_start", "?")
        category = issue.get("category", "?")
        desc = issue.get("description", "")[:200]
        status = issue.get("status", "new")

        lines.append(f"### Previous Issue #{i} (status: {status})")
        lines.append(f"**Title:** {title}")
        lines.append(f"**Location:** `{file}:{line}`")
        lines.append(f"**Category:** {category}")
        if desc:
            lines.append(f"**Description:** {desc}")
        lines.append("")

    return "\n".join(lines)


def _render_definitions_section(
    code_samples: Optional[Dict[str, List[dict]]] = None,
) -> str:
    """Render code samples (classes, functions, imports) as markdown sections."""
    if not code_samples:
        return ""

    parts = []
    for sample_type, samples in code_samples.items():
        if not samples:
            continue

        type_label = sample_type.capitalize()
        names_list = ", ".join(f"`{s.get('name', 'Unknown')}`" for s in samples)
        parts.append(f"## {type_label} Definitions: {names_list}")

        for sample in samples:
            name = sample.get("name", "Unknown")
            source = sample.get("source_code") or ""
            docstring = sample.get("docstring") or ""
            path = sample.get("path", "")
            parts.append(f"### `{name}` ({path})")
            if docstring:
                parts.append(f"Docstring: {docstring}")
            if source:
                parts.append(f"```python\n{source}\n```")
            parts.append("")
        parts.append("")

    return "\n".join(parts)


def _build_file_context(
    file_path: str,
    full_diff: str,
    full_file_content: str,
    file_ast: Any,
    previous_issues: str,
    explorer_context: str,
    code_samples: Optional[Dict[str, List[dict]]] = None,
) -> str:
    """Build the full file context for the review prompt."""
    ast_summary = _build_ast_summary_for_file(file_path, full_file_content, file_ast)

    parts = [
        f"## File Under Review: {file_path}",
        "",
        "## Raw Unified Diff",
        "```diff",
        full_diff or "(no diff available)",
        "```",
        "",
        "## POST-PR File Content (with line numbers)",
        "```",
        format_file_with_line_numbers(full_file_content) or "(no file content available)",
        "```",
        "",
        _build_hunk_ranges_section(full_diff),
        "",
        "## AST Summary",
        ast_summary or "(No AST)",
        "",
        "## Previous Issues",
        previous_issues or "No previous issues for this file",
        "",
        "## Explorer Context (Neo4j Graph Intelligence)",
        explorer_context or "No external context available",
        "Context from code search and graph queries about this file and related entities",
    ]

    definitions_section = _render_definitions_section(code_samples)
    if definitions_section:
        parts.append("")
        parts.append(definitions_section)

    return "\n".join(parts)


def _build_explorer_prompt(
    repo_id: str,
    pr_number: int,
    file_path: str,
    full_diff: str,
    ast_summary: str,
    file_content: str,
    code_samples: Optional[Dict[str, List[dict]]] = None,
) -> str:
    """Build prompt for Explorer agent."""
    parts = [
        f"## PR #{pr_number} in {repo_id}",
        f"**File:** {file_path}",
        "",
        "## Diff",
        "```diff",
        full_diff,
        "```",
        "",
        "## AST Summary",
        ast_summary or "(No AST)",
        "",
        "## File Content",
        f"```{_get_file_language(file_path)}",
        file_content,
        "```",
        "",
    ]

    definitions_section = _render_definitions_section(code_samples)
    if definitions_section:
        parts.append("")
        parts.append(definitions_section)

    parts.extend(
        [
            "## Task",
            "1. Investigate changed functions/classes using Neo4j tools",
            "2. Find callers and dependencies",
            "3. Check for bugs, security issues, performance problems",
            "4. Report findings for the Review Agent",
        ]
    )
    return "\n".join(parts)


async def execute_agentic_review(
    owner: str,
    repo: str,
    pr_number: int,
    diff_text: str,
    pr_info: Dict[str, str],
    neo4j: Optional[Neo4jClient] = None,
    pr_files: Optional[Dict[str, str]] = None,
    parsed_files: Optional[List[ParsedFile]] = None,
    file_diffs: Optional[Dict[str, str]] = None,
    previous_issues_by_file: Optional[Dict[str, List[dict]]] = None,
    review_dir: Optional[Path] = None,
    code_samples_by_file: Optional[Dict[str, Dict[str, List[dict]]]] = None,
) -> AggregatedReviewResult:
    """Execute agentic per-file code review.

    For each file:
    1. Build prompt with: diff, file code, AST
    2. If enable_explorer: run Explorer agent for Neo4j context
    3. Run Review agent
    4. Aggregate results
    """
    logger.info("Starting agentic review for %s/%s#%s", owner, repo, pr_number)

    repo_id = f"{owner}/{repo}"

    if neo4j is None:
        neo4j = get_neo4j_client()
    query_service = CodeSearchService(neo4j)

    if not diff_text:
        logger.warning("Empty diff — skipping review")
        return AggregatedReviewResult(summary="No changes in PR")

    files_changed = list(pr_files.keys()) if pr_files else []
    if not files_changed:
        logger.warning("No files to review")
        return AggregatedReviewResult(summary="No files changed")

    asts_by_file = {pf.path: pf for pf in parsed_files} if parsed_files else {}
    pr_patches = file_diffs or {}

    valid_results: List[FileReviewResult] = []

    for file_path in files_changed:
        content = pr_files.get(file_path, "")
        ast = asts_by_file.get(file_path)
        full_diff = _build_file_diff_from_patch(file_path, pr_patches.get(file_path, ""))
        safe_filename = file_path.replace("/", "_").replace(".", "_")

        file_code_samples = (
            code_samples_by_file.get(file_path, {"classes": [], "functions": [], "imports": []})
            if code_samples_by_file
            else {"classes": [], "functions": [], "imports": []}
        )

        file_previous_issues = (
            previous_issues_by_file.get(file_path, []) if previous_issues_by_file else []
        )
        formatted_prev_issues = _format_previous_issues(file_previous_issues)

        if config.enable_explorer:
            result = await _review_file_with_explorer(
                file_path=file_path,
                content=content,
                ast=ast,
                full_diff=full_diff,
                safe_filename=safe_filename,
                repo_id=repo_id,
                pr_number=pr_number,
                query_service=query_service,
                review_dir=review_dir,
                formatted_prev_issues=formatted_prev_issues,
                file_previous_issues=file_previous_issues,
                file_code_samples=file_code_samples,
            )
        else:
            result = await _review_file_without_explorer(
                file_path=file_path,
                content=content,
                ast=ast,
                full_diff=full_diff,
                safe_filename=safe_filename,
                repo_id=repo_id,
                query_service=query_service,
                review_dir=review_dir,
                formatted_prev_issues=formatted_prev_issues,
                file_previous_issues=file_previous_issues,
                file_code_samples=file_code_samples,
            )

        if result:
            valid_results.append(result)

    logger.info("File reviews complete: %d successful", len(valid_results))

    aggregated = aggregate_file_reviews(valid_results, lint_issues=[])

    write_step(
        review_dir,
        "05_aggregated.md",
        f"# Aggregated Results\n\n## Summary\n{aggregated.summary or 'No summary'}\n\n"
        f"## Issues ({len(aggregated.issues)})\n"
        + "\n".join(
            f"- {i.get('title', 'Untitled')} ({i.get('file', '?')}:{i.get('line_start', '?')})"
            for i in aggregated.issues
        )
        + f"\n\n## Positive Findings ({len(aggregated.positive_findings)})\n"
        + "\n".join(f"- {pf}" for pf in aggregated.positive_findings),
    )

    logger.info(
        "Aggregated: %d issues (%d new, %d still_open, %d fixed)",
        len(aggregated.issues),
        len([i for i in aggregated.issues if i.get("status") == "new"]),
        len([i for i in aggregated.issues if i.get("status") == "still_open"]),
        len(aggregated.previous_fixed),
    )

    return aggregated


async def _review_file_with_explorer(
    file_path: str,
    content: str,
    ast: Any,
    full_diff: str,
    safe_filename: str,
    repo_id: str,
    pr_number: int,
    query_service: CodeSearchService,
    review_dir: Path | None,
    formatted_prev_issues: str,
    file_previous_issues: List[dict],
    file_code_samples: Dict[str, List[dict]],
) -> FileReviewResult | None:
    """Review a single file with the Explorer agent enabled."""
    logger.info("Running Explorer for %s...", file_path)

    explorer_prompt = _build_explorer_prompt(
        repo_id=repo_id,
        pr_number=pr_number,
        file_path=file_path,
        full_diff=full_diff,
        ast_summary=_build_ast_summary_for_file(file_path, content, ast) if ast else "",
        file_content=content,
        code_samples=file_code_samples,
    )

    if review_dir:
        write_step(
            review_dir,
            f"03_explorer_prompt_{safe_filename}.md",
            f"# Explorer Prompt: {file_path}\n\n{explorer_prompt}",
        )

    explored_context = ""
    tool_rounds = 0

    try:
        explorer = build_review_explorer(
            query_service=query_service,
            system_prompt=REVIEW_EXPLORER_PROMPT,
            model=config.review_model,
            repo_id=repo_id,
            explorer_goals=f"Investigate changed file: {file_path}",
        )
        explorer_result = await explorer.ainvoke(
            {"messages": [HumanMessage(content=explorer_prompt)], "tool_rounds": 0}
        )
        explored_messages = list(explorer_result.get("messages", []))
        tool_rounds = explorer_result.get("tool_rounds", 0)
        for msg in explored_messages:
            if hasattr(msg, "content") and msg.content:
                explored_context += f"\n\n{msg.content}"
        if len(explored_context) > token_limits.explorer_context_max_chars:
            explored_context = (
                explored_context[: token_limits.explorer_context_max_chars] + "\n... (truncated)"
            )
    except Exception:
        logger.exception("Explorer failed for %s", file_path)

    if review_dir:
        write_step(
            review_dir,
            f"04_explorer_output_{safe_filename}.md",
            f"# Explorer Output: {file_path}\n**Tool rounds:** {tool_rounds}\n\n{explored_context}",
        )

    return await _run_review_agent(
        file_path=file_path,
        content=content,
        ast=ast,
        full_diff=full_diff,
        safe_filename=safe_filename,
        repo_id=repo_id,
        query_service=query_service,
        review_dir=review_dir,
        formatted_prev_issues=formatted_prev_issues,
        file_previous_issues=file_previous_issues,
        explored_context=explored_context,
        file_code_samples=file_code_samples,
    )


async def _review_file_without_explorer(
    file_path: str,
    content: str,
    ast: Any,
    full_diff: str,
    safe_filename: str,
    repo_id: str,
    query_service: CodeSearchService,
    review_dir: Path | None,
    formatted_prev_issues: str,
    file_previous_issues: List[dict],
    file_code_samples: Dict[str, List[dict]],
) -> FileReviewResult | None:
    """Review a single file without the Explorer agent."""
    logger.info("Reviewing %s without explorer", file_path)

    return await _run_review_agent(
        file_path=file_path,
        content=content,
        ast=ast,
        full_diff=full_diff,
        safe_filename=safe_filename,
        repo_id=repo_id,
        query_service=query_service,
        review_dir=review_dir,
        formatted_prev_issues=formatted_prev_issues,
        file_previous_issues=file_previous_issues,
        explored_context="",
        file_code_samples=file_code_samples,
    )


async def _run_review_agent(
    file_path: str,
    content: str,
    ast: Any,
    full_diff: str,
    safe_filename: str,
    repo_id: str,
    query_service: CodeSearchService,
    review_dir: Path | None,
    formatted_prev_issues: str,
    file_previous_issues: List[dict],
    explored_context: str,
    file_code_samples: Dict[str, List[dict]],
) -> FileReviewResult | None:
    """Common review logic used by both explorer and non-explorer paths."""
    try:
        agent = build_review_graph(query_service=query_service, repo_id=repo_id)

        file_context = _build_file_context(
            file_path=file_path,
            full_diff=full_diff,
            full_file_content=content,
            file_ast=ast,
            previous_issues=formatted_prev_issues,
            explorer_context=explored_context,
            code_samples=file_code_samples,
        )

        if review_dir:
            write_step(
                review_dir,
                f"04_review_prompt_{safe_filename}.md",
                f"# Review Agent Prompt: {file_path}\n\n{file_context}",
            )

        result = await agent.ainvoke({"messages": [HumanMessage(content=file_context)]})

        structured_response = result.get("structured_response")
        if structured_response is None:
            logger.warning("No structured_response, returning empty result")
            structured_response = FileReviewLLMOutput(
                walk_through=f"{file_path} — Review could not be completed",
                issues=[],
                positive_findings=[],
            )

        response: FileReviewLLMOutput = structured_response
        raw_walk = response.walk_through
        if isinstance(raw_walk, list):
            walk_through = "; ".join(str(w) for w in raw_walk) if raw_walk else ""
        else:
            walk_through = raw_walk or ""
        issues = [issue.model_dump() for issue in response.issues]
        raw_findings = response.positive_findings or []
        positive_findings: list[str] = []
        for pf in raw_findings:
            if isinstance(pf, list):
                positive_findings.extend(str(item) for item in pf)
            elif isinstance(pf, str):
                positive_findings.append(pf)
            else:
                positive_findings.append(str(pf))

        previous_status = {}
        for prev in file_previous_issues:
            issue_id = (
                f"{prev.get('file', '')}:{prev.get('line_start', '')}:{prev.get('title', '')}"
            )
            matched = False
            for issue in issues:
                if (
                    issue.get("file") == prev.get("file")
                    and issue.get("line_start") == prev.get("line_start")
                    and issue.get("title") == prev.get("title")
                ):
                    previous_status[issue_id] = issue.get("status", "still_open")
                    matched = True
                    break
            if not matched:
                previous_status[issue_id] = "fixed"

        return FileReviewResult(
            file_path=file_path,
            issues=issues,
            walk_through_entry=(
                f"{file_path} — {walk_through}" if walk_through else f"{file_path} — Modified"
            ),
            positive_findings=positive_findings,
            previous_issues_status=previous_status,
            raw_agent_output=(
                response.model_dump_json(indent=2) if hasattr(response, "model_dump_json") else ""
            ),
        )

    except Exception as e:
        logger.error("File review failed for %s: %s", file_path, e)
        return None


def aggregate_file_reviews(
    file_results: List[FileReviewResult],
    lint_issues: List[Any] | None = None,
) -> AggregatedReviewResult:
    """Aggregate results from all file reviews."""
    all_issues = []
    all_walk_through = []
    all_positive_findings = []
    previous_fixed = []

    for result in file_results:
        if result.error:
            continue

        all_walk_through.append(result.walk_through_entry)
        all_positive_findings.extend(result.positive_findings)

        for issue in result.issues:
            status = issue.get("status", "new")

            if status == "fixed":
                previous_fixed.append(issue)
            elif status == "still_open":
                all_issues.append(issue)
            else:
                all_issues.append(issue)

        for issue_id, status in result.previous_issues_status.items():
            if status == "fixed":
                matching = [
                    i
                    for i in result.issues
                    if f"{i.get('file')}:{i.get('line_start')}:{i.get('title')}" == issue_id
                ]
                if not matching:
                    previous_fixed.append(
                        {
                            "title": issue_id.split(":")[-1] if ":" in issue_id else issue_id,
                            "status": "fixed",
                        }
                    )

    all_issues.sort(key=lambda x: x.get("confidence", 5), reverse=True)

    total_files = len([r for r in file_results if not r.error])
    total_issues = len(all_issues)
    new_issues = len([i for i in all_issues if i.get("status") == "new"])
    still_open_issues = len([i for i in all_issues if i.get("status") == "still_open"])

    # Collect raw agent outputs per file
    raw_agent_outputs = {}
    for result in file_results:
        if result.raw_agent_output:
            raw_agent_outputs[result.file_path] = result.raw_agent_output

    summary = (
        f"Reviewed {total_files} files. Found {total_issues} issues "
        f"({new_issues} new, {still_open_issues} still open, {len(previous_fixed)} fixed)."
    )

    return AggregatedReviewResult(
        summary=summary,
        issues=all_issues,
        walk_through=all_walk_through,
        positive_findings=all_positive_findings,
        total_files_reviewed=total_files,
        total_issues=total_issues,
        new_issues=new_issues,
        still_open_issues=still_open_issues,
        previous_fixed=previous_fixed,
        total_tool_rounds=0,
        raw_agent_outputs=raw_agent_outputs,
    )
