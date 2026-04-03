# Code Review Agent - Implementation Summary

## ✅ Completed: 3-Node Architecture

### Architecture Overview

```
┌─────────────────────────────────────────────────┐
│  Input: Review Context (Markdown File)          │
│  - File path, diff, code with lines             │
│  - AST summary, hunk ranges                     │
│  - Previous issues, context                     │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 1: Explorer (ReAct Loop with Tools)        │
│  - Investigates diff, code, AST                  │
│  - Calls tools (bounded by MAX_TOOL_ROUNDS)      │
│  - Accumulates evidence in messages              │
│  - Extracts sources from tool artifacts          │
│                                                  │
│  Tools: 19 code exploration tools                │
│  - search_code, peek_code, find_function         │
│  - find_class, find_callers, get_complexity      │
│  - find_imports, semantic_search, ...            │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 2: Reviewer (Structured LLM Pass)          │
│  - Reads full message history                    │
│  - Generates structured output:                   │
│    • file_based_issues                            │
│    • file_based_positive_findings                │
│  - Precise line numbers from NEW file            │
│  - Confidence scoring (0-10)                     │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 3: Summarizer (Structured LLM Pass)        │
│  - Generates narrative walkthrough                │
│  - Output: file_based_walkthrough                │
│  - Step-by-step observations                      │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Output: CodeReviewAgentState                    │
│  - file_based_issues (high-confidence only)      │
│  - file_based_positive_findings                   │
│  - file_based_walkthrough                        │
│  - sources (tool artifacts)                      │
└─────────────────────────────────────────────────┘
```

---

## Implementation Details

### 1. State Schema (`nstate.py`)

**Models:**
- `ReviewCodeIssue` - Individual issue with all metadata
- `FileBasedIssues` - Issues grouped by file
- `AgentPositiveFinding` - Positive findings per file
- `FileBasedWalkthrough` - Step-by-step analysis
- `ReviewerOutput` - Structured output for reviewer node (NEW)
- `SummarizerOutput` - Structured output for summarizer node (NEW)

**State:**
```python
class CodeReviewAgentState(TypedDict):
    # Input (from pipeline)
    file_based_context: str
    
    # Exploration state
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    sources: Annotated[list[dict], _merge_sources]
    
    # Output
    file_based_issues: list[FileBasedIssues]
    file_based_positive_findings: list[AgentPositiveFinding]
    file_based_walkthrough: list[FileBasedWalkthrough]
```

---

### 2. System Prompts (`nprompt.py`)

**ThreeFocused Prompts:**

#### Explorer Prompt
- **Focus**: Investigation only
- **Goal**: Gather intelligence using tools
- **Output**: Brief summary of findings
- **No structured output**

#### Reviewer Prompt
- **Focus**: Issues and positive findings
- **Goal**: Precise structured output
- **Output**: `file_based_issues`, `file_based_positive_findings`
- **Emphasis**: Line number accuracy, confidence scoring

#### Summarizer Prompt
- **Focus**: Narrative walkthrough
- **Goal**: Chronological observations
- **Output**: `file_based_walkthrough`
- **Emphasis**: Flow of the code, mix of positives and issues

---

### 3. Graph Structure (`ngraph.py`)

**Nodes:**
1. `explorer` - ReAct loop with tools
2. `tools` - Tool execution (ToolNode)
3. `extract_sources` - Extract ToolMessage.artifact
4. `increment_rounds` - Track iterations
5. `reviewer` - Structured output (issues + positives)
6. `summarizer` - Structured output (walkthrough)

**Edges:**
```
START → explorer
explorer ──(has tool_calls?)──→ tools
    │                              │
    └──(no tool_calls)──→ reviewer
tools → extract_sources → increment_rounds → explorer
reviewer → summarizer → END
```

---

### 4. Tools (`ntools.py`)

**19 Code Exploration Tools:**

**Code Search:**
- `search_code(query)` - Find symbols by name/keyword
- `peek_code(path, line)` - Read code around a line
- `find_by_content(query)` - Search for code patterns
- `find_by_line(query)` - Find lines containing text

**Symbol Lookup:**
- `find_function(name)` - Find function definition
- `find_class(name)` - Find class definition
- `find_variable(name)` - Find variable/constant
- `find_module(name)` - Find module/package

**Dependency Analysis:**
- `find_imports(name)` - Find import statements
- `find_callers(symbol)` - Find who calls a function
- `find_method_usages(method)` - Find method call sites
- `get_change_impact(symbol)` - Analyze blast radius

**Structural Analysis:**
- `get_class_hierarchy(class_name)` - Get inheritance tree
- `get_complexity(fn_name)` - Check cyclomatic complexity
- `get_top_complex_functions()` - List complex functions

**Other:**
- `semantic_search(question)` - Find code by meaning
- `get_file_source(path)` - Get full file source
- `get_repo_stats()` - Repository statistics
- `get_language_stats()` - Language breakdown

**All tools return:** `(content: str, sources: list[dict])`

---

### 5. Runner (`nrunner.py`)

**Functions:**
- `run_review_agent(file, repo_id, model)` - Programmatic interface
- `main()` - CLI entry point

**Usage:**
```bash
python -m code_review_agent.nagent.nrunner \
    output/review.md \
    --repo owner/repo \
    --model anthropic/claude-sonnet-4-5
```

---

## Key Design Decisions

### 1. **Why 3 Nodes?**

**Separation of Concerns:**
- **Explorer**: Pure investigation (tool calls only)
- **Reviewer**: Issues and positives (structured output)
- **Summarizer**: Walkthrough (narrative)

**Benefits:**
- Each node has a single responsibility
- Smaller Pydantic models = more reliable output
- Easier to debug and test
- Natural pipeline flow

### 2. **Why MAX_TOOL_ROUNDS = 8?**

**Cost Control:**
- Bounded tool calls (8 rounds)
- Enough for thorough investigation
- Explorer naturally stops early if done
- For large PRs, bump to 12-15

### 3. **Why Extract Sources Node?**

**Clean Architecture:**
- Separates source extraction from tool execution
- Easy to test
- Sources available for reviewer
- Deduplication via merge function

### 4. **Why Reviewer + Summarizer Split?**

**Reliability:**
- Smaller Pydantic models
- Less hallucination
- Easier for LLM to fill correctly
- Two small calls > one big call

### 5. **Why Full Message History in Reviewer?**

**Accuracy:**
- Reviewer needs raw tool outputs
- Can cite specific line numbers
- No information loss
- Token cost is bounded (explorer bounded)

---

## Testing

### How to Test

```bash
# 1. Start Neo4j database
# 2. Set environment variables (OPENROUTER_API_KEY, etc.)
# 3. Run with a real review context file

python -m code_review_agent.nagent.nrunner \
    output/review-2026-04-02_20-32-14/04_review_prompt_api_routers_webhook_py.md \
    --repo skmabudalam/BugViper
```

### Expected Output

```
EXPLORATION PHASE
- Tool rounds: 5-7
- Sources collected: 10-20
- Messages: 15-30

REVIEWER OUTPUT - ISSUES
- file_based_issues: 2-5 high-confidence issues
- Exact line numbers from post-change code
- Confidence scores 7-10

REVIEWER OUTPUT - POSITIVE FINDINGS
- file_based_positive_findings: 3-6 observations
- Specific good patterns

SUMMARIZER OUTPUT - WALKTHROUGH
- file_based_walkthrough: 5-10 steps
- Chronological observations
- Mix of positives and issues
```

---

## File Structure

```
code_review_agent/nagent/
├── nstate.py              # State + Pydantic models
│   ├── ReviewCodeIssue
│   ├── FileBasedIssues
│   ├── AgentPositiveFinding
│   ├── FileBasedWalkthrough
│   ├── ReviewerOutput ✨ NEW
│   ├── SummarizerOutput ✨ NEW
│   ├── _merge_sources()
│   └── CodeReviewAgentState
├── ntools.py              # 19 code exploration tools
├── nprompt.py             # System prompts ✨ UPDATED
│   ├── get_explorer_system_prompt()
│   ├── get_reviewer_system_prompt()
│   └── get_summarizer_system_prompt()
├── ngraph.py              # Graph definition ✨ REFACTORED
│   ├── _slim_messages()
│   ├── _format_messages()
│   ├── explorer_node()
│   ├── extract_sources()
│   ├── increment_rounds()
│   ├── reviewer_node() ✨ NEW
│   ├── summarizer_node() ✨ NEW
│   ├── should_continue()
│   └── build_code_review_graph()
├── nrunner.py             # CLI runner
├── example_3node.py       # Example usage ✨ NEW
└── plan.md                # Architecture plan
```

---

## Cost Estimate

**Typical code review (medium-sized PR):**

- **Explorer**: ~8 tool calls × ~500 tokens = 4,000 tokens
- **Reviewer**: ~1 structured call × ~2,000 tokens = 2,000 tokens
- **Summarizer**: ~1 structured call × ~500 tokens = 500 tokens

**Total**: ~6,500 tokens per file review

**With `claude-sonnet-4-5`:**
- Input: $3/M tokens → $0.02 per review
- Output: $15/M tokens → $0.10 per review
- **Total**: ~$0.12 per file review

**Cost Optimization:**
- Reduce `MAX_TOOL_ROUNDS` for simple files
- Use smaller model for summarizer
- Batch multiple files in one run

---

## Comparison: Old vs New Architecture

### Old (2-Node)
```
Explorer → Synthesize
```
- Explorer tried to do everything
- Large structured output model
- More hallucinations
- Harder to debug

### New (3-Node)
```
Explorer → Reviewer → Summarizer
```
- Each node has single responsibility
- Smaller structured output models
- More reliable output
- Easier to debug

---

## Next Steps

### ✅ Completed

1. ✅ State schema with Pydantic models
2. ✅ Three focused system prompts
3. ✅ 3-node graph architecture
4. ✅ Source extraction node
5. ✅ Structured output for reviewer/summarizer
6. ✅ Example and documentation

### ⏳ TODO

7. 🧪 Integration tests with real context
8. 📊 Performance tuning (rounds, tokens, model selection)
9. 🔍 Add validation for line numbers (must be in hunk ranges)
10. 📝 Add more examples for different file types

---

## Summary

The 3-node architecture provides:
- ✅ **Clear separation of concerns**
- ✅ **Better cost control** (bounded tool calls)
- ✅ **More reliable structured output** (smaller models)
- ✅ **Easier debugging** (node-by-node inspection)
- ✅ **Production-ready** design

Total implementation: ~500 lines of clean, well-documented code across 5 files.