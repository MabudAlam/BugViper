"""Tests for ncodereview.call_graph + common.diff_parser line-snapping."""

from __future__ import annotations

import pytest

from common.diff_parser import (
    calculate_comment_line,
    extract_valid_diff_lines,
    snap_lines_to_diff,
    split_diff_by_file,
)
from ncodereview.call_graph import (
    CallGraph,
    _find_caller,
    build_call_graph,
    format_call_graph,
    parse_file,
)


class TestParseFileGo:
    def test_go_functions_and_calls(self) -> None:
        source = """\
package main

import "fmt"

func main() {
    result := processUser(42)
    fmt.Println(result)
}

func processUser(id int) string {
    name := fetchFromDB(id)
    return formatResponse(name)
}

func fetchFromDB(id int) string {
    return "user-" + strconv.Itoa(id)
}

func formatResponse(name string) string {
    return fmt.Sprintf("Hello, %s", name)
}
"""
        pf = parse_file("internal/api/handlers/user.go", source)
        assert pf.error is None, pf.error
        assert pf.language == "go"

        func_names = [f.name for f in pf.functions]
        assert "main" in func_names
        assert "processUser" in func_names
        assert "fetchFromDB" in func_names
        assert "formatResponse" in func_names

        call_names = [c.name for c in pf.call_sites]
        assert "processUser" in call_names
        assert "fetchFromDB" in call_names
        assert "formatResponse" in call_names
        has_println = "Println" in call_names
        assert has_println

    def test_go_line_numbers_correct(self) -> None:
        source = """\
package main

func add(a, b int) int {
    return a + b
}

func multiply(a, b int) int {
    return a * b
}

func compute(x, y int) int {
    return add(x, y) + multiply(x, y)
}
"""
        pf = parse_file("calc.go", source)
        assert pf.error is None

        compute_func = next(f for f in pf.functions if f.name == "compute")
        assert compute_func.line_number == 11
        assert compute_func.end_line == 13

        compute_calls = [
            c for c in pf.call_sites
            if _find_caller("calc.go", pf.functions, c) is not None
        ]
        assert len(compute_calls) == 2


class TestBuildCallGraph:
    def test_resolves_internal_calls(self) -> None:
        handler = """\
package handler

func RateLimitMiddlewareGin(c *Context) {
    if !Allow(c) {
        return
    }
    clientIP := getClientIPGin(c)
    _ = clientIP
}

func Allow(c *Context) bool {
    return true
}

func getClientIPGin(c *Context) string {
    return c.Request.IP
}
"""
        urlutils = """\
package urlutils

func ValidateURL(rawURL string) error {
    if !ValidateSafeURL(rawURL) {
        return ErrInvalidURL
    }
    return nil
}

func ValidateSafeURL(rawURL string) bool {
    if isBlockedIP(rawURL) {
        return false
    }
    return true
}

func isBlockedIP(rawURL string) bool {
    return false
}
"""
        pr_files = {
            "internal/api/middleware/ratelimit.go": handler,
            "internal/utils/url.go": urlutils,
        }
        graph = build_call_graph(pr_files)

        assert len(graph.nodes) >= 5

        allow_node = next(
            (n for n in graph.nodes.values() if n.name == "Allow"), None
        )
        assert allow_node is not None

        rate_limit_calls_allow = [
            e for e in graph.calls
            if e.caller == "RateLimitMiddlewareGin" and e.callee == "Allow"
        ]
        assert len(rate_limit_calls_allow) == 1
        assert rate_limit_calls_allow[0].resolved is True

        validate_calls_safe = [
            e for e in graph.calls
            if e.caller == "ValidateURL" and e.callee == "ValidateSafeURL"
        ]
        assert len(validate_calls_safe) == 1
        assert validate_calls_safe[0].resolved is True

    def test_cross_file_calls(self) -> None:
        handler = """\
package handler

import "internal/scrape"

func Scrape(url string) string {
    return scrape.Fetch(url)
}
"""
        scrape_pkg = """\
package scrape

func Fetch(url string) string {
    return "content"
}
"""
        graph = build_call_graph({
            "handler.go": handler,
            "scrape/scrape.go": scrape_pkg,
        })

        scrape_fetch_calls = [e for e in graph.calls if e.callee == "Fetch"]
        assert len(scrape_fetch_calls) == 1
        assert scrape_fetch_calls[0].resolved is True

    def test_no_false_positives_on_empty_files(self) -> None:
        graph = build_call_graph({})
        assert len(graph.nodes) == 0
        assert len(graph.calls) == 0

    def test_skips_external_unresolved_calls(self) -> None:
        source = """\
package main

import "fmt"

func main() {
    fmt.Println("hello")
    result := externalCall()
    _ = result
}
"""
        graph = build_call_graph({"main.go": source})

        external = [e for e in graph.calls if e.callee == "externalCall"]
        assert len(external) == 1
        assert external[0].resolved is False


class TestFormatCallGraph:
    def test_contains_function_list(self) -> None:
        source = """\
package main

func alpha() {}
func beta() {}
"""
        graph = build_call_graph({"main.go": source})
        output = format_call_graph(graph)

        assert "alpha" in output
        assert "beta" in output
        assert "main.go" in output

    def test_contains_no_calls_placeholder(self) -> None:
        source = """\
package main

func lone() {}
"""
        graph = build_call_graph({"main.go": source})
        output = format_call_graph(graph)

        assert "lone" in output
        assert "no calls" in output.lower()


class TestFindCaller:
    def test_finds_caller_by_line_range(self) -> None:
        source = """\
package main

func outer() {
    inner()
    inner()
}

func inner() {}
"""
        pf = parse_file("main.go", source)
        inner_calls = [c for c in pf.call_sites if c.name == "inner"]

        assert len(inner_calls) == 2
        for call in inner_calls:
            caller = _find_caller("main.go", pf.functions, call)
            assert caller is not None
            assert "outer" in caller


_DIFF = (
    "diff --git a/internal/api/handlers/handler.go b/internal/api/handlers/handler.go\n"
    "--- a/internal/api/handlers/handler.go\n"
    "+++ b/internal/api/handlers/handler.go\n"
    "@@ -519,5 +519,7 @@ func (h *Handler) Brand(c *gin.Context) {\n"
    " if req.URL == \"\" {\n"
    " \t\treturn\n"
    " }\n"
    "+\t// SSRF: validates nothing about the URL\n"
    "+\t// before passing it to scraper.FetchBrand\n"
    " ctx := c.Request.Context()\n"
    " scraper := h.State.CoreScraper\n"
    "diff --git a/internal/api/middleware/gin_middleware.go b/internal/api/middleware/gin_middleware.go\n"
    "--- a/internal/api/middleware/gin_middleware.go\n"
    "+++ b/internal/api/middleware/gin_middleware.go\n"
    "@@ -90,3 +92,5 @@ func CORSMiddlewareGin() gin.HandlerFunc {\n"
    " \t\tc.AbortWithStatus(http.StatusOK)\n"
    "+\t\t// also reflect arbitrary Origin without allowlist\n"
    "+\t\t// with credentials enabled\n"
    " \t\treturn\n"
    " }\n"
)

_PATCHES = split_diff_by_file(_DIFF)
_HANDLER_PATCH = _PATCHES["internal/api/handlers/handler.go"]
_MIDDLEWARE_PATCH = _PATCHES["internal/api/middleware/gin_middleware.go"]


class TestSplitDiffByFile:
    def test_splits_into_two_files(self) -> None:
        assert set(_PATCHES.keys()) == {
            "internal/api/handlers/handler.go",
            "internal/api/middleware/gin_middleware.go",
        }

    def test_each_patch_contains_its_hunks(self) -> None:
        assert "@@ -519,5" in _HANDLER_PATCH
        assert "@@ -90,3" in _MIDDLEWARE_PATCH
        assert "@@ -519,5" not in _MIDDLEWARE_PATCH


class TestExtractValidDiffLines:
    def test_handler_single_hunk(self) -> None:
        ranges = extract_valid_diff_lines(_HANDLER_PATCH)
        assert ranges == [(519, 525)]

    def test_middleware_single_hunk(self) -> None:
        ranges = extract_valid_diff_lines(_MIDDLEWARE_PATCH)
        assert ranges == [(92, 96)]

    def test_empty_patch_returns_empty(self) -> None:
        assert extract_valid_diff_lines("") == []
        assert extract_valid_diff_lines(None) == []

    def test_no_newline_marker_is_skipped(self) -> None:
        patch = (
            "diff --git a/x b/x\n"
            "--- a/x\n+++ b/x\n"
            "@@ -1,2 +1,3 @@\n"
            " a\n"
            "+b\n"
            " c\n"
            "\\ No newline at end of file\n"
        )
        ranges = extract_valid_diff_lines(patch)
        assert ranges == [(1, 3)]

    def test_two_hunks_yield_two_ranges(self) -> None:
        patch = (
            "diff --git a/x b/x\n"
            "--- a/x\n+++ b/x\n"
            "@@ -1,2 +1,3 @@\n"
            " a\n+b\n c\n"
            "@@ -10,2 +10,3 @@\n"
            " d\n+e\n f\n"
        )
        ranges = extract_valid_diff_lines(patch)
        assert ranges == [(1, 3), (10, 12)]


class TestSnapLinesToDiff:
    def test_in_range_passes_through(self) -> None:
        ranges = [(519, 525), (90, 96)]
        result = snap_lines_to_diff(521, 521, ranges)
        assert result == (521, 521)

    def test_overlap_clips_to_range(self) -> None:
        ranges = [(519, 525)]
        result = snap_lines_to_diff(518, 530, ranges)
        assert result == (519, 525)

    def test_no_overlap_snaps_to_nearest(self) -> None:
        ranges = [(519, 525), (90, 96)]
        result = snap_lines_to_diff(700, 705, ranges)
        assert result is not None
        snapped_start, _ = result
        assert snapped_start in range(519, 526)

    def test_no_ranges_returns_none(self) -> None:
        assert snap_lines_to_diff(42, 42, []) is None

    def test_none_start_falls_back_to_first_range(self) -> None:
        ranges = [(10, 15), (50, 55)]
        result = snap_lines_to_diff(None, None, ranges)
        assert result == (10, 15)


class TestCalculateCommentLine:
    def test_single_line_returns_start(self) -> None:
        assert calculate_comment_line(42, 42) == 42

    def test_none_end_returns_start(self) -> None:
        assert calculate_comment_line(42, None) == 42

    def test_small_range_returns_end(self) -> None:
        assert calculate_comment_line(42, 45) == 45

    def test_oversized_range_collapses_to_start(self) -> None:
        assert calculate_comment_line(42, 100) == 42
        assert calculate_comment_line(42, 60) == 42
