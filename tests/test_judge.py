"""Smoke test: judge classifies correctly across 4 known scenarios.

Covers:
- phantom typo flagged as `false`
- real CORS regression flagged as `valid`
- hallucinated file path flagged as `outside-diff`
- bounded_read centers on the requested line within the radius
"""

from __future__ import annotations

import asyncio

from ncodereview.judge import (
    bounded_read,
    judge_findings,
    partition_findings,
    summarize_judgment,
)
from ncodereview.schemas import SubagentReviewIssue


class _MockModel:
    """Returns a deterministic classification per finding file."""

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    async def ainvoke(self, prompt):
        from langchain_core.messages import AIMessage

        for key, classification in self.mapping.items():
            if key in str(prompt):
                return AIMessage(
                    content=(
                        "```json\n"
                        '{"classification": "' + classification + '", '
                        '"resolved_line_start": 1, '
                        '"resolved_line_end": 1, '
                        '"drop_reason": null}\n'
                        "```"
                    )
                )
        return AIMessage(content='{"classification": "valid"}')


_URL_GO = """\
   1: package utils

   3: func init() {
   4:    cidrs := make([]*net.IPNet, 0, 5)
   5:    for _, s := range []string{"10.0.0.0/8"} {
   6:        _, ipNet, err := net.ParseCIDR(s)
   7:        if err == nil {
   8:            cidrs = append(cidrs, ipNet)
   9:        }
  10:    }
  11:    privateCIDRs = cidrs
  12: }
"""

_MW = """\
   1: func CORSMiddlewareGin() gin.HandlerFunc {
   2:    return func(c *gin.Context) {
   3:        origin := c.GetHeader("Origin")
   4:        if origin != "" && allowedOrigins[origin] {
   5:            c.Header("Access-Control-Allow-Origin", origin)
   6:            c.Header("Access-Control-Allow-Credentials", "true")
   7:        } else if origin == "" {
   8:            c.Header("Access-Control-Allow-Origin", "*")
   9:        }
  10:        c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
  11:    }
  12: }
"""


def _findings():
    return [
        SubagentReviewIssue(
            file="internal/utils/url.go",
            line_start=15,
            line_end=15,
            severity="critical",
            category="security",
            title="SSRF bypass via cids/cidrs typo",
            description="init() appends to undefined 'cids'",
            confidence=9,
        ),
        SubagentReviewIssue(
            file="internal/api/middleware/gin_middleware.go",
            line_start=30,
            line_end=36,
            severity="high",
            category="bug",
            title="CORS regression: missing fallback header",
            description="No Access-Control-Allow-Origin when origin not in allowlist",
            confidence=9,
        ),
        SubagentReviewIssue(
            file="nonexistent/path/foo.go",
            line_start=42,
            line_end=42,
            severity="medium",
            category="bug",
            title="hallucinated file",
            description="This file does not exist",
            confidence=8,
        ),
    ]


def _pr_files():
    return {
        "internal/utils/url.go": _URL_GO,
        "internal/api/middleware/gin_middleware.go": _MW,
    }


def test_bounded_read_centers_on_requested_line():
    content = "\n".join(f"line {i}: filler" for i in range(1, 51))
    window = bounded_read(content, center_line=10, radius=5, max_lines=20)
    assert "   6: " in window
    assert "  15: " in window
    assert "   1: " not in window


def test_bounded_read_caps_at_max_lines():
    content = "\n".join(f"L{i}" for i in range(1, 1001))
    window = bounded_read(content, center_line=500, radius=100, max_lines=40)
    line_count = window.count("\n") + 1
    assert line_count <= 40


def test_judge_drops_phantom_typo_keeps_real_bug_partitions_unknown_file():
    async def run():
        mock = _MockModel(
            {
                "internal/utils/url.go": "false",
                "internal/api/middleware/gin_middleware.go": "valid",
            }
        )
        annotated = await judge_findings(_findings(), _pr_files(), model=mock)
        summary = summarize_judgment(annotated)
        assert summary == {"valid": 1, "nitpick": 0, "outside-diff": 1, "false": 1}

        parts = partition_findings(annotated)
        assert len(parts["false"]) == 1
        assert parts["false"][0]["file"] == "internal/utils/url.go"
        assert len(parts["valid"]) == 1
        assert parts["valid"][0]["file"] == "internal/api/middleware/gin_middleware.go"
        assert len(parts["outside-diff"]) == 1
        assert parts["outside-diff"][0]["file"] == "nonexistent/path/foo.go"

    asyncio.run(run())


def test_judge_falls_back_to_valid_on_llm_error():
    class _Broken:
        async def ainvoke(self, prompt):
            raise RuntimeError("rate limit")

    async def run():
        annotated = await judge_findings(_findings(), _pr_files(), model=_Broken())
        valid_count = sum(1 for f in annotated if f["classification"] == "valid")
        assert valid_count == 2

    asyncio.run(run())
