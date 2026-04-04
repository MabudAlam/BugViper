from collections import defaultdict

from code_review_agent.config import config
from code_review_agent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview

# ── Helpers ───────────────────────────────────────────────────────────────────

_SECURITY_TOOLS = {"bandit", "semgrep", "gitleaks"}


def _extract_tool(issue: Issue) -> str:
    """Parse tool name from lint issue titles like '[ruff] E501: ...'."""
    if issue.title.startswith("[") and "]" in issue.title:
        return issue.title[1 : issue.title.index("]")]
    return "lint"


def _render_static_section(lint_issues: list[Issue]) -> list[str]:
    """Render grouped static analysis findings by tool.

    Security tools (bandit, semgrep, gitleaks) are shown open by default.
    Style/quality tools are collapsed.
    """
    if not lint_issues:
        return []

    by_tool: dict[str, list[Issue]] = defaultdict(list)
    for issue in lint_issues:
        by_tool[_extract_tool(issue)].append(issue)

    lines: list[str] = []
    lines.append("### 🔬 Static Analysis")
    lines.append("")
    lines.append(
        f"*{len(lint_issues)} finding(s) from {len(by_tool)} tool(s) — "
        "deterministic, confidence 10/10*"
    )
    lines.append("")

    # Security tools first, then alphabetical
    security = {t: v for t, v in by_tool.items() if t in _SECURITY_TOOLS}
    quality = {t: v for t, v in by_tool.items() if t not in _SECURITY_TOOLS}

    for tool, issues in list(security.items()) + sorted(quality.items()):
        is_sec = tool in _SECURITY_TOOLS
        # All sections open by default
        lines.append("<details open>")
        lines.append(
            f"<summary>{'🔐' if is_sec else '🔧'} {tool} ({len(issues)} issue(s))</summary>"
        )
        lines.append("")
        lines.append("| File | Line | Rule | Message |")
        lines.append("|------|------|------|---------|")
        for issue in sorted(issues, key=lambda i: (i.file, i.line_start)):
            # Extract rule from title: "[tool] RULE: message" → "RULE"
            rule = ""
            if "] " in issue.title:
                after_tool = issue.title.split("] ", 1)[1]
                rule = (
                    after_tool.split(":")[0].strip() if ":" in after_tool else after_tool.split()[0]
                )
            # No truncation — show full message
            lines.append(
                f"| `{issue.file}` | {issue.line_start} | `{rule}` | {issue.description} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return lines


_LANG_MAP = {
    "py": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "rb": "ruby",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "cs": "csharp",
    "cpp": "cpp",
    "c": "c",
}


def _file_lang(path: str) -> str:
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return _LANG_MAP.get(ext, ext)


def _ai_fix_to_code_block(ai_fix: str, file_ext: str) -> str | None:
    """Convert a unified-diff ai_fix into a human-readable code block.

    Extracts only the '+' lines (new code) and formats them as a clean code snippet.
    Returns None for complex or malformed diffs.
    """
    raw = ai_fix.strip()
    lines = raw.splitlines()

    # Reject malformed diffs
    for line in lines:
        if line.startswith("-+") or line.startswith("+-"):
            return None

    # Extract only the new lines (lines starting with '+')
    plus_lines = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
    if not plus_lines:
        return None

    # Detect language from file extension
    lang_map = {
        "py": "python",
        "ts": "typescript",
        "tsx": "typescript",
        "js": "javascript",
        "jsx": "javascript",
        "rb": "ruby",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "cs": "csharp",
        "cpp": "cpp",
        "c": "c",
    }
    lang = lang_map.get(file_ext, file_ext)

    # Join with proper indentation
    code = "\n".join(plus_lines)
    return f"```{lang}\n{code}\n```"


def _ai_fix_to_suggestion(ai_fix: str) -> str | None:
    """Convert a unified-diff ai_fix into a GitHub suggestion block.

    Only produces a suggestion when the diff is clean and unambiguous:
    - Contains at least one '+' line (new code)
    - No lines that start with '-+' or '+-' (malformed agent output)
    - No hunk headers ('@@') that would confuse the suggestion block

    Returns None for complex or malformed diffs so callers fall back to
    showing the fix as prose — avoids broken rendered comments.
    """
    raw = ai_fix.strip()
    lines = raw.splitlines()

    # Reject malformed diffs immediately
    for line in lines:
        # Agent sometimes writes '-+' or '+-' on the same line — not valid unified diff
        if line.startswith("-+") or line.startswith("+-") or line.startswith("@@"):
            return None

    plus_lines = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
    if not plus_lines:
        return None

    return "```suggestion\n" + "\n".join(plus_lines) + "\n```"


# Severity labels removed — descriptions carry the meaning


def _line_range(issue: Issue) -> str:
    if issue.line_end and issue.line_end != issue.line_start:
        return f"`{issue.line_start}-{issue.line_end}`"
    return f"`{issue.line_start}`"


def _render_issue_block(issue: Issue) -> list[str]:
    """Render one issue in CodeRabbit inline-comment style."""
    lines: list[str] = []

    issue_type = getattr(issue, "issue_type", None) or "Potential issue"
    lines.append(f"{_line_range(issue)}: ⚠️ **{issue_type}**")
    lines.append("")
    lines.append(f"**{issue.title}**")
    lines.append("")
    lines.append(issue.description)

    if issue.impact:
        lines.append("")
        lines.append(f"**Impact**: {issue.impact}")

    if issue.code_snippet:
        lines.append("")
        # detect language from the file extension
        ext = issue.file.rsplit(".", 1)[-1] if "." in issue.file else ""
        lang_map = {
            "py": "python",
            "ts": "typescript",
            "tsx": "typescript",
            "js": "javascript",
            "jsx": "javascript",
        }
        lang = lang_map.get(ext, ext)
        lines.append(f"```{lang}")
        # strip leading/trailing blank lines from snippet
        snippet = issue.code_snippet.strip("\n")
        lines.append(snippet)
        lines.append("```")

    if issue.suggestion:
        lines.append("")
        lines.append(f"> **Suggestion**: {issue.suggestion}")

    if issue.ai_fix:
        suggestion = _ai_fix_to_suggestion(issue.ai_fix)
        lines.append("")
        if suggestion:
            lines.append("<details>")
            lines.append("<summary>🔧 Suggested fix (apply on diff)</summary>")
            lines.append("")
            lines.append(suggestion)
            lines.append("")
            lines.append("</details>")
        else:
            # Strip any existing code block markers from ai_fix
            raw_fix = issue.ai_fix.strip()
            if raw_fix.startswith("```"):
                # Remove opening code block
                first_newline = raw_fix.find("\n")
                if first_newline != -1:
                    raw_fix = raw_fix[first_newline + 1 :]
            if raw_fix.endswith("```"):
                # Remove closing code block
                raw_fix = raw_fix[:-3].rstrip()
            lines.append("<details>")
            lines.append("<summary>🔧 Suggested fix (diff)</summary>")
            lines.append("")
            lines.append("```diff")
            lines.append(raw_fix.strip())
            lines.append("```")
            lines.append("")
            lines.append("</details>")

    return lines


def _render_issues_by_file(issues: list[Issue]) -> list[str]:
    """Group issues by file and render with collapsible file headers."""
    if not issues:
        return []

    by_file: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        by_file[issue.file].append(issue)

    lines: list[str] = []
    for file_path, file_issues in sorted(by_file.items()):
        lines.append(f"**`{file_path}`**")
        lines.append("")
        for issue in sorted(file_issues, key=lambda i: i.line_start):
            for line in _render_issue_block(issue):
                lines.append(line)
            lines.append("")
        lines.append("---")
        lines.append("")

    return lines


# ── Public API ────────────────────────────────────────────────────────────────


def format_inline_comment(issue: Issue) -> str:
    """Format one issue as an inline PR review comment body."""
    lang = _file_lang(issue.file)
    issue_type = getattr(issue, "issue_type", None) or "Potential issue"

    lines: list[str] = [
        f"⚠️{issue_type}",
        "",
        f"**{issue.title}**",
        "",
        issue.description,
    ]

    if issue.impact:
        lines += ["", f"**Impact:** {issue.impact}"]

    if issue.suggestion:
        lines += ["", f"**Suggestion:** {issue.suggestion}"]

    if issue.ai_fix:
        # ai_fix is now the corrected code, not a diff
        fix_text = issue.ai_fix.strip()
        # Remove any surrounding code blocks if present
        if fix_text.startswith("```"):
            first_newline = fix_text.find("\n")
            if first_newline != -1:
                fix_text = fix_text[first_newline + 1 :]
        if fix_text.endswith("```"):
            fix_text = fix_text[:-3].rstrip()

        lines += [
            "",
            "**Suggested fix:**",
            "",
            f"```{lang}",
            fix_text,
            "```",
        ]

    if issue.code_snippet and not issue.ai_fix:
        lines += ["", f"```{lang}", issue.code_snippet.strip(), "```"]

    ai_agent_prompt = getattr(issue, "ai_agent_prompt", None)
    if ai_agent_prompt:
        lines += [
            "",
            "---",
            "",
            "**🤖 Prompt for AI Agents**",
            "",
            "Copy this to give to an AI agent to fix the issue:",
            "",
            "```",
            ai_agent_prompt,
            "```",
        ]

    lines += ["", f"*Category: `{issue.category}` · Confidence: {issue.confidence}/10*"]
    return "\n".join(lines)


def _render_debug_section(raw_agent_outputs: dict[str, str], debug_info: dict) -> list[str]:
    """Render a collapsible debug section with agent JSON + full lint dump."""
    lines: list[str] = []
    lines.append("<details>")
    lines.append("<summary>🛠️ Debug — Agent output &amp; static analysis dump</summary>")
    lines.append("")

    # Pipeline stats
    tool_rounds = debug_info.get("tool_rounds_used", 0)
    lint_raw = debug_info.get("lint_raw_count", 0)
    lint_pr = debug_info.get("lint_on_diff_count", 0)
    lines.append(f"**Explorer tool rounds:** {tool_rounds}")
    lines.append(f"**Lint findings:** {lint_pr} in PR files / {lint_raw} total (pre-filter)")
    lines.append("")

    # Raw lint dump
    lint_raw_issues: list[dict] = debug_info.get("lint_raw", [])
    if lint_raw_issues:
        lines.append("**All lint findings (including pre-existing / off-diff):**")
        lines.append("")
        lines.append("| File | Line | Tool | Rule | Severity |")
        lines.append("|------|------|------|------|----------|")
        for i in lint_raw_issues:
            title = i.get("title", "")
            rule = ""
            if "] " in title:
                after = title.split("] ", 1)[1]
                rule = after.split(":")[0].strip() if ":" in after else after.split()[0]
            tool = title[1 : title.index("]")] if title.startswith("[") and "]" in title else "lint"
            lines.append(
                f"| `{i.get('file', '')}` | {i.get('line_start', '')} "
                f"| {tool} | `{rule}` | {i.get('severity', '')} |"
            )
        lines.append("")

    # Raw agent JSON outputs per file
    _MAX_JSON = 25_000
    if raw_agent_outputs:
        lines.append("**Raw Agent Output (JSON):**")
        lines.append("")
        for file_path, raw_json in raw_agent_outputs.items():
            lines.append(f"### `{file_path}`")
            lines.append("")
            display = raw_json[:_MAX_JSON]
            truncated = len(raw_json) > _MAX_JSON
            # Escape embedded code blocks to prevent markdown breakage
            display = display.replace("```", "\\`\\`\\`")
            lines.append("```json")
            lines.append(display)
            if truncated:
                lines.append("# ... truncated")
            lines.append("```")
            lines.append("")

    lines.append("</details>")
    lines.append("")
    return lines


def format_review_summary(
    review: ReconciledReview,
    context: ContextData | None,
    pr_number: int,
    lint_issues: list[Issue] | None = None,
    files_changed_summary: list[FileSummary] | None = None,
    walk_through: list[str] | None = None,
    inline_posted: int = 0,
    inline_skipped: int = 0,
    raw_agent_outputs: dict[str, str] | None = None,
    debug_info: dict | None = None,
) -> str:
    """Format the top-level PR review body (overview only).

    Individual issue details are posted as inline comments; this summary
    shows the walkthrough, impact analysis, and positive findings.
    """
    parts: list[str] = []

    # review.issues contains LLM-only findings; lint_issues are passed separately
    lint_issues = lint_issues or []
    fixed_issues = [i for i in review.issues if i.status == "fixed"]
    open_issues = [i for i in review.issues if i.status == "still_open"]
    new_issues = [i for i in review.issues if i.status == "new"]
    actionable = len(open_issues) + len(new_issues)

    # ── Header ────────────────────────────────────────────────────────────────
    parts.append("## 🐍 BugViper AI Code Review")
    parts.append("")
    parts.append(f"**PR**: #{pr_number} | **Model**: `{config.synthesis_model}`")
    parts.append("")

    high_conf_actionable = [i for i in open_issues + new_issues if i.confidence >= 7]
    nitpicks = [i for i in open_issues + new_issues if i.confidence < 7]

    badges = []
    if fixed_issues:
        badges.append(f"✅ {len(fixed_issues)} fixed")
    if open_issues:
        badges.append(f"🔁 {len(open_issues)} still open")
    if new_issues:
        badges.append(f"🆕 {len(new_issues)} new")
    if lint_issues:
        badges.append(f"🔬 {len(lint_issues)} static")
    if badges:
        parts.append("  ".join(badges))
        parts.append("")

    nitpick_note = f" + {len(nitpicks)} nitpicks below" if nitpicks else ""
    parts.append(f"**Actionable: {len(high_conf_actionable)}**{nitpick_note}")
    if inline_posted:
        skipped_note = f" ({inline_skipped} outside diff)" if inline_skipped else ""
        parts.append(
            f"*{inline_posted} inline comment(s) posted directly on the diff{skipped_note}*"
        )
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── Walkthrough ───────────────────────────────────────────────────────────
    wt_rows = walk_through or []
    if not wt_rows and files_changed_summary:
        wt_rows = [f"`{fs.file}` — {fs.what_changed}" for fs in files_changed_summary]

    if wt_rows:
        parts.append("<details open>")
        parts.append("<summary>📋 Walkthrough</summary>")
        parts.append("")
        parts.append("| File | Change |")
        parts.append("|------|--------|")
        for entry in wt_rows:
            if " — " in entry:
                fp, summary = entry.split(" — ", 1)
                fp = fp.strip().strip("`")
                parts.append(f"| `{fp}` | {summary.strip()} |")
            else:
                parts.append(f"| | {entry} |")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    # Impact analysis intentionally removed — low signal for most PRs

    # ── All Issues table (collapsed) ──────────────────────────────────────────
    all_actionable = [i for i in review.issues if i.status in ("new", "still_open")]
    if all_actionable:
        sorted_issues = sorted(all_actionable, key=lambda i: i.confidence, reverse=True)
        parts.append("<details>")
        parts.append(f"<summary>🔍 All Issues ({len(sorted_issues)})</summary>")
        parts.append("")
        parts.append("| File | Line | Type | Title | Confidence |")
        parts.append("|------|------|------|-------|------------|")
        for i in sorted_issues:
            status_icon = "🆕" if i.status == "new" else "🔁"
            line_ref = (
                f"{i.line_start}"
                if not i.line_end or i.line_end == i.line_start
                else f"{i.line_start}–{i.line_end}"
            )
            issue_type = getattr(i, "issue_type", None) or "Potential issue"
            parts.append(
                f"| `{i.file}` | {line_ref} | {status_icon} {issue_type} "
                f"| {i.title} | {i.confidence}/10 |"
            )
        parts.append("")
        parts.append("</details>")
        parts.append("")

    # ── Fixed ─────────────────────────────────────────────────────────────────
    if fixed_issues:
        parts.append(f"### ✅ Fixed Since Last Review ({len(fixed_issues)})")
        parts.append("")
        for issue in fixed_issues:
            parts.append(f"- ~~**{issue.title}**~~ `{issue.file}:{issue.line_start}` — resolved")
        parts.append("")

    # ── Nitpicks toggle (all <7 confidence actionable issues) ─────────────────
    if nitpicks:
        parts.append(f"🔍 **Nitpicks & Low-confidence** ({len(nitpicks)})")
        parts.append("")
        parts.append(
            "*These findings have lower confidence and may be false positives. "
            "Review at your discretion.*"
        )
        parts.append("")
        for issue in nitpicks:
            lines = _render_issue_block(issue)
            parts.append("<details>")
            summary = (
                f"<summary>{_line_range(issue)}: ⚠️ "
                f"{getattr(issue, 'issue_type', 'Issue') or 'Issue'} — "
                f"{issue.title} "
                f"[{issue.confidence}/10]</summary>"
            )
            parts.append(summary)
            parts.append("")
            for line in lines:
                parts.append(line)
            parts.append("</details>")
            parts.append("")
        parts.append("")

    # ── Positive findings ─────────────────────────────────────────────────────
    if review.positive_findings:
        parts.append("<details>")
        parts.append("<summary>👍 Positive Findings</summary>")
        parts.append("")
        for finding in review.positive_findings:
            parts.append(f"- {finding}")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    if not actionable and not fixed_issues:
        parts.append("✅ **BugViper found no issues.**")
        parts.append("")

    # ── Static analysis section ───────────────────────────────────────────────
    for line in _render_static_section(lint_issues):
        parts.append(line)

    # ── Debug section ─────────────────────────────────────────────────────────
    if raw_agent_outputs or debug_info:
        for line in _render_debug_section(raw_agent_outputs or {}, debug_info or {}):
            parts.append(line)

    # ── Footer ────────────────────────────────────────────────────────────────
    parts.append("---")
    parts.append("")
    parts.append(
        f"*🤖 Generated by [BugViper](https://github.com/Pavel401/BugViper)"
        f" | Powered by `{config.synthesis_model}`*"
    )

    return "\n".join(parts)


def format_github_comment(
    review: ReconciledReview,
    context: ContextData | None,
    pr_number: int,
    lint_issues: list[Issue] | None = None,
    files_changed_summary: list[FileSummary] | None = None,
    walk_through: list[str] | None = None,
    raw_agent_outputs: dict[str, str] | None = None,
    debug_info: dict | None = None,
) -> str:
    """Format a ReconciledReview into a GitHub PR comment (CodeRabbit-style)."""
    parts: list[str] = []

    # review.issues contains LLM-only findings; lint_issues are passed separately
    lint_issues = lint_issues or []
    fixed_issues = [i for i in review.issues if i.status == "fixed"]
    open_issues = [i for i in review.issues if i.status == "still_open"]
    new_issues = [i for i in review.issues if i.status == "new"]
    actionable = len(open_issues) + len(new_issues)

    # ── Header ────────────────────────────────────────────────────────────────
    parts.append("## 🐍 BugViper AI Code Review")
    parts.append("")
    parts.append(f"**PR**: #{pr_number} | **Model**: {config.synthesis_model}")
    parts.append("")

    run_summary_parts = []
    if fixed_issues:
        run_summary_parts.append(f"**{len(fixed_issues)} fixed**")
    if open_issues:
        run_summary_parts.append(f"**{len(open_issues)} still open**")
    if new_issues:
        run_summary_parts.append(f"**{len(new_issues)} new**")
    if lint_issues:
        run_summary_parts.append(f"**{len(lint_issues)} static**")
    if run_summary_parts:
        parts.append(" · ".join(run_summary_parts))
        parts.append("")

    parts.append(f"**Actionable comments: {actionable}**")
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── Walkthrough ───────────────────────────────────────────────────────────
    wt_rows = walk_through or []
    # Fall back to files_changed_summary if agent didn't produce a walkthrough
    if not wt_rows and files_changed_summary:
        wt_rows = [f"`{fs.file}` — {fs.what_changed}" for fs in files_changed_summary]

    if wt_rows:
        parts.append("<details>")
        parts.append("<summary>📋 Walkthrough</summary>")
        parts.append("")
        parts.append("| File | Change |")
        parts.append("|------|--------|")
        for entry in wt_rows:
            # Accept both "file — summary" string and plain strings
            if " — " in entry:
                file_part, summary_part = entry.split(" — ", 1)
                file_part = file_part.strip().strip("`")
                parts.append(f"| `{file_part}` | {summary_part.strip()} |")
            else:
                parts.append(f"| | {entry} |")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    parts.append("---")
    parts.append("")

    if fixed_issues:
        parts.append(f"### ✅ Fixed Since Last Review ({len(fixed_issues)})")
        parts.append("")
        for issue in fixed_issues:
            parts.append(f"- ~~**{issue.title}**~~ `{issue.file}:{issue.line_start}` — resolved")
        parts.append("")

    # Split all actionable issues by confidence
    open_high = [i for i in open_issues if i.confidence >= 7]
    open_nitty = [i for i in open_issues if i.confidence < 7]
    new_high = [i for i in new_issues if i.confidence >= 7]
    new_nitty = [i for i in new_issues if i.confidence < 7]
    all_nitpicks = open_nitty + new_nitty

    # ── Still open (high confidence) ──────────────────────────────────────────
    if open_high:
        parts.append(f"### 🔁 Still Open ({len(open_high)})")
        parts.append("")
        for line in _render_issues_by_file(open_high):
            parts.append(line)

    # ── New issues (high confidence) — grouped by severity ────────────────────
    if new_high:
        parts.append(f"### 🆕 New Issues ({len(new_high)})")
        parts.append("")
        for line in _render_issues_by_file(new_high):
            parts.append(line)
        parts.append("")

    # ── Nitpicks toggle (all <7 confidence actionable issues) ─────────────────
    if all_nitpicks:
        parts.append("<details>")
        parts.append(
            f"<summary>🔍 Nitpicks & Low-confidence "
            f"({len(all_nitpicks)} issues, confidence &lt; 7)</summary>"
        )
        parts.append("")
        parts.append(
            "*These findings have lower confidence and may be false positives. "
            "Review at your discretion.*"
        )
        parts.append("")
        for line in _render_issues_by_file(all_nitpicks):
            parts.append(line)
        parts.append("</details>")
        parts.append("")

    if not fixed_issues and not open_issues and not new_issues:
        parts.append("✅ **BugViper found no issues.**")
        parts.append("")
    elif not fixed_issues and not open_high and not new_high and all_nitpicks:
        parts.append(
            "✅ **No significant issues found.** "
            "Only low-confidence observations (see Nitpicks above)."
        )
        parts.append("")

    # ── Static analysis section ───────────────────────────────────────────────
    for line in _render_static_section(lint_issues):
        parts.append(line)

    # ── Positive findings ─────────────────────────────────────────────────────
    if review.positive_findings:
        parts.append("<details>")
        parts.append("<summary>👍 Positive Findings</summary>")
        parts.append("")
        for finding in review.positive_findings:
            parts.append(f"- {finding}")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    # ── Debug section ─────────────────────────────────────────────────────────
    if raw_agent_outputs or debug_info:
        for line in _render_debug_section(raw_agent_outputs or {}, debug_info or {}):
            parts.append(line)

    # ── Footer ────────────────────────────────────────────────────────────────
    parts.append("---")
    parts.append("")
    parts.append(
        f"*🤖 Generated by [BugViper](https://github.com/Pavel401/BugViper)"
        f" | Powered by {config.synthesis_model}*"
    )

    return "\n".join(parts)


def format_pr_description(
    review: ReconciledReview,
    walk_through: list[str] | None = None,
) -> str:
    """Format a CodeRabbit-style PR description summary.

    This is designed to be appended to the PR body, providing a high-level
    overview of changes grouped by category (New Features, Bug Fixes, etc.).
    """
    parts: list[str] = []

    parts.append("<!-- This is an auto-generated comment: release notes by BugViper -->")
    parts.append("## Summary by BugViper")
    parts.append("")

    open_issues = [i for i in review.issues if i.status in ("new", "still_open")]
    positive_findings = review.positive_findings or []

    categories: dict[str, list[str]] = {}

    if open_issues:
        bug_fixes = []
        improvements = []
        for issue in open_issues:
            if issue.category in ("bug", "security", "logic_error"):
                bug_fixes.append(f"{issue.title} in `{issue.file}`")
            else:
                improvements.append(f"{issue.title} in `{issue.file}`")

        if bug_fixes:
            categories["Bug Fixes"] = bug_fixes
        if improvements:
            categories["Improvements"] = improvements

    if walk_through:
        new_features = []
        for entry in walk_through:
            if " — " in entry:
                _, summary = entry.split(" — ", 1)
                summary = summary.strip()
                if any(
                    keyword in summary.lower()
                    for keyword in ["add", "new", "introduce", "implement"]
                ):
                    new_features.append(summary)

        if new_features:
            categories["New Features"] = new_features

    if positive_findings:
        categories["Code Quality"] = [f for f in positive_findings[:5]]

    if not categories:
        categories["Changes"] = (
            [f"Reviewed {len(walk_through)} file(s)"]
            if walk_through
            else ["Minor changes detected"]
        )

    for category, items in categories.items():
        parts.append(f"* **{category}**")
        for item in items:
            parts.append(f"  * {item}")
        parts.append("")

    parts.append("<!-- end of auto-generated comment: release notes by BugViper -->")

    return "\n".join(parts)
