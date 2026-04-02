# Code Review Pipeline Analysis

## Pipeline Overview

1. **Trigger**: `@bugviper` mention in PR comment → `/onComment` webhook
2. **Context Building**: Diff parsing → AST analysis → Graph enrichment → History loading
3. **Execution**: Lint + LLM review run in parallel
4. **Output**: Inline comments + PR review summary posted to GitHub

---

## Critical Gaps & Issues

### 1. Webhook Security - No Signature Verification

- **Location**: `api/routers/webhook.py:18-39`
- **Issue**: The `/onComment` endpoint does NOT verify the webhook signature (`X-Hub-Signature-256`)
- **Risk**: Anyone can spoof requests and trigger reviews on any repo
- **Fix**: Add HMAC verification like the marketplace webhook at line 221-226

### 2. No Authorization Check

- **Location**: `api/routers/webhook.py:153-199`
- **Issue**: Any GitHub user can trigger a review for any repository by mentioning `@bugviper`
- **Risk**: Public repo abuse, spam reviews
- **Fix**: Verify the commenter has write access to the repo before processing

### 3. TOCTOU Race Condition in Running Review Check

- **Location**: `api/routers/webhook.py:176-190`
- **Issue**: Check for `RUNNING` status is not atomic - status could change between check and actual execution
- **Risk**: Duplicate reviews in edge cases
- **Fix**: Use Firestore transaction or database-level atomic operation

### 4. No Retry Mechanism

- **Location**: `api/services/review_service.py:449-931`
- **Issue**: If review fails at any step (LLM timeout, API error), no automatic retry
- **Risk**: Users must manually re-trigger, especially for transient failures
- **Fix**: Add retry logic with exponential backoff

### 5. Arbitrary Cap on External Symbol Resolution

- **Location**: `api/services/review_service.py:612`
- **Issue**: `for name in list(external_calls)[:30]` - only resolves 30 external calls
- **Risk**: Larger PRs miss critical context from imported modules
- **Fix**: Make configurable or use smarter prioritization (e.g., prioritize called functions in the diff's changed lines)

### 6. Graph Context Not Required

- **Location**: `api/services/review_service.py:664-670`
- **Issue**: If Neo4j has no data for the repo (not indexed), graph context returns "No graph context available" - but no clear handling
- **Risk**: Review runs without dependency/caller context for non-indexed repos
- **Fix**: Add clear warning/fallback path or require repo to be indexed before allowing reviews

### 7. Hardcoded Token Limits (Truncation)

- **Locations**:
  - `api/services/review_service.py:198` - diff truncated at 60K chars
  - `api/services/review_service.py:213` - file content at 20K chars
  - `api/services/review_service.py:233` - imported symbols at 3K chars
- **Issue**: Large files/PRs lose context silently
- **Risk**: Miss important bugs in large diffs

### 8. No Diff Validation Against Current Branch

- **Location**: `api/services/review_service.py:485-489`
- **Issue**: Uses `head_sha` from PR but doesn't verify it matches current branch state
- **Risk**: Stale SHA could cause wrong file content to be fetched

### 9. Lint Issues Not Filtered by Changed Lines

- **Location**: `api/services/review_service.py:753-761`
- **Issue**: Lint runs on full file content, not just PR-changed lines
- **Risk**: Reports pre-existing issues not introduced by PR (false positives)

### 10. No Rate Limiting

- **Location**: `api/routers/webhook.py:153`
- **Issue**: Can trigger unlimited reviews (though running check helps)
- **Risk**: DoS via excessive review requests

---

## System Quality Assessment

### Strengths

- **Well-structured pipeline** with clear separation of concerns
- **Parallel execution** of lint + LLM for speed
- **Rich graph context** integration when available
- **History tracking** for fixed/still_open issue detection
- **Comprehensive tools** for the explorer agent (19 tools)
- **Fallback handling** for PR review API failures
- **Debug output** to local files for troubleshooting

### Weaknesses

- **Security-first missing** - no webhook verification, no auth check
- **Resilience lacking** - no retry, no circuit breaker
- **Scalability concerns** - arbitrary caps, no streaming for large outputs
- **Observability gaps** - no metrics on review success/failure rates

### Recommendations Priority

1. **P0**: Add webhook signature verification
2. **P0**: Add authorization check (caller has write access)
3. **P1**: Add retry mechanism with backoff
4. **P1**: Remove hardcoded token caps or make configurable
5. **P2**: Add retry status check with atomic operations
6. **P2**: Add rate limiting

---

The pipeline architecture is solid but needs security hardening and resilience improvements before production use.


Current Architecture Analysis
Your current approach has a fundamental problem:
Review Prompt (60K diff + 20K/file contents + imports + graph context)
    ↓
Explorer Agent (Max 10 tool rounds) - explores HUGE context blindly
    ↓
Review Agent (synthesis) - receives all explored context
The Problem with Large PRs
1. Prompt bloat: The _build_review_prompt() dumps everything (diff, full files, imports, graph) into the prompt BEFORE the Explorer runs
2. Explorer has no guidance: It explores blindly with no idea what parts of the huge context are relevant
3. Token explosion: 60K diff + multiple 20K files + imported symbols + graph context = easily 100K+ tokens
4. 10 tool round cap: Explorer can only make 10 tool calls to understand a massive codebase - insufficient
---
Recommended Approach: Two-Pass Architecture
Pass 1: Intelligent Context Selection (Pre-Explorer)
Before sending to LLM, analyze the diff and select relevant context:
# Pseudo-code for smart context selection
def select_relevant_context(diff, pr_files, graph):
    changed_symbols = extract_changed_symbols(diff)  # functions/classes modified
    affected_files = extract_affected_files(diff)      # files with changes
    
    # What's directly impacted (in PR)
    direct = {
        "changed_functions": get_definitions(changed_symbols),
        "changed_files": pr_files,
        "imports": get_pr_imports(affected_files),
    }
    
    # What's indirectly impacted (callers/dependencies)
    indirect = {
        "callers": get_callers_of(changed_symbols),
        "dependents": get_dependents(changed_symbols),
    }
    
    # Score by relevance (PR changes > callers > dependents)
    return prioritize(direct, indirect)
Pass 2: Focused Explorer with Goals
Instead of dumping all context, give the Explorer specific goals:
EXPLORER_GOALS = """
## PR-Specific Investigation Goals
Changed files: {files_changed}
Changed functions: {changed_fn_names}
Your job:
1. For each changed function, find its definition + docstring
2. For each changed function, find WHO calls it (callers)
3. For each changed function, find WHAT it calls (dependencies)
4. Check for security issues in: {risky_patterns}
5. Check for regressions in: {impacted_areas}
"""
Pass 3: Streaming Synthesis
For huge PRs, don't dump everything at once - stream iteratively:
async def review_large_pr(diff, files):
    chunks = split_into_chunks(diff, max_tokens=30_000)
    
    for chunk in chunks:
        # Explorer + Review for this chunk
        findings = await run_explorer_and_review(chunk)
        all_findings.append(findings)
    
    # Final deduplication + synthesis
    return deduplicate_and_synthesize(all_findings)
---
Robustness Improvements
1. Hierarchical Context Loading
Level 1 (always): Changed function definitions + their immediate callers
Level 2 (if needed): Dependencies, broader caller tree  
Level 3 (on demand): Full file contents, imports, graph relationships
Trigger next level only when Explorer explicitly asks for it.
2. Chunked Processing
CHUNK_STRATEGY = {
    "small": "< 50 changes → single pass",
    "medium": "50-200 changes → 2 passes", 
    "large": "200+ changes → chunk by file + aggregate"
}
3. Exploration Budget Allocation
# Instead of fixed 10 rounds, allocate based on PR size
budget = {
    "changed_files": len(files_changed) * 2,  # 2 rounds per file
    "critical_symbols": 10,                     # high-value targets
    "remaining": max(0, 20 - allocated)
}
4. Caching for Repeat Queries
If same file is peeked 3x → fetch full file source once
If same function callers queried → cache result
5. Timeout + Progress Tracking
async with asyncio.timeout(300):  # 5 min max
    result = await run_review()
    
# If timeout, return partial results with clear disclaimer
---
Architecture Comparison
Aspect	Current	Recommended
Context loading	All at once (prompt dump)	Progressive/hierarchical
Explorer guidance	None (blind exploration)	Goal-directed with priorities
Tool budget	Fixed 10 rounds	Adaptive based on PR size
Large PR handling	Truncate silently	Chunk + stream + aggregate
Caching	None	Strategy-aware caching
Timeout	None	Hard timeout with partial results
---
Implementation Priority
1. P0: Add chunked processing for large diffs (>50 files or >60K chars)
2. P0: Implement hierarchical context loading (Levels 1/2/3)
3. P1: Add exploration goals to Explorer prompt
4. P1: Add timeout with graceful degradation
5. P2: Add caching layer for repeated queries
Would you like me to write a detailed spec or implementation for any of these?