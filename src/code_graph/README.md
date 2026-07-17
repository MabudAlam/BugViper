# code_graph — Code Graph Generator

Extracts a **call graph** from a git repository and slices it to a **pull request's changed files**. The result is a per-file map of function calls, dependencies, and blast radius that code review agents use to trace cross-file impact.

---

## How it works

```
┌─────────────┐    ┌───────────┐    ┌──────────────┐    ┌────────────────┐
│ Clone repo  │───→│ Parse all │───→│ Build graph  │───→│ Extract PR     │
│ at PR head  │    │ source    │    │ nodes+edges  │    │ subgraph       │
└─────────────┘    │ files     │    └──────────────┘    │ (by diff files)│
                   └───────────┘                        └────────────────┘
                                                               │
                                                          ┌────▼────┐
                                                          │ blast   │
                                                          │ radius  │
                                                          │ markdown│
                                                          └─────────┘
```

**Pipeline:**

1. **Clone** — checks out the repo at the PR's head SHA
2. **Parse** — walks the repo, parses every source file with tree-sitter (17 languages supported)
3. **Graph** — builds nodes (File, Class, Function, Module) and edges (CALLS, IMPORTS, DEFINED_IN, BELONGS_TO, DEPENDS_ON, INHERITS_FROM)
4. **Extract** — given a list of changed files, filters the graph to only the PR's scope, classifying edges as `internal` (both ends in PR), `incoming` (external → PR), or `outgoing` (PR → external)
5. **Blast radius** — renders a markdown summary per file: which functions call what, who calls them, impact level

---

## Files

| Module | Purpose |
|--------|---------|
| `parser.py` | Tree-sitter parser for 17 languages (Python, JS, TS, Go, Rust, Java, Ruby, C++, C#, Svelte, Dart, PHP, Swift, Kotlin, Scala, Haskell, C). Falls back to regex when tree-sitter grammar is unavailable. |
| `graph_builder.py` | Builds a graph from parsed files: typed nodes (File, Class, Function, Module) and edges with call frequency counts. |
| `pr_extractor.py` | Takes the full graph + list of changed files → produces PR-scoped call graph with `internal_calls`, `incoming_calls`, `outgoing_calls` per file. |
| `blast_radius.py` | Renders the per-file markdown summary for agents. |
| `utils.py` | Git helpers: `changed_files_from_diff()`, `clone_with_token()`. |
| `run_pr_graph.py` | Standalone runner — clone a PR, generate code graph + PR graph + blast radius. |

---

## Install

The package is part of BugViper. Dependencies include `tree-sitter` and language grammars:

```bash
uv sync
```

Tree-sitter grammars installed: Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, C++, C#, Svelte, Dart, PHP, Swift, Kotlin, Scala, Haskell, C.
Missing grammars fall back to regex parsing (no crash).

---

## Usage

### CLI — generate for any PR

```bash
# Generate code graph + PR graph + blast radius
uv run python src/code_graph/run_pr_graph.py "https://github.com/owner/repo/pull/123"

# Specify output directory
uv run python src/code_graph/run_pr_graph.py "https://github.com/owner/repo/pull/123" my_output

# Or as a module
uv run python -m code_graph.run_pr_graph "https://github.com/owner/repo/pull/123"
```

Requires `gh` CLI authenticated and `uv sync` run first. Outputs:

```
output/
├── code_graph.json        # full repo graph (nodes + edges)
├── pr_graph.json          # PR-scoped call graph (per_file edges)
├── blast_radius.md        # per-file function-level markdown
├── diff.patch             # PR diff
└── batches/               # per-batch filtered artifacts
    ├── batch_00/
    │   ├── pr_graph.json
    │   ├── blast_radius.md
    │   └── files.txt
    ├── batch_01/
    └── ...
```

### Library — import and use in your own code

```python
from code_graph import (
    parse_source_files,
    build_graph,
    extract_pr_call_graph,
    render_blast_radius_markdown,
)
from code_graph.utils import clone_with_token, changed_files_from_diff

# 1. Clone repo
clone_with_token(token, "owner/repo", sha, clone_path)

# 2. Parse all source files
files, parsed = parse_source_files(str(clone_path))

# 3. Build full code graph
graph = build_graph(str(clone_path), files, parsed)

# 4. Extract PR subgraph from changed files
changed = changed_files_from_diff(diff_text)
pr_graph = extract_pr_call_graph(graph, changed)

# 5. Render markdown
blast_radius = render_blast_radius_markdown(pr_graph)
```

### Extract just the call relationships for a PR

```python
pr_graph = extract_pr_call_graph(full_graph, changed_files)

# Per-file data:
for file_path, data in pr_graph["per_file"].items():
    internal = data["internal_calls"]   # both ends in PR
    incoming = data["incoming_calls"]   # external → PR
    outgoing = data["outgoing_calls"]   # PR → external
    imports  = data["imports"]          # resolved import paths
```

---

## Output format

### `code_graph.json` — full repo graph

```json
{
  "nodes": [
    { "id": "fn_parseFile_src_parser_py", "type": "Function", "label": "parseFile",
      "file": "src/parser.py", "lineStart": 66, "lineEnd": 80, "language": "python" },
    { "id": "file_src_parser_py", "type": "File", "label": "src/parser.py",
      "language": "python", "lines": 278 }
  ],
  "edges": [
    { "source": "fn_parseFile_...", "target": "file_src_parser_py", "type": "DEFINED_IN" },
    { "source": "fn_parseFile_...", "target": "fn_extractCalls_...", "type": "CALLS" }
  ]
}
```

### `pr_graph.json` — PR-scoped call graph

```json
{
  "pr_files": ["src/api.ts", "src/db.ts"],
  "total_pr_files": 2,
  "per_file": {
    "src/api.ts": {
      "imports": [{ "local_name": "db", "source": "./db", "resolved_file": "src/db.ts" }],
      "internal_calls": [{ "caller_scope": "getUser", "callee": "query", "callee_file": "src/db.ts" }],
      "incoming_calls": [],
      "outgoing_calls": [{ "caller_scope": "getUser", "callee": "sendEmail", "callee_file": "src/email.ts" }]
    }
  },
  "summary": { "internal_calls": 5, "incoming_calls": 2, "outgoing_calls": 3 }
}
```

---

## Supported languages

`.py` `.js` `.ts` `.tsx` `.jsx` `.go` `.rs` `.dart` `.java` `.rb` `.php` `.c` `.h` `.cpp` `.hpp` `.cc` `.cxx` `.cs` `.kt` `.kts` `.scala` `.swift` `.hs` `.svelte`

---

## Batching

For PRs with many files, call-graph-based batching groups related files together. See `ai_code_review/batch.py` for the Louvain community detection + score-based packing into batches of 8 (normal mode) or 20 (deep mode).
