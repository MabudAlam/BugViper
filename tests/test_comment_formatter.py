from api.utils.comment_formatter import format_inline_comment, format_review_summary
from common.schemas import Issue, ReconciledReview


def test_inline_ai_prompt_keeps_current_code_inside_single_details_block():
    issue = Issue(
        file="internal/cache/redis.go",
        line_start=285,
        line_end=297,
        issue_type="Performance",
        category="performance",
        severity="medium",
        title="Stats scans every cache key",
        description=(
            "Stats uses SCAN with batch size 100 to count all cache keys iteratively."
        ),
        impact=(
            "Stats latency grows linearly with cache size -- 1M keys requires "
            "~10,000 Redis round-trips."
        ),
        suggestion="Use Redis DBSIZE for total key count.",
        code_snippet=(
            'for {\n'
            '    keys, nextCursor, err := c.client.Scan(ctx, cursor, keyPrefix+"*", 100).Result()\n'
            '    keyCount += int64(len(keys))\n'
            '    cursor = nextCursor\n'
            '    if cursor == 0 { break }\n'
            '}'
        ),
        confidence=5,
    )

    body = format_inline_comment(issue)

    assert body.count("<details>") == 1
    assert body.count("</details>") == 1
    assert "<summary>🤖 Prompt for AI Agent — click to expand &amp; copy</summary>" in body
    assert "~~~\nVerify each finding against current code." in body
    assert "Current code:\n```" in body
    assert body.index("Current code:") < body.index("~~~", body.index("Current code:"))


def test_combined_ai_prompt_uses_single_details_block_with_tilde_fence():
    issue = Issue(
        file="internal/cache/redis.go",
        line_start=285,
        line_end=297,
        issue_type="Performance",
        category="performance",
        severity="medium",
        title="Stats scans every cache key",
        description="Stats uses SCAN with batch size 100 to count all cache keys iteratively.",
        impact="Stats latency grows linearly with cache size.",
        suggestion="Use Redis DBSIZE for total key count.",
        code_snippet=(
            'for {\n'
            '    keys, nextCursor, err := c.client.Scan(ctx, cursor, keyPrefix+"*", 100).Result()\n'
            '    keyCount += int64(len(keys))\n'
            '    cursor = nextCursor\n'
            '    if cursor == 0 { break }\n'
            '}'
        ),
        confidence=8,
    )
    review = ReconciledReview(issues=[issue], summary="Summary")

    body = format_review_summary(review, None, pr_number=1)
    prompt_start = body.index("<summary>🤖 Prompt for AI Agent")
    prompt_block = body[prompt_start:]

    assert body.count("<summary>🤖 Prompt for AI Agent — click to expand &amp; copy</summary>") == 1
    assert "~~~\nVerify each finding against current code." in prompt_block
    assert "Current code:\n```" in prompt_block
    assert prompt_block.index("Current code:") < prompt_block.index(
        "~~~", prompt_block.index("Current code:")
    )
