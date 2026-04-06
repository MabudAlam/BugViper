# BugViper Code Review Architecture

## Overview

BugViper is an AI-powered code review system that ingests repositories into a Neo4j knowledge graph and uses LangGraph agents to review pull requests. It supports two review modes:

- **Incremental Review** (`@bugviper review`) - Validates previous issues and finds new ones
- **Full Review** (`@bugviper full review`) - Complete review from scratch

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              GitHub Webhook                                      │
│                    (PR comment: @bugviper review)                                │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           api/routers/webhook.py                                 │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │  on_comment()                                                               │ │
│  │  ├── Parse webhook payload                                                  │ │
│  │  ├── Extract command: review | full review                                 │ │
│  │  ├── Check if repo is indexed in Firebase                                   │ │
│  │  └── Dispatch to review_pipeline() via Cloud Tasks or BackgroundTasks      │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        api/services/review_service.py                            │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │  review_pipeline()                                                          │ │
│  │  ├── Fetch PR data (diff, files, ASTs, Neo4j samples)                      │ │
│  │  ├── Fetch previous issues from Firebase (incremental only)                 │ │
│  │  ├── Build file context for each changed file                               │ │
│  │  ├── Run 5-node LangGraph agent for each file (parallel)                    │ │
│  │  ├── Merge validated still_open issues with new issues                      │ │
│  │  ├── Update Firebase (mark fixed issues, save new run)                     │ │
│  │  └── Post GitHub review comments                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
┌─────────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│   Firebase Service      │ │   GitHub Client     │ │  5-Node LangGraph    │
│   - Get previous run    │ │   - Get PR diff     │ │  Agent (per file)    │
│   - Save review run     │ │   - Get file content│ │                     │
│   - Update fixed issues │ │   - Post comments   │ │  ┌───────────────┐   │
└─────────────────────────┘ │   - Post review     │ │  │   Explorer    │   │
                            └─────────────────────┘ │  └───────┬───────┘   │
                                                      │          │           │
                                ┌─────────────────────┐ │  ┌───────▼───────┐   │
                                │   Neo4j Graph      │ │  │     Tools     │   │
                                │   - Code search    │ │  └───────┬───────┘   │
                                │   - AST traversal  │ │          │           │
                                │   - Symbol lookup  │ │  ┌───────▼───────┐   │
                                └─────────────────────┘ │  │ Extract Srcs  │   │
                                                      │  └───────┬───────┘   │
                                                      │          │           │
                                                      │  ┌───────▼───────┐   │
                                                      │  │   Validator   │   │
                                                      │  │   (AI-based)  │   │
                                                      │  └───────┬───────┘   │
                                                      │          │           │
                                                      │  ┌───────▼───────┐   │
                                                      │  │   Reviewer    │   │
                                                      │  └───────┬───────┘   │
                                                      │          │           │
                                                      │  ┌───────▼───────┐   │
                                                      │  │  Summarizer   │   │
                                                      │  └───────────────┘   │
                                                      └─────────────────────┘
```

---

## 5-Node LangGraph Agent

The core review logic is a LangGraph agent with 5 nodes that process each file:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         5-Node LangGraph Agent Flow                              │
│                                                                                  │
│  ┌──────────┐     ┌──────────┐     ┌───────────────┐                            │
│  │ Explorer │────▶│  Tools   │────▶│Extract Sources│                            │
│  └────┬─────┘     └────┬─────┘     └───────┬───────┘                            │
│       │                │                   │                                     │
│       │    ┌───────────┘                   │                                     │
│       │    │                               │                                     │
│       │    │      ┌────────────────────────┘                                     │
│       │    │      │                                                              │
│       │    ▼      │                                                              │
│       │  More     │         ┌─────────────┐                                      │
│       │  Rounds?  ├────────▶│  Validator  │                                      │
│       │           │  No     │  (AI-based) │                                      │
│       │           │         └──────┬──────┘                                      │
│       │           │                │                                              │
│       ▼           │                ▼                                              │
│  ┌────MAX_ROUNDS──┤         ┌─────────────┐                                      │
│  │    reached     │         │  Reviewer   │                                      │
│  │    (exit loop) │         └──────┬──────┘                                      │
│  └────────────────┘                │                                              │
│                                      ▼                                              │
│                               ┌─────────────┐                                      │
│                               │ Summarizer  │                                      │
│                               └─────────────┘                                      │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Node Responsibilities

| Node | Purpose | Input | Output |
|------|---------|-------|--------|
| **Explorer** | Investigates code using tools | File context + message history | Tool calls or final analysis |
| **Tools** | Executes tool calls | Tool name + args | Tool results + tool artifacts |
| **Extract Sources** | Collects tool artifacts | Tool messages | Source references |
| **Validator** | AI validates previous issues against current code | Previous issues + file content | Validated issues with status |
| **Reviewer** | Finds NEW issues and positives | Exploration results + validated issues | Structured issues + positives |
| **Summarizer** | Generates file walkthrough | Exploration results + issues | File summaries |

### Explorer Tools (19 available)

The Explorer has access to code intelligence tools that query the Neo4j graph:

| Tool | Purpose |
|------|---------|
| `find_function(name)` | Find function definition by name |
| `find_class(name)` | Find class definition by name |
| `find_variable(name)` | Find variable/constant definition |
| `find_imports(name)` | Find all import chains |
| `find_callers(symbol)` | Find all callers of a function/class |
| `find_method_usages(method)` | Find all method call sites |
| `search_code(query)` | Broad code search |
| `peek_code(path, line)` | Read source at specific location |
| `find_by_content(query)` | Search code bodies for pattern |
| `find_by_line(query)` | Search raw file lines |
| `find_module(name)` | Get module/package info |
| `semantic_search(question)` | Search by meaning/intent |

---

## Review Types

### Incremental Review (`@bugviper review`)

Validates previous issues and finds new ones:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        Incremental Review Flow                                   │
│                                                                                  │
│  1. Fetch previous review run from Firebase                                     │
│     └── Get issues from last run for this PR                                    │
│                                                                                  │
│  2. For each file:                                                              │
│     ┌──────────────────────────────────────────────────────────────────────┐   │
│     │  Build Context                                                         │   │
│     │  ├── POST-PR file content with line numbers                          │   │
│     │  ├── Unified diff                                                     │   │
│     │  ├── AST summary (functions, classes, imports, call sites)           │   │
│     │  ├── Previous issues for this file                                    │   │
│     │  └── Neo4j code samples (definitions, callers)                       │   │
│     └──────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│     ┌──────────────────────────────────────────────────────────────────────┐   │
│     │  Run 5-Node Agent                                                      │   │
│     │  ├── Explorer: Investigate missing symbols, callers, dependencies     │   │
│     │  ├── Validator: AI checks if previous issues still exist             │   │
│     │  │   ├── still_open    → Issue persists in current code              │   │
│     │  │   ├── fixed         → Issue was resolved                          │   │
│     │  │   └── partially_fixed → Some improvement but not fully resolved  │   │
│     │  ├── Reviewer: Find NEW issues only                                   │   │
│     │  └── Summarizer: Generate file walkthrough                             │   │
│     └──────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  3. Merge results:                                                              │
│     ├── still_open issues from Validator (status: "still_open")               │
│     └── NEW issues from Reviewer (status: "new")                               │
│                                                                                  │
│  4. Update Firebase:                                                            │
│     ├── Mark fixed issues in previous run                                      │
│     ├── Save new review run with all issues                                    │
│     └── Update openIssueCount for PR                                           │
│                                                                                  │
│  5. Post GitHub comments                                                        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Full Review (`@bugviper full review`)

Complete review from scratch:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Full Review Flow                                      │
│                                                                                  │
│  1. Do NOT fetch previous issues (previous_issues = [])                        │
│                                                                                  │
│  2. For each file:                                                              │
│     ├── Validator receives empty list → returns empty validatied_issues        │
│     ├── Reviewer finds ALL issues as new                                       │
│     └── All issues have status: "new"                                           │
│                                                                                  │
│  3. Save new review run (replaces previous)                                    │
│                                                                                  │
│  4. Post GitHub comments                                                        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### Issue Status

| Status | Description | Included in Output |
|--------|-------------|-------------------|
| `new` | Issue found in this review | Yes (confidence ≥ 7) |
| `still_open` | Previous issue not fixed | Yes |
| `fixed` | Previous issue resolved | No (updated in Firebase) |
| `partially_fixed` | Some improvement made | Yes |

### Issue Structure

```json
{
  "issue_type": "Bug",
  "category": "bug",
  "severity": "high",
  "title": "Null pointer dereference on malformed input",
  "file": "path/to/file.py",
  "line_start": 42,
  "line_end": 42,
  "description": "The code accesses user.name without checking if user is None.",
  "suggestion": "Add a None check before accessing user attributes.",
  "impact": "Runtime crash when API returns unexpected empty response",
  "code_snippet": "user = fetch_user(user_id)\nprocess_name(user.name)",
  "confidence": 9,
  "status": "new"
}
```

---

## Key Files

### API Layer

| File | Purpose |
|------|---------|
| `api/routers/webhook.py` | GitHub webhook handler, dispatches to review_pipeline |
| `api/services/review_service.py` | Main orchestration: fetches data, runs agents, posts results |
| `api/services/context_builder.py` | Builds markdown context for each file |
| `api/services/firebase_service.py` | Firebase CRUD for review runs |

### Agent Layer

| File | Purpose |
|------|---------|
| `code_review_agent/agent_executor.py` | Entry point for running 5-node agent |
| `code_review_agent/nagent/ngraph.py` | LangGraph graph definition |
| `code_review_agent/nagent/nstate.py` | State models (Pydantic) |
| `code_review_agent/nagent/nprompt.py` | System prompts for each node |
| `code_review_agent/nagent/ntools.py` | 19 code exploration tools |

### Models

| File | Purpose |
|------|---------|
| `code_review_agent/models/agent_schemas.py` | Issue, ReconciledReview, ContextData models |
| `common/firebase_models.py` | PRMetadata, ReviewRunData models |

---

## Configuration

### Environment Variables

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password

# LLM
OPENROUTER_API_KEY=your-key
REVIEW_MODEL=anthropic/claude-sonnet-4-5

# GitHub
GITHUB_APP_ID=your-app-id
GITHUB_PRIVATE_KEY_PATH=/path/to/key.pem
GITHUB_WEBHOOK_SECRET=your-secret

# Firebase
SERVICE_FILE_LOC=/path/to/service-account.json

# Agent
MAX_TOOL_ROUNDS=8  # Max tool calls per file
```

### Agent Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_TOOL_ROUNDS` | 8 | Maximum tool calls in Explorer loop |
| `REVIEW_MODEL` | `anthropic/claude-sonnet-4-5` | LLM for all nodes |

---

## Output Files

Debug files are written to `output/review-{timestamp}/`:

| File | Description |
|------|-------------|
| `01_diff.md` | Raw PR diff |
| `02_parsed_files.json` | AST-parsed file structures |
| `04_review_prompt_{file}.md` | Full context fed to agent |
| `05_agent_output_{file}.md` | Agent execution trace |
| `00_diff_parsing_debug.json` | Valid comment lines, hunk ranges |

---

## Example Flow

### User Triggers Review

```bash
# PR Comment:
@bugviper review
```

### Webhook Processing

1. `webhook.py` receives `issue_comment` event
2. Extracts command → `ReviewType.INCREMENTAL_REVIEW`
3. Dispatches to `review_pipeline()`

### Review Execution

```python
# review_pipeline() does:
1. Fetch PR diff, files, head SHA
2. Parse ASTs for each file
3. Fetch Neo4j code samples (classes, functions, imports)
4. Fetch previous issues from Firebase (incremental only)
5. For each file, in parallel:
   - Build markdown context
   - Run 5-node agent
   - Collect issues + validated issues
6. Merge: still_open issues + new issues
7. Update Firebase (mark fixed, save run)
8. Post GitHub PR review
```

### Agent Output

```json
{
  "file_based_issues": [
    {
      "file": "app/model/agent_model.py",
      "issues": [
        {
          "issue_type": "Bug",
          "category": "bug",
          "severity": "high",
          "title": "Unresolved reference in get_limit method",
          "status": "new",
          "confidence": 9
        }
      ]
    }
  ],
  "validated_previous_issues": [
    {
      "title": "Missing input validation",
      "status": "fixed",
      "confidence": 10
    }
  ]
}
```

---

## Error Handling

| Error | Handling |
|-------|----------|
| Agent fails mid-execution | Log error, return empty results, continue with other files |
| Firebase fetch fails | Continue with empty previous issues |
| GitHub API rate limit | Retry with exponential backoff |
| LLM timeout | Return partial results, mark file as failed |

---

## Performance

| Metric | Value |
|--------|-------|
| Files reviewed in parallel | All changed files |
| Tool rounds per file | 5-8 (bounded by MAX_ROUNDS) |
| Cost per file | ~$0.10-0.15 with claude-sonnet-4-5 |
| Tokens per file | ~6,500 input + output |
| Review time | ~30-60 seconds per file |

---

## Future Improvements

1. **Caching** - Cache Neo4j query results across files
2. **Streaming** - Stream results as files complete
3. **Batching** - Batch LLM calls for multiple files
4. **Incremental Graph** - Only update changed files in Neo4j
5. **Feedback Loop** - Learn from user feedback on false positives