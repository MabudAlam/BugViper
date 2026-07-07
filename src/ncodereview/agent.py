"""Agent creation and prompt building utilities."""

from __future__ import annotations

from langchain_e2b import E2BSandbox

from ncodereview.schemas import FinalReviewOutput
from ncodereview.subagents import build_subagents


def create_deep_agent(
    model, sbx, review_type: str, run_limit: int = 20, use_generalist: bool = False
):
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend

    e2b_backend = E2BSandbox(sandbox=sbx)
    backend = CompositeBackend(
        default=e2b_backend,
        routes={},
        artifacts_root="/home/user/artifacts",
    )
    return create_deep_agent(
        model=model,
        backend=backend,
        tools=[],
        subagents=build_subagents(
            model=model,
            backend=backend,
            run_limit=run_limit,
            use_generalist=use_generalist,
        ),
        system_prompt=build_orchestrator_prompt(review_type, use_generalist=use_generalist),
        response_format=FinalReviewOutput,
    )


def build_orchestrator_prompt(review_type: str, use_generalist: bool = False) -> str:
    from ncodereview.prompts import GENERALIST_ORCHESTRATOR_PROMPT, ORCHESTRATOR_PROMPT

    if use_generalist:
        prompt = GENERALIST_ORCHESTRATOR_PROMPT
    else:
        prompt = ORCHESTRATOR_PROMPT

    if review_type == "incremental_review":
        prompt += (
            "\nThis is an INCREMENTAL review — focus on changes in the diff. "
            "Pre-existing issues outside the diff are out of scope.\n"
        )
    return prompt


MAX_RANGES_DISPLAY = 8


def build_user_message(
    pr_title: str,
    pr_files: list[str],
    line_ranges: dict[str, list[dict[str, int]]] | None = None,
) -> str:
    if line_ranges is None:
        line_ranges = {}

    file_lines: list[str] = []
    for f in pr_files:
        ranges = line_ranges.get(f, [])
        if ranges:
            if len(ranges) > MAX_RANGES_DISPLAY:
                first = ranges[0]
                last = ranges[-1]
                summary = f"L{first['start']}-L{last['end']}, {len(ranges)} changes"
                file_lines.append(f"- `{f}` ({summary})")
            else:
                range_strs = [f"L{r['start']}-{r['end']}" for r in ranges]
                file_lines.append(f"- `{f}` ({', '.join(range_strs)})")
        else:
            file_lines.append(f"- `{f}` (new file)")

    file_list = "\n".join(file_lines) or "- (none)"
    return (
        f"Please review this pull request.\n\n"
        f"**PR title:** {pr_title}\n\n"
        f"**Files to review in THIS BATCH ({len(pr_files)}):**\n{file_list}\n\n"
        f"**IMPORTANT:** Only review the files listed above. The diff.patch contains\n"
        f"changes ONLY for these files. The blast_radius.md shows impact analysis\n"
        f"for these files only. Do NOT comment on issues outside these files.\n\n"
        f"**Line ranges shown above are your guide** — when reading files, focus on\n"
        f"those ranges. For new files, read the entire file.\n\n"
        f"Required protocol:\n"
        f"1. Read diff.patch to understand what changed in these files\n"
        f"2. Read blast_radius.md to understand call impact\n"
        f"3. List `/home/user/workspace/repo` and read relevant files at the line ranges listed\n"
        f"4. Use available tools to traverse and understand the code structure\n"
    )
