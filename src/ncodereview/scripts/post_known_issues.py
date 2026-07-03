"""Post the known issues from a previous review run to the same PR.

Like knowledge_parser/knowledge_runner.py — edit the PR_LINK constant below,
then run:

    uv run python -m ncodereview.scripts.post_known_issues

Or pass overrides:

    --pr-link https://github.com/<owner>/<repo>/pull/<n>
    --issues path/to/issues.json
    --head <sha>            auto-fetched if omitted
    --diff-file path.patch  fetched from GitHub if omitted

App auth (required):
  GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH  (loaded from .env automatically)
"""  # noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(ENV_PATH)

PR_LINK = "https://github.com/MabudAlam/QC_BugViper_test/pull/8"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("post_known_issues")


DEFAULT_ISSUES_PATH = Path(__file__).parent / "data" / "last_review_issues.json"


def _load_hardcoded_issues() -> list[dict]:
    if DEFAULT_ISSUES_PATH.exists():
        with open(DEFAULT_ISSUES_PATH) as fh:
            data = json.load(fh)
        return data["issues"] if isinstance(data, dict) and "issues" in data else data
    return []


def _print_table(rows: list[tuple[str, str]]) -> None:
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}")


async def _post_one(gh, owner, repo, pr_number, head_sha, diff_text, issue: dict, idx: int) -> dict:
    """Post one inline comment; return a result dict summarizing what happened."""
    from api.utils.comment_formatter import format_inline_comment
    from common.diff_parser import (
        calculate_comment_line,
        extract_valid_diff_lines,
        snap_lines_to_diff,
        split_diff_by_file,
    )
    from common.schemas import Issue

    file = issue["file"]
    line_start = int(issue["line_start"])
    line_end = int(issue.get("line_end") or line_start)

    patches_by_file = split_diff_by_file(diff_text)
    valid_ranges = extract_valid_diff_lines(patches_by_file.get(file))
    snapped = snap_lines_to_diff(line_start, line_end, valid_ranges)
    if snapped is None:
        return {
            "idx": idx,
            "file": file,
            "line_start": line_start,
            "line_end": line_end,
            "outcome": "skipped",
            "reason": "no valid diff ranges (snap returned None)",
        }

    s_start, s_end = snapped
    line = calculate_comment_line(s_start, s_end, 15)
    start_line = s_start if line != s_start else None

    issue_obj = Issue(
        file=file,
        line_start=line_start,
        line_end=line_end,
        title=issue.get("title", "Untitled issue"),
        category=issue.get("category", "bug"),
        severity=issue.get("severity", "medium"),
        issue_type=issue.get("issue_type", "Potential issue"),
        description=issue.get("description", ""),
        suggestion=issue.get("suggestion", ""),
        impact=issue.get("impact", ""),
        code_snippet=issue.get("code_snippet", ""),
        confidence=int(issue.get("confidence", 8)),
        classification=issue.get("classification"),
        status="new",
    )
    body = format_inline_comment(issue_obj)

    print(f"\n[{idx}] {file}:{s_start}-{s_end} (snapped from {line_start}-{line_end})")
    print(f"    title: {issue.get('title','')[:80]}")
    print(f"    category={issue.get('category')} conf={issue.get('confidence')}")

    try:
        result = await gh.post_inline_comment(
            owner, repo, pr_number, head_sha, file, line, body,
            start_line=start_line,
        )
        if result.get("success"):
            cid = result.get("comment_id")
            return {
                "idx": idx,
                "file": file,
                "line_start": s_start,
                "line_end": s_end,
                "outcome": "posted",
                "comment_id": cid,
            }
        return {
            "idx": idx,
            "file": file,
            "line_start": s_start,
            "line_end": s_end,
            "outcome": "skipped",
            "reason": "GitHub rejected after retries",
        }
    except Exception as exc:
        return {
            "idx": idx,
            "file": file,
            "line_start": s_start,
            "line_end": s_end,
            "outcome": "error",
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _parse_pr_link(pr_link: str) -> tuple[str, str, int]:
    """Parse a github.com/<owner>/<repo>/pull/<n> URL into (owner, repo, pr_number).

    Accepts trailing slashes and surrounding whitespace. Raises argparse.ArgumentTypeError
    if the URL doesn't match the expected shape.
    """
    import re
    pattern = re.compile(
        r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)/?",
        re.IGNORECASE,
    )
    match = pattern.match(pr_link.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid PR link: {pr_link!r}. Expected: https://github.com/<owner>/<repo>/pull/<n>"
        )
    return match.group(1), match.group(2), int(match.group(3))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issues", default=None,
        help="Path to JSON file with issues; omit to use hardcoded set",
    )
    parser.add_argument(
        "--pr-link", type=_parse_pr_link, default=None,
        help="PR URL (default: PR_LINK constant at top of file).",
    )
    parser.add_argument("--owner", default=os.getenv("OWNER"))
    parser.add_argument("--repo", default=os.getenv("REPO"))
    parser.add_argument("--pr", type=int, default=int(os.getenv("PR_NUMBER", "0")) or None)
    parser.add_argument(
        "--head", default=os.getenv("HEAD_SHA"),
        help="Head commit SHA; auto-fetched from the PR if omitted.",
    )
    parser.add_argument("--diff-file", default=None, help="Path to a file with the PR diff text")
    args = parser.parse_args()

    if not args.pr_link:
        try:
            args.pr_link = _parse_pr_link(PR_LINK)
        except argparse.ArgumentTypeError:
            pass

    if args.pr_link:
        args.owner, args.repo, args.pr = args.pr_link
        print(f"Using PR: {args.owner}/{args.repo}#{args.pr}")

    if not (args.owner and args.repo and args.pr):
        parser.error(
            "Need PR_LINK constant OR --pr-link OR (--owner/--repo/--pr) "
            "OR (OWNER/REPO/PR_NUMBER env vars)"
        )

    from common.github_client import get_github_client
    gh = get_github_client()

    diff_text: str = ""
    if args.diff_file:
        diff_text = Path(args.diff_file).read_text()
    else:
        args.head = await gh.get_pr_head_ref(args.owner, args.repo, args.pr)
        diff_text = await gh.get_pr_diff(args.owner, args.repo, args.pr)
        print(f"Resolved head SHA: {args.head[:10]}...")

    if not args.head:
        args.head = await gh.get_pr_head_ref(args.owner, args.repo, args.pr)
        print(f"Resolved head SHA: {args.head[:10]}...")

    if args.issues:
        with open(args.issues) as fh:
            data = json.load(fh)
        issues = data["issues"] if isinstance(data, dict) and "issues" in data else data
    else:
        issues = _load_hardcoded_issues()
        if not issues:
            print("No issues provided and no default file found.")
            print(f"Expected defaults at: {DEFAULT_ISSUES_PATH}")
            sys.exit(1)

    _print_table([
        ("Owner", args.owner),
        ("Repo", args.repo),
        ("PR", str(args.pr)),
        ("Head SHA", args.head[:10] + "..."),
        ("Issues loaded", str(len(issues))),
        ("Diff size", f"{len(diff_text)} chars"),
    ])

    results = []
    for idx, issue in enumerate(issues, start=1):
        result = await _post_one(
            gh, args.owner, args.repo, args.pr, args.head, diff_text, issue, idx,
        )
        results.append(result)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    posted = [r for r in results if r["outcome"] == "posted"]
    skipped = [r for r in results if r["outcome"] == "skipped"]
    errored = [r for r in results if r["outcome"] == "error"]
    print(f"Total issues: {len(results)}")
    print(f"Posted:       {len(posted)}")
    print(f"Skipped:      {len(skipped)}")
    print(f"Errored:      {len(errored)}")

    if skipped or errored:
        print("\nNon-posted:")
        for r in skipped + errored:
            print(
                f"  [{r['idx']}] {r['file']}:{r['line_start']}-{r['line_end']} — "
                f"{r['outcome']}: {r.get('reason','?')}"
            )

    if posted:
        print("\nPosted:")
        for r in posted:
            print(
                f"  [{r['idx']}] {r['file']}:{r['line_start']}-{r['line_end']} — "
                f"comment_id={r.get('comment_id')}"
            )


if __name__ == "__main__":
    asyncio.run(main())


def _test_parse_pr_link() -> None:
    import argparse
    cases = [
        ("https://github.com/MabudAlam/QC_BugViper_test/pull/8",
         ("MabudAlam", "QC_BugViper_test", 8)),
        ("github.com/foo/bar/pull/42", ("foo", "bar", 42)),
        ("https://github.com/a/b/pull/1/", ("a", "b", 1)),
        ("  https://github.com/x/y/pull/99  ", ("x", "y", 99)),
    ]
    for url, expected in cases:
        got = _parse_pr_link(url)
        assert got == expected, f"{url!r}: expected {expected}, got {got}"

    invalid = ["not a url", "https://github.com/foo/bar", "https://gitlab.com/foo/bar/pull/1"]
    for url in invalid:
        try:
            _parse_pr_link(url)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"Expected ArgumentTypeError for {url!r}")


if os.getenv("TEST_PARSE_PR_LINK"):
    _test_parse_pr_link()
    print("parse_pr_link: all assertions passed")
