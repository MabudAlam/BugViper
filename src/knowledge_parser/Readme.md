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
  "pr_files": ["src/a.ts", "src/b.ts"],
  "total_pr_files": 2,
  "per_file": {
    "src/a.ts": {
      "imports": [
        {
          "local_name": "store",
          "original_name": "store",
          "source": "./store",
          "resolved_file": "src/store.ts",
          "resolved": true,
          "line_number": 3
        }
      ],
      "internal_calls": [
        {
          "caller_file": "src/a.ts",
          "caller_function": "handleClick",
          "caller_scope": "Button.handleClick",
          "caller_line": 12,
          "callee": "submit",
          "callee_qualified": "submit",
          "callee_file": "src/b.ts",
          "callee_line": 25,
          "resolution": "imported",
          "receiver": null,
          "is_method": false,
          "call_sites": [
            {"line": 18, "call": "submit(data)", "args": ["data"]}
          ],
          "frequency": 1
        }
      ],
      "incoming_calls": [
        {
          "caller_file": "src/legacy.ts",
          "caller_function": "onSave",
          "caller_scope": "onSave",
          "caller_line": 40,
          "callee": "render",
          "callee_qualified": "Button.render",
          "callee_file": "src/a.ts",
          "callee_line": 5,
          "resolution": "this_method",
          "receiver": "this",
          "is_method": true,
          "call_sites": [
            {"line": 47, "call": "this.render()", "args": []}
          ],
          "frequency": 1
        }
      ]
    }
  },
  "summary": {
    "internal_calls": 11,
    "incoming_calls": 7,
    "total_call_sites_in_repo": 412,
    "total_files_in_repo": 87,
    "resolution_breakdown": {
      "local": 6,
      "imported": 3,
      "imported_method": 2,
      "this_method": 1,
      "builtin": 412
    }
  }
}
```

#### Field Reference

| Field | Meaning |
|-------|---------|
| `caller_file` | File containing the call site |
| `caller_function` | Function/method name where the call originates (AST context) |
| `caller_scope` | Qualified caller name (e.g. `ClassName.methodName`) |
| `caller_line` | Line where the caller is defined |
| `callee` | Method/function name as called |
| `callee_qualified` | Fully-qualified callee name |
| `callee_file` | Resolved file containing the callee definition |
| `callee_line` | Line where the callee is defined |
| `resolution` | How the callee was resolved (see below) |
| `receiver` | Object on which the method was called (e.g. `store.update` -> `store`) |
| `is_method` | True for `obj.method()` calls |
| `call_sites` | Up to 5 sample call sites with line, full expression, args |
| `frequency` | Total number of times this caller invokes this callee |

#### Resolution Types

The algorithm only emits edges when the callee can be proven to point at a
top-level definition in our own source files. Library calls, runtime
builtins, and bare-name collisions across files are dropped.

| Resolution | When |
|------------|------|
| `local` | Callee is defined in the same file as the caller |
| `imported` | Callee is reached through a named import |
| `imported_method` | Method call on an imported object's class method |
| `this_method` | `this.method()` resolved to current class method |
| `local_method` | Method call on a locally constructed class |
| `local_constructor` | Receiver matches a class declared in the same file |
| `builtin` | `console.log`, `window.x`, `crypto.createHash().update`, etc. |
| `external_or_builtin` | Could not resolve - likely an external dependency |

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
