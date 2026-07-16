from __future__ import annotations


def create_verifier_agent(model, sbx, run_limit=10):
    """Create a standalone verifier agent with sandbox tools (read_file, grep).

    Runs on the same runner with the same tools as the finder agent.
    """
    from deepagents.backends import CompositeBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from deepagents.middleware.subagents import create_agent
    from langchain.agents.middleware import ToolCallLimitMiddleware
    from langchain_e2b import E2BSandbox

    from ncodereview.middleware import ModelCallLimitMiddleware
    from ncodereview.prompts import VERIFIER_SYSTEM_PROMPT
    from ncodereview.schemas import VerifierOutput

    e2b_backend = E2BSandbox(sandbox=sbx)
    backend = CompositeBackend(
        default=e2b_backend,
        routes={},
    )
    return create_agent(
        model=model,
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        tools=[],
        middleware=[
            ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior="report"),
            FilesystemMiddleware(backend=backend),
            ToolCallLimitMiddleware(run_limit=run_limit),
        ],
        name="verifier",
        response_format=VerifierOutput,
    )


def build_verifier_task(flat_issues: list[dict]) -> str:
    """Build the verifier task prompt with all findings inline."""
    from ncodereview.prompts import VERIFIER_TASK_PROMPT

    lines: list[str] = []
    for i, issue in enumerate(flat_issues):
        file = issue.get("file", "?")
        ls = issue.get("line_start", "?")
        le = issue.get("line_end", ls)
        cat = issue.get("category", "?")
        sev = issue.get("severity", "?")
        title = (issue.get("title") or "")[:120]
        desc = (issue.get("description") or "")[:300]
        snippet = (issue.get("code_snippet") or "")[:200]
        lines.append(
            f"[{i}] {file}:{ls}-{le} [{cat}/{sev}]: {title}\n"
            f"    description: {desc}\n"
            f"    code: {snippet}"
        )

    findings_block = "\n\n".join(lines) if lines else "No findings to verify."
    return VERIFIER_TASK_PROMPT.format(findings_block=findings_block)


def create_direct_generalist_agent(
    model,
    sbx,
    review_type: str,
    review_mode: str = 'normal',
    run_limit: int = 30,
    files_in_batch: int = 1,
):
    """Create a generalist reviewer agent directly — no orchestrator.

    The agent explores code with tools (read_file, grep, etc.), then returns
    a structured FinalReviewOutput via response_format. No JSON parsing
    needed — result["structured_response"] is always a valid Pydantic model.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend
    from langchain.agents.middleware import ToolCallLimitMiddleware, ModelFallbackMiddleware, ModelRetryMiddleware
    from langchain_e2b import E2BSandbox

    from ncodereview.config import config
    from ncodereview.llm import load_chat_model
    from ncodereview.prompts import GENERALIST_PROMPT
    from ncodereview.schemas import FinalReviewOutput
    from ncodereview.middleware import ModelCallLimitMiddleware

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
        system_prompt=GENERALIST_PROMPT,
        middleware=[
            ModelRetryMiddleware(
                max_retries=config.MODEL_RETRY_MAX_RETRIES,
                backoff_factor=config.MODEL_RETRY_BACKOFF_FACTOR,
                initial_delay=config.MODEL_RETRY_INITIAL_DELAY,
                max_delay=config.MODEL_RETRY_MAX_DELAY,
            ),
            ModelFallbackMiddleware(
                model,
                *(load_chat_model(m) for m in config.DEEPAGENT_CODE_REVIEW_MODEL_FALLBACK),
            ),
            ModelCallLimitMiddleware(
                run_limit=run_limit,
                exit_behavior="report",
                response_format=FinalReviewOutput,
            ),
            ToolCallLimitMiddleware(run_limit=300),
        ],
        response_format=FinalReviewOutput,
    )


# ── Step budgets ──────────────────────────────────────────────────────────

NORMAL_MODE_STEPS: dict[str, int] = {
    'generalist': 30,
    'bug': 30,
    'security': 20,
    'performance': 20,
}
DEEP_MODE_STEPS = 100


def calculate_batch_tool_limits(files_in_batch: int, review_mode: str = 'normal') -> int:
    if review_mode == 'deep':
        return DEEP_MODE_STEPS
    base = max(NORMAL_MODE_STEPS.values())
    BASELINE_FILES = 8
    STEPS_PER_EXTRA_FILE = 0.5
    if files_in_batch <= BASELINE_FILES:
        return base
    extra = round((files_in_batch - BASELINE_FILES) * STEPS_PER_EXTRA_FILE)
    return min(base + extra, DEEP_MODE_STEPS)


def get_subagent_steps(agent_name: str, review_mode: str, files_in_batch: int) -> int:
    if review_mode == 'deep':
        return DEEP_MODE_STEPS
    base = NORMAL_MODE_STEPS.get(agent_name, 20)
    BASELINE_FILES = 8
    STEPS_PER_EXTRA_FILE = 0.5
    if files_in_batch <= BASELINE_FILES:
        return base
    extra = round((files_in_batch - BASELINE_FILES) * STEPS_PER_EXTRA_FILE)
    return min(base + extra, DEEP_MODE_STEPS)


# ── Agent creation ────────────────────────────────────────────────────────

MAX_RANGES_DISPLAY = 8


def build_user_message(
    pr_title: str,
    pr_files: list[str],
    line_ranges: dict[str, list[dict[str, int]]] | None = None,
) -> str:
    """Build user message in <ReviewJob> format."""
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
        f"<ReviewJob>\n"
        f"  <PRInfo>\n"
        f"    Title: {pr_title}\n"
        f"  </PRInfo>\n\n"
        f"  <Changes>\n"
        f"    The unified diff is at `/home/user/review/diff.patch`. Read it with `read_file` to see what changed.\n"
        f"  </Changes>\n\n"
        f"  <Brief>\n"
        f"    Review the files listed below. Only report issues on files in this batch.\n"
        f"    Files in this batch ({len(pr_files)}):\n"
        f"{file_list}\n\n"
        f"    The diff.patch contains changes ONLY for these files.\n"
        f"    When reading source files, use `read_file /home/user/workspace/repo/<file>`.\n"
        f"  </Brief>\n"
        f"</ReviewJob>"
    )


def create_specialized_agent(model, sbx, system_prompt: str, name: str, run_limit: int = 30):
    """Create a standalone specialized agent using the same pattern as the generalist.

    Uses create_deep_agent (deepagents library) with FinalReviewOutput,
    matching create_direct_generalist_agent exactly.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend
    from langchain.agents.middleware import ToolCallLimitMiddleware, ModelFallbackMiddleware, ModelRetryMiddleware
    from langchain_e2b import E2BSandbox

    from ncodereview.config import config
    from ncodereview.llm import load_chat_model
    from ncodereview.middleware import ModelCallLimitMiddleware
    from ncodereview.schemas import FinalReviewOutput

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
        system_prompt=system_prompt,
        middleware=[
            ModelRetryMiddleware(
                max_retries=config.MODEL_RETRY_MAX_RETRIES,
                backoff_factor=config.MODEL_RETRY_BACKOFF_FACTOR,
                initial_delay=config.MODEL_RETRY_INITIAL_DELAY,
                max_delay=config.MODEL_RETRY_MAX_DELAY,
            ),
            ModelFallbackMiddleware(
                model,
                *(load_chat_model(m) for m in config.DEEPAGENT_CODE_REVIEW_MODEL_FALLBACK),
            ),
            ModelCallLimitMiddleware(
                run_limit=run_limit,
                exit_behavior="report",
                response_format=FinalReviewOutput,
            ),
            ToolCallLimitMiddleware(run_limit=300),
        ],
        response_format=FinalReviewOutput,
    )


def merge_subagent_results(results: list[dict]) -> "FinalReviewOutput":
    """Merge multiple FinalReviewOutput results into a single FinalReviewOutput."""
    from ncodereview.schemas import FinalReviewOutput

    all_issues = []
    all_positives = []
    all_walkthrough = []
    seen_files = set()
    agent_names = ["correctness", "security", "performance"]
    issue_counts = []

    for i, result in enumerate(results):
        structured = result.get("structured_response")
        if structured is None:
            continue
        if hasattr(structured, "model_dump"):
            data = structured.model_dump()
        elif hasattr(structured, "dict"):
            data = structured.dict()
        else:
            data = structured

        issues = data.get("issues", [])
        for issue in issues:
            issue["_agent"] = agent_names[i] if i < len(agent_names) else f"agent_{i}"
        all_issues.extend(issues)
        all_positives.extend(data.get("positives", []))

        for wt in data.get("walkthrough", []):
            f = wt.get("file", "")
            summary = wt.get("summary", "")
            if f and summary:
                # Merge with existing entry for same file if exists
                existing = next((e for e in all_walkthrough if e.get("file") == f), None)
                if existing:
                    existing["summary"] += f" | {summary}"
                else:
                    all_walkthrough.append({"file": f, "summary": summary})

        issue_counts.append(f"{agent_names[i]}: {len(issues)} issues")

    summary = "Deep review completed. " + " | ".join(issue_counts) + "." if issue_counts else "Deep review completed."

    return FinalReviewOutput(
        issues=all_issues,
        positives=all_positives,
        walkthrough=all_walkthrough,
        summary=summary,
    )
