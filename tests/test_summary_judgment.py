from api.utils.comment_formatter import format_inline_comment, format_review_summary
from common.schemas import Issue, ReconciledReview


def test_summary_renders_judgment_counts_header_when_provided():
    issues = [
        Issue(file="foo.go", line_start=30, line_end=36, title="CORS regression",
              category="bug", severity="high", confidence=9, classification="valid"),
        Issue(file="bar.go", line_start=42, line_end=42, title="Rename helper",
              category="style", severity="low", confidence=8, classification="nitpick"),
        Issue(file="baz.go", line_start=100, line_end=100, title="Off-diff finding",
              category="security", severity="critical", confidence=9,
              classification="outside-diff"),
    ]
    body = format_review_summary(
        ReconciledReview(issues=issues),
        context=None,
        pr_number=7,
        walk_through=["foo.go — CORS middleware fix"],
        inline_posted=2,
        inline_skipped=1,
        judgment_counts={"valid": 1, "nitpick": 1, "outside-diff": 1, "false": 2},
    )
    assert "**Actionable:** 1" in body
    assert "🟡 **Nitpicks:** 1" in body
    assert "⚪ **Outside diff:** 1" in body
    assert "✂️ **Dropped (false):** 2" in body


def test_summary_renders_outside_diff_section():
    outside = Issue(file="baz.go", line_start=100, line_end=100, title="Off-diff",
                    category="security", severity="critical", confidence=9,
                    classification="outside-diff")
    body = format_review_summary(
        ReconciledReview(issues=[outside]),
        context=None,
        pr_number=7,
        walk_through=["baz.go — unrelated"],
        judgment_counts={"valid": 0, "nitpick": 0, "outside-diff": 1, "false": 0},
    )
    assert "⚠️ Review outside the diff (1)" in body
    assert "baz.go" in body
    assert "Off-diff" in body


def test_summary_shows_all_issues_in_all_issues_table():
    issues = [
        Issue(file="a.go", line_start=10, title="valid bug", category="bug",
              severity="high", confidence=9, classification="valid"),
        Issue(file="b.go", line_start=20, title="nitpick rename", category="style",
              severity="low", confidence=8, classification="nitpick"),
        Issue(file="c.go", line_start=30, title="outside diff bug", category="bug",
              severity="medium", confidence=9, classification="outside-diff"),
    ]
    body = format_review_summary(
        ReconciledReview(issues=issues),
        context=None,
        pr_number=1,
        walk_through=["x.go — change"],
        judgment_counts={"valid": 1, "nitpick": 1, "outside-diff": 1, "false": 0},
    )
    assert "🔍 All Issues (3)" in body
    assert "valid bug" in body
    assert "nitpick rename" in body
    assert "outside diff bug" in body


def test_inline_comment_shows_nitpick_tag():
    nitpick = Issue(file="b.go", line_start=20, title="rename", category="style",
                    severity="low", confidence=8, classification="nitpick")
    body = format_inline_comment(nitpick)
    assert "🟡 Nitpick" in body
    assert "rename" in body


def test_inline_comment_no_nitpick_tag_for_valid():
    valid = Issue(file="a.go", line_start=10, title="real bug", category="bug",
                  severity="high", confidence=9, classification="valid")
    body = format_inline_comment(valid)
    assert "🟡 Nitpick" not in body
    assert "Potential issue" in body


def test_summary_falls_back_to_confidence_when_no_judgment_counts():
    issues = [
        Issue(file="a.go", line_start=10, title="high conf", category="bug",
              severity="high", confidence=9),
        Issue(file="b.go", line_start=20, title="low conf", category="bug",
              severity="medium", confidence=5),
    ]
    body = format_review_summary(
        ReconciledReview(issues=issues),
        context=None,
        pr_number=1,
        walk_through=["x.go — change"],
    )
    assert "Actionable:" in body
    assert "nitpicks below" in body
