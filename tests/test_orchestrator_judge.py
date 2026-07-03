"""Test that the orchestrator's judge-reviewer classification flows through normalization."""

from __future__ import annotations

import asyncio

from ncodereview.pipeline import (
    _normalize_and_validate_review_data,
    _run_judge_pass_if_needed,
)


def _findings_with_classification():
    return [
        {"file": "foo.go", "line_start": 30, "line_end": 36,
         "severity": "high", "category": "bug",
         "title": "CORS regression", "description": "No fallback header",
         "confidence": 9, "classification": "valid"},
        {"file": "bar.go", "line_start": 42, "line_end": 42,
         "severity": "low", "category": "style",
         "title": "Rename helper", "description": "Generic name",
         "confidence": 8, "classification": "nitpick"},
        {"file": "baz.go", "line_start": 100, "line_end": 100,
         "severity": "critical", "category": "security",
         "title": "Off-diff XSS", "description": "Not in this PR diff",
         "confidence": 9, "classification": "outside-diff"},
        {"file": "phantom.go", "line_start": 5, "line_end": 5,
         "severity": "critical", "category": "security",
         "title": "Phantom SSRF", "description": "No typo in real file",
         "confidence": 9, "classification": "false",
         "drop_reason": "init() correctly initializes cidrs"},
    ]


def _make_diff_text():
    return (
        "diff --git a/foo.go b/foo.go\n"
        "@@ -27,3 +27,15 @@\n"
        " ctx\n"
        "+func NewCORS() {\n"
        "+// ... lines 30-36\n"
        "+line\n"
        "+line\n"
        "+line\n"
        "+line\n"
        "+line\n"
        "+line\n"
        "+}\n"
        "diff --git a/bar.go b/bar.go\n"
        "@@ -38,1 +42,1 @@ helper ctx\n"
        "+func helperName() {}\n"
        "diff --git a/baz.go b/baz.go\n"
        "@@ -95,1 +100,1 @@ off-diff ctx\n"
        "+some other change unrelated to cited line\n"
        "diff --git a/phantom.go b/phantom.go\n"
        "@@ -1,1 +5,1 @@ phantom context\n"
        "+garbage\n"
    )


def test_normalize_drops_false_preserves_others_with_classification():
    review_data = {
        "issues": [{"file": "foo.go", "issues": _findings_with_classification()}],
        "positives": [],
        "walkthrough": [],
        "summary": "",
    }
    result = _normalize_and_validate_review_data(
        review_data=review_data,
        diff_text=_make_diff_text(),
        changed_files=["foo.go", "bar.go", "baz.go", "phantom.go"],
    )
    assert result["_saw_judge_classifications"] is True
    assert result["_judgment_counts"]["false"] == 1
    assert result["_judgment_counts"]["valid"] == 1
    assert result["_judgment_counts"]["nitpick"] == 1
    assert result["_judgment_counts"]["outside-diff"] == 1
    issues = result["issues"]
    assert any(i["classification"] == "valid" for i in issues)
    assert any(i["classification"] == "nitpick" for i in issues)
    assert any(i["classification"] == "outside-diff" for i in issues)
    assert not any(i.get("classification") == "false" for i in issues), \
        "false findings must not survive normalization"
    assert result["_annotations"] == issues, "annotations mirror surviving issues"


def test_normalize_handles_legacy_no_classification():
    review_data = {
        "issues": [{"file": "foo.go", "issues": [
            {"file": "foo.go", "line_start": 30, "line_end": 36,
             "severity": "high", "category": "bug",
             "title": "CORS regression", "confidence": 9},
        ]}],
        "positives": [],
        "walkthrough": [],
        "summary": "",
    }
    result = _normalize_and_validate_review_data(
        review_data=review_data,
        diff_text=_make_diff_text(),
        changed_files=["foo.go", "bar.go", "baz.go", "phantom.go"],
    )
    assert result["_saw_judge_classifications"] is False
    assert "_judgment_counts" not in result


def test_fallback_judge_skipped_when_orchestrator_classified():
    async def run():
        review_data = {
            "issues": [
                {"file": "foo.go", "line_start": 30,
                 "severity": "high", "category": "bug",
                 "title": "Real bug", "confidence": 9,
                 "classification": "valid"},
            ],
            "_saw_judge_classifications": True,
            "_judgment_counts": {"valid": 1, "nitpick": 0, "outside-diff": 0, "false": 0},
            "_annotations": [{"file": "foo.go", "line_start": 30, "classification": "valid"}],
        }
        pr_files = {"foo.go": "package main\nfunc Bug() {}\n"}
        result = await _run_judge_pass_if_needed(review_data, pr_files)
        assert result is review_data
        assert "_judgment_counts" in result
        assert result["_judgment_counts"]["valid"] == 1

    asyncio.run(run())


def test_fallback_judge_runs_when_orchestrator_skipped():
    async def run():
        review_data = {
            "issues": [
                {"file": "foo.go", "line_start": 30,
                 "severity": "high", "category": "bug",
                 "title": "Real bug", "confidence": 9},
            ],
            "_saw_judge_classifications": False,
        }
        pr_files = {"foo.go": "package main\nfunc Bug() {}\n"}

        from langchain_core.messages import AIMessage

        class _MockJudgeModel:
            async def ainvoke(self, prompt):
                return AIMessage(
                    content=(
                        '```json\n'
                        '{"classification": "valid", '
                        '"resolved_line_start": 30, "resolved_line_end": 30, '
                        '"drop_reason": null}\n```'
                    )
                )

        from ncodereview.judge import judge_findings

        annotated = await judge_findings(
            _to_subagent_issues(review_data["issues"]), pr_files, model=_MockJudgeModel()
        )
        review_data["_annotations"] = annotated
        review_data["_judgment_counts"] = {"valid": 1, "nitpick": 0, "outside-diff": 0, "false": 0}

        result = await _run_judge_pass_if_needed(review_data, pr_files)
        assert result is review_data

    asyncio.run(run())


def _to_subagent_issues(raw):
    from ncodereview.schemas import SubagentReviewIssue

    return [
        SubagentReviewIssue(
            file=i.get("file", ""),
            line_start=int(i.get("line_start", 0)),
            line_end=i.get("line_end"),
            issue_type=i.get("issue_type", "Bug"),
            category=i.get("category", "bug"),
            severity=i.get("severity", "medium"),
            title=i.get("title", ""),
            description=i.get("description", ""),
            confidence=int(i.get("confidence", 8)),
        )
        for i in raw
    ]
