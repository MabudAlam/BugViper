# Knowledge Parser

AST-based code analysis for generating repository knowledge graphs.

A lot of the language based parsers are taken from https://github.com/CodeGraphContext/CodeGraphContext/tree/main/src/codegraphcontext/tools/languages . I want to thank the creator for their work.

Repo : https://github.com/CodeGraphContext/CodeGraphContext

## What it does

Parses source code repositories using Tree-sitter, extracts structural information (functions, classes, imports, calls), and generates knowledge graphs for PR analysis.

## Components

| File | Purpose |
|------|---------|
| `knowledge_runner.py` | Main entry point - clone repo, generate AST, analyze PR call graph |
| `parser.py` | TreeSitterParser wrapper with language-specific parsing |
| `tree_sitter_manager.py` | Thread-safe language loading and caching |
| `registry.py` | File extension to language mapping |
| `parse.py` | Direct parse access (low-level) |

## Usage

Edit variables at top of `knowledge_runner.py`:

```python
REPO_URL = "https://github.com/owner/repo"
PR_NUMBER = 123
OUTPUT_DIR = "./output"
```

Run:
```bash
python -m knowledge_parser.knowledge_runner
```

## Output Files

### ast.json

Full repository AST with all files, functions, classes, imports, and calls.

```json
{
  "repo": "owner/repo",
  "owner": "owner",
  "repo_name": "repo",
  "files": [
    {
      "path": "src/main.py",
      "language": "python",
      "functions": [
        {
          "name": "handle_request",
          "line_number": 10,
          "end_line": 25,
          "args": ["payload"],
          "cyclomatic_complexity": 3,
          "context": null,
          "decorators": []
        }
      ],
      "classes": [
        {
          "name": "Service",
          "line_number": 1,
          "end_line": 50,
          "bases": ["BaseClass"]
        }
      ],
      "imports": [
        {
          "name": "database",
          "full_import_name": "database",
          "line_number": 5
        }
      ],
      "function_calls": [
        {
          "name": "save",
          "full_name": "database.save",
          "line_number": 15,
          "context": ["handle_request", "function_definition", 10]
        }
      ]
    }
  ],
  "statistics": {
    "files_parsed": 42,
    "files_skipped": 3,
    "functions_found": 156,
    "classes_found": 23,
    "imports_found": 89,
    "calls_found": 412
  },
  "errors": []
}
```

### call_graph.json

PR-focused call relationship analysis.

```json
{
  "pr_files": ["src/a.py", "src/b.py", "src/c.py"],
  "total_pr_files": 3,
  "per_file": {
    "src/a.py": {
      "internal_calls": [
        {
          "caller_file": "src/a.py",
          "caller_function": "func_a",
          "callee": "func_b",
          "callee_line": 15,
          "callee_file": "src/b.py",
          "full_name": "func_b"
        }
      ],
      "outgoing_calls": [
        {
          "caller_file": "src/a.py",
          "caller_function": "func_a",
          "callee": "external_func",
          "callee_line": 20,
          "callee_file": "lib/utils.py",
          "full_name": "utils.external_func"
        }
      ],
      "incoming_calls": [
        {
          "caller_file": "src/d.py",
          "caller_function": "caller_func",
          "callee": "func_a",
          "callee_line": 10,
          "callee_file": "src/a.py",
          "full_name": "func_a"
        }
      ]
    }
  },
  "summary": {
    "internal_calls": 11,
    "outgoing_calls": 287,
    "incoming_calls": 207
  }
}
```

## Call Types Explained

Given a PR with files `A.py`, `B.py`, `C.py`:

### Internal Calls
Calls where **both caller and callee are in PR files**.

```
A.py:func_a → B.py:func_b
```
Both in PR → internal call

### Outgoing Calls
Calls from **PR file to external code** (not in PR).

```
A.py:func_a → external_func (defined in lib/utils.py)
```
A.py is in PR, `external_func` is not → outgoing from PR

### Incoming Calls
Calls from **external code into PR files**.

```
external.py:caller → A.py:func_a
```
`external.py` not in PR, `func_a` in PR → incoming to PR

## Summary Statistics

| Field | Meaning |
|-------|---------|
| `internal_calls` | Calls between PR files (cross-file within PR) |
| `outgoing_calls` | Calls from PR to external/stdlib |
| `incoming_calls` | Calls from external code into PR files |

These help understand:
- **High outgoing** = PR code depends heavily on external dependencies
- **High incoming** = Changes may affect callers outside PR (blast radius)
- **High internal** = Self-contained refactoring with no external impact
