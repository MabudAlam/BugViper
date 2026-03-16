
from collections import defaultdict

from code_review_agent.config import config
from code_review_agent.models.agent_schemas import ContextData, FileSummary, Issue, ReconciledReview

# ── Helpers ───────────────────────────────────────────────────────────────────

_LANG_MAP = {
    "py": "python", "ts": "typescript", "tsx": "typescript",
    "js": "javascript", "jsx": "javascript", "rb": "ruby",
    "go": "go", "rs": "rust", "java": "java", "cs": "csharp",
    "cpp": "cpp", "c": "c",
}


def _file_lang(path: str) -> str:
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return _LANG_MAP.get(ext, ext)


def _ai_fix_to_suggestion(ai_fix: str) -> str | None:
    """Convert a unified-diff ai_fix into a GitHub suggestion block.

    Extracts the '+' lines (new code) so the PR author can apply the fix
    with a single click. Returns None if there are no '+' lines.
    """
    plus_lines = [
        line[1:] for line in ai_fix.strip().splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if not plus_lines:
        return None
    return "```suggestion\n" + "\n".join(plus_lines) + "\n```"

_SEV_LABEL = {
    "critical": "🔴 Critical",
    "high":     "🟠 High",
    "medium":   "🟡 Medium",
    "low":      "🟢 Low",
}
_RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def _line_range(issue: Issue) -> str:
    if issue.line_end and issue.line_end != issue.line_start:
        return f"`{issue.line_start}-{issue.line_end}`"
    return f"`{issue.line_start}`"


def _render_issue_block(issue: Issue) -> list[str]:
    """Render one issue in CodeRabbit inline-comment style."""
    lines: list[str] = []

    sev = _SEV_LABEL.get(issue.severity, issue.severity.capitalize())
    lines.append(f"{_line_range(issue)}: _⚠️ {sev}_")
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
            "py": "python", "ts": "typescript", "tsx": "typescript",
            "js": "javascript", "jsx": "javascript",
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
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>🤖 Suggested fix</summary>")
        lines.append("")
        lines.append("```diff")
        lines.append(issue.ai_fix.strip("\n"))
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
    """Format one issue as an inline PR review comment body.

    If the issue has an ai_fix, converts it to a GitHub suggestion block
    so the PR author can apply it with one click.
    """
    sev = _SEV_LABEL.get(issue.severity, issue.severity.capitalize())
    lang = _file_lang(issue.file)

    lines: list[str] = [
        f"**{sev} · {issue.title}**",
        "",
        issue.description,
    ]

    if issue.impact:
        lines += ["", f"**Impact:** {issue.impact}"]

    if issue.suggestion:
        lines += ["", f"**Suggestion:** {issue.suggestion}"]

    if issue.ai_fix:
        suggestion = _ai_fix_to_suggestion(issue.ai_fix)
        if suggestion:
            lines += ["", suggestion]
        else:
            lines += ["", "```diff", issue.ai_fix.strip(), "```"]
    elif issue.code_snippet:
        lines += ["", f"```{lang}", issue.code_snippet.strip(), "```"]

    lines += ["", f"*Category: `{issue.category}` · Confidence: {issue.confidence}/10*"]
    return "\n".join(lines)


def format_review_summary(
    review: ReconciledReview,
    context: ContextData | None,
    pr_number: int,
    files_changed_summary: list[FileSummary] | None = None,
    walk_through: list[str] | None = None,
    inline_posted: int = 0,
    inline_skipped: int = 0,
) -> str:
    """Format the top-level PR review body (overview only).

    Individual issue details are posted as inline comments; this summary
    shows the walkthrough, impact analysis, and positive findings.
    """
    parts: list[str] = []

    fixed_issues = [i for i in review.issues if i.status == "fixed"]
    open_issues  = [i for i in review.issues if i.status == "still_open"]
    new_issues   = [i for i in review.issues if i.status == "new"]
    actionable   = len(open_issues) + len(new_issues)

    # ── Header ────────────────────────────────────────────────────────────────
    parts.append("## 🐍 BugViper AI Code Review")
    parts.append("")
    parts.append(f"**PR**: #{pr_number} | **Model**: `{config.review_model}`")
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
    if badges:
        parts.append("  ".join(badges))
        parts.append("")

    nitpick_note = f" + {len(nitpicks)} nitpicks below" if nitpicks else ""
    parts.append(f"**Actionable: {len(high_conf_actionable)}**{nitpick_note}")
    if inline_posted:
        skipped_note = f" ({inline_skipped} outside diff)" if inline_skipped else ""
        parts.append(f"*{inline_posted} inline comment(s) posted directly on the diff{skipped_note}*")
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

    # ── Impact analysis ───────────────────────────────────────────────────────
    if context:
        risk_emoji = _RISK_EMOJI.get(context.risk_level, "⚪")
        parts.append("<details>")
        parts.append("<summary>📊 Impact Analysis</summary>")
        parts.append("")
        parts.append(f"- **Symbols modified**: {len(context.modified_symbols)}")
        parts.append(f"- **Downstream callers**: {context.total_callers}")
        parts.append(f"- **Risk level**: {risk_emoji} {context.risk_level.upper()}")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    # ── Fixed ─────────────────────────────────────────────────────────────────
    if fixed_issues:
        parts.append(f"### ✅ Fixed Since Last Review ({len(fixed_issues)})")
        parts.append("")
        for issue in fixed_issues:
            parts.append(
                f"- ~~**{issue.title}**~~ `{issue.file}:{issue.line_start}` — resolved"
            )
        parts.append("")

    # ── Nitpicks toggle (all <7 confidence actionable issues) ─────────────────
    if nitpicks:
        parts.append("<details>")
        parts.append(f"<summary>🔍 Nitpicks & Low-confidence ({len(nitpicks)} issues, confidence &lt; 7)</summary>")
        parts.append("")
        parts.append("*These findings have lower confidence and may be false positives. Review at your discretion.*")
        parts.append("")
        for line in _render_issues_by_file(nitpicks):
            parts.append(line)
        parts.append("</details>")
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
        parts.append("✅ **No issues found. The code looks good!**")
        parts.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    parts.append("---")
    parts.append("")
    parts.append(
        f"*🤖 Generated by [BugViper](https://github.com/Pavel401/BugViper)"
        f" | Powered by `{config.review_model}`*"
    )

    return "\n".join(parts)


def format_github_comment(
    review: ReconciledReview,
    context: ContextData | None,
    pr_number: int,
    files_changed_summary: list[FileSummary] | None = None,
    walk_through: list[str] | None = None,
    raw_agent_json: str | None = None,
) -> str:
    """Format a ReconciledReview into a GitHub PR comment (CodeRabbit-style)."""
    parts: list[str] = []

    fixed_issues = [i for i in review.issues if i.status == "fixed"]
    open_issues  = [i for i in review.issues if i.status == "still_open"]
    new_issues   = [i for i in review.issues if i.status == "new"]
    actionable   = len(open_issues) + len(new_issues)

    # ── Header ────────────────────────────────────────────────────────────────
    parts.append("## 🐍 BugViper AI Code Review")
    parts.append("")
    parts.append(
        f"**PR**: #{pr_number} | **Model**: {config.review_model}"
    )
    parts.append("")

    run_summary_parts = []
    if fixed_issues:
        run_summary_parts.append(f"**{len(fixed_issues)} fixed**")
    if open_issues:
        run_summary_parts.append(f"**{len(open_issues)} still open**")
    if new_issues:
        run_summary_parts.append(f"**{len(new_issues)} new**")
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

    # ── Impact analysis ───────────────────────────────────────────────────────
    if context:
        risk_emoji = _RISK_EMOJI.get(context.risk_level, "⚪")
        parts.append("<details>")
        parts.append("<summary>📊 Impact Analysis</summary>")
        parts.append("")
        parts.append(f"- **Symbols modified**: {len(context.modified_symbols)}")
        parts.append(f"- **Downstream callers**: {context.total_callers}")
        parts.append(f"- **Risk level**: {risk_emoji} {context.risk_level.upper()}")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    parts.append("---")
    parts.append("")

    # ── Fixed ─────────────────────────────────────────────────────────────────
    if fixed_issues:
        parts.append(f"### ✅ Fixed Since Last Review ({len(fixed_issues)})")
        parts.append("")
        for issue in fixed_issues:
            parts.append(
                f"- ~~**{issue.title}**~~ `{issue.file}:{issue.line_start}` — resolved"
            )
        parts.append("")

    # Split all actionable issues by confidence
    open_high   = [i for i in open_issues if i.confidence >= 7]
    open_nitty  = [i for i in open_issues if i.confidence < 7]
    new_high    = [i for i in new_issues if i.confidence >= 7]
    new_nitty   = [i for i in new_issues if i.confidence < 7]
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

        critical = [i for i in new_high if i.severity == "critical"]
        high     = [i for i in new_high if i.severity == "high"]
        medium   = [i for i in new_high if i.severity == "medium"]
        low      = [i for i in new_high if i.severity == "low"]

        for group_label, group, open_default in [
            ("🔴 Critical", critical, True),
            ("🟠 High",     high,     True),
            ("🟡 Medium",   medium,   False),
            ("🟢 Low",      low,      False),
        ]:
            if not group:
                continue
            open_attr = " open" if open_default else ""
            parts.append(f"<details{open_attr}>")
            parts.append(f"<summary>{group_label} ({len(group)})</summary>")
            parts.append("")
            for line in _render_issues_by_file(group):
                parts.append(line)
            parts.append("</details>")
            parts.append("")

    # ── Nitpicks toggle (all <7 confidence actionable issues) ─────────────────
    if all_nitpicks:
        parts.append("<details>")
        parts.append(f"<summary>🔍 Nitpicks & Low-confidence ({len(all_nitpicks)} issues, confidence &lt; 7)</summary>")
        parts.append("")
        parts.append("*These findings have lower confidence and may be false positives. Review at your discretion.*")
        parts.append("")
        for line in _render_issues_by_file(all_nitpicks):
            parts.append(line)
        parts.append("</details>")
        parts.append("")

    if not fixed_issues and not open_issues and not new_issues:
        parts.append("✅ **No issues found. The code looks good!**")
        parts.append("")
    elif not fixed_issues and not open_high and not new_high and all_nitpicks:
        parts.append("✅ **No significant issues found.** Only low-confidence observations (see Nitpicks above).")
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

    # ── Raw agent JSON (capped to keep total comment under GitHub's 65 536-char limit) ──
    _MAX_JSON_CHARS = 30_000
    if raw_agent_json:
        display_json = raw_agent_json
        truncated = False
        if len(raw_agent_json) > _MAX_JSON_CHARS:
            display_json = raw_agent_json[:_MAX_JSON_CHARS]
            truncated = True
        parts.append("<details>")
        parts.append("<summary>🔍 Raw agent response (JSON)</summary>")
        parts.append("")
        parts.append("```json")
        parts.append(display_json)
        if truncated:
            parts.append("# ... (truncated — full JSON exceeds display limit)")
        parts.append("```")
        parts.append("")
        parts.append("</details>")
        parts.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    parts.append("---")
    parts.append("")
    parts.append(
        f"*🤖 Generated by [BugViper](https://github.com/Pavel401/BugViper)"
        f" | Powered by {config.review_model}*"
    )

    return "\n".join(parts)
