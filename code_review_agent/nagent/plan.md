# LangGraph Agent Implementation Plan (3-Node Architecture)

## Overview

**Production-grade 3-node ReAct graph** for code review:
- Separates concerns (investigation, review, summary)
- Cost-effective (bounded tool calls, single LLM passes for output)
- Reliable structured output (smaller Pydantic models)
- Follows LangGraph best practices

---

## Architecture

### 4-Node Linear Pipeline

```
┌─────────────────────────────────────────────────┐
│  Node 1: Context Builder (Pre-pipeline)          │
│  ✓ Already implemented in review_pipeline.py     │
│  Output: file_based_context (markdown string)    │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 2: Explorer (ReAct Loop with Tools)        │
│  - Investigates diff, code, AST                  │
│  - Calls tools (bounded by MAX_TOOL_ROUNDS)      │
│  - Accumulates evidence in messages              │
│  - Extracts sources from tool artifacts          │
│                                                  │
│  Tools: 19 code exploration tools                 │
│  - search_code, peek_code, find_function         │
│  - find_class, find_callers, get_complexity      │
│  - find_imports, semantic_search, ...            │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 3: Reviewer (Structured LLM Pass)          │
│  - Reads full message history                    │
│  - Generates structured output:                   │
│    • file_based_issues                            │
│    • file_based_positive_findings                │
│  - Precise line numbers from NEW file            │
│  - Confidence scoring (0-10)                     │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  Node 4: Summarizer (Structured LLM Pass)         │
│  - Generates narrative walkthrough                │
│  - Output: file_based_walkthrough                │
│  - Step-by-step observations                      │
└─────────────────────────────────────────────────┘
```

---

## Why This Architecture?

### 1. **Separation of Concerns**
- **Explorer**: Pure investigation (tool calls only)
- **Reviewer**: Issues and positives (structured output)
- **Summarizer**: Walkthrough (narrative)
- Each node has a single responsibility

### 2. **Cost Control**
- Explorer: Bounded by `MAX_TOOL_ROUNDS` (default: 8)
- Reviewer: Single structured LLM call
- Summarizer: Single structured LLM call
- No risk of infinite loops in output generation

### 3. **Reliable Structured Output**
- Smaller Pydantic models (easier for LLM to fill correctly)
- Less likely to hallucinate or miss fields
- Easier to validate each output separately

### 4. **Source Tracking**
- Extract sources from `ToolMessage.artifact`
- Accumulated throughout exploration
- Available for citation in reviewer

### 5. **Natural Pipeline Flow**
- Linear graph (no cycles between reviewer/summarizer)
- Clear state transitions
- Easy to debug and test

---

## State Schema

```python
class CodeReviewAgentState(TypedDict):
    # Input (from pipeline)
    file_based_context: str  # Raw markdown with diff, code, AST, etc.
    
    # Exploration state (explorer node)
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    sources: Annotated[list[dict], _merge_sources]  # Deduped by merge function
    
    # Output (filled by reviewer/summarizer nodes)
    file_based_issues: list[FileBasedIssues]
    file_based_positive_findings: list[AgentPositiveFinding]
    file_based_walkthrough: list[FileBasedWalkthrough]
```

---

## Pydantic Output Models

```python
# For Reviewer Node
class ReviewerOutput(BaseModel):
    file_based_issues: list[FileBasedIssues]
    file_based_positive_findings: list[AgentPositiveFinding]

# For Summarizer Node  
class SummarizerOutput(BaseModel):
    file_based_walkthrough: list[FileBasedWalkthrough]
```

**Why separate models?**
- Smaller output → more reliable
- Easier for LLM to fill correctly
- Less chance of hallucination
- Cheaper tokens

---

## Graph Structure

```
Nodes:
├── "explorer"          # ReAct loop with tools
├── "tools"             # ToolNode (prebuilt)
├── "extract_sources"   # Extract artifact sources from ToolMessages
├── "increment_rounds"  # Track tool call iterations
├── "reviewer"          # Structured output: issues + positives
└── "summarizer"        # Structured output: walkthrough

Edges:
START → explorer
explorer ──(has tool_calls & rounds < MAX?)──→ tools
    │                                               │
    └──(no tool_calls OR rounds >= MAX)──→ reviewer
tools → extract_sources
extract_sources → increment_rounds
increment_rounds → explorer
reviewer → summarizer → END
```

---

## Implementation Components

### 1. **Explorer Node** (`explorer_node`)

**Purpose**: Investigate code using tools (ReAct loop).

**Input**:
- `file_based_context` - diff, code, AST summary
- `messages` - conversation history
- `tool_rounds` - iteration counter

**Output**:
- AI message with tool calls OR text response (signals done)

**Logic**:
```python
def explorer_node(state: CodeReviewAgentState) -> dict:
    if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
        return {}  # Force exit to reviewer
    
    system_prompt = get_explorer_system_prompt(
        file_based_context=state["file_based_context"],
        system_time=datetime.now(tz=UTC).isoformat()
    )
    
    response = llm_with_tools.invoke([
        SystemMessage(system_prompt),
        *_slim_messages(state["messages"])
    ])
    
    return {"messages": [response]}
```

**Key Design**:
- Focus on *investigation*, not output
- Use tools to gather intelligence
- Don't try to generate structured output
- Stop when investigation complete or rounds exhausted

---

### 2. **Tool Node** (`tools`)

**Purpose**: Execute tool calls and return results.

**Implementation**: Use LangGraph's `ToolNode(tools)` from `langgraph.prebuilt`

**Input**: Tool calls from last AI message

**Output**: Tool messages with results (content + artifacts)

**Source Extraction**:
```python
# Tools return: (content: str, sources: list[dict])
# ToolNode stores sources in ToolMessage.artifact
```

---

### 3. **Extract Sources Node** (`extract_sources`)

**Purpose**: Extract sources from `ToolMessage.artifact` and merge into state.

```python
def extract_sources(state: CodeReviewAgentState) -> dict:
    """Extract sources from ToolMessage artifacts and merge into state."""
    new_sources = []
    for msg in state["messages"]:
        if hasattr(msg, "artifact") and isinstance(msg.artifact, list):
            new_sources.extend(msg.artifact)
    return {"sources": new_sources}  # _merge_sources will dedupe
```

**Why separate node?**
- Clean separation of concerns
- Easy to test
- Doesn't clutter the main flow

---

### 4. **Increment Rounds Node** (`increment_rounds`)

```python
def increment_rounds(state: CodeReviewAgentState) -> dict:
    return {"tool_rounds": state["tool_rounds"] + 1}
```

---

### 5. **Conditional Edge Router** (`should_continue`)

```python
def should_continue(state: CodeReviewAgentState) -> Literal["tools", "reviewer"]:
    last = state["messages"][-1]
    rounds = state["tool_rounds"]
    
    # Max rounds reached
    if rounds >= MAX_TOOL_ROUNDS:
        return "reviewer"
    
    # Has tool calls - continue exploring
    if isinstance(last, AIMessage) and last.tool_calls:
        # After 3+ rounds, check if we should stop early
        if rounds >= 3:
            content = str(last.content).lower() if last.content else ""
            if any(kw in content for kw in ["caller", "found", "definition", "complete"]):
                return "reviewer"
        return "tools"
    
    # No tool calls - done, move to reviewer
    return "reviewer"
```

---

### 6. **Reviewer Node** (`reviewer_node`)

**Purpose**: Generate structured output for issues and positive findings.

**Input**:
- Full message history (explorer's investigation)
- `file_based_context` (diff, code, AST)

**Output**:
- `file_based_issues`
- `file_based_positive_findings`

**Logic**:
```python
def reviewer_node(state: CodeReviewAgentState) -> dict:
    structured_llm = llm.with_structured_output(ReviewerOutput)
    
    prompt = get_reviewer_system_prompt(
        file_based_context=state["file_based_context"]
    )
    
    result = structured_llm.invoke([
        SystemMessage(prompt),
        *state["messages"]  # Full exploration history
    ])
    
    return {
        "file_based_issues": result.file_based_issues,
        "file_based_positive_findings": result.file_based_positive_findings
    }
```

**Key Design**:
- Single structured LLM call
- Smaller output model (`ReviewerOutput`)
- Focus on *precision* (exact line numbers)
- Confidence scoring (0-10, skip if < 5)
- AI fixes and agent prompts for each issue

---

### 7. **Summarizer Node** (`summarizer_node`)

**Purpose**: Generate narrative walkthrough.

**Input**:
- Message history
- `file_based_context`

**Output**:
- `file_based_walkthrough`

**Logic**:
```python
def summarizer_node(state: CodeReviewAgentState) -> dict:
    structured_llm = llm.with_structured_output(SummarizerOutput)
    
    prompt = get_summarizer_system_prompt(
        file_based_context=state["file_based_context"]
    )
    
    result = structured_llm.invoke([
        SystemMessage(prompt),
        *state["messages"]
    ])
    
    return {"file_based_walkthrough": result.file_based_walkthrough}
```

**Key Design**:
- Single structured LLM call
- Even smaller output model (`SummarizerOutput`)
- Focus on *narrative* (step-by-step)
- Chronological observations
- Mix of positives and issues

---

## System Prompts

### Explorer System Prompt

**File**: `nprompt.py`

**Focus**: *Investigation only, not output generation*

```
You are a senior code reviewer doing targeted investigation.

You have the diff context. Use tools to resolve unknowns:
- Trace cross-file dependencies with find_callers / find_method_usages
- Check complexity hotspots with get_complexity
- Peek at surrounding context with peek_code
- Use semantic_search when you're not sure what something does

Stop calling tools when you have enough signal to write a thorough review.

Do NOT generate structured output. Just investigate and report findings.
```

---

### Reviewer System Prompt

**File**: `nprompt.py`

**Focus**: *Issues and positive findings with precision*

```
You are writing the final code review.

Context (diff + AST + investigation):
{file_based_context}

Investigation findings (tool calls above):
{format_messages(messages)}

Produce:
1. file_based_issues (high confidence only)
2. file_based_positive_findings

Be precise about line numbers — use line_start/line_end from the NEW file.
Confidence < 5 → skip the issue.

For each issue:
- issue_type: Bug, Security, Performance, Error Handling, Logic Error, Style
- category: bug, security, performance, error_handling, style
- title: Short specific title
- file: File path
- line_start: Line number in POST-CHANGE code (must be in hunk range)
- description: What's wrong, why it matters, what input triggers it
- suggestion: One clear sentence on how to fix
- impact: Concrete production consequence
- code_snippet: VERBATIM copy from diff (3-8 lines)
- confidence: 0-10 (10=provable, 7-9=strong, 5-6=likely)
- ai_fix: The corrected code
- ai_agent_prompt: Instructions for fixing

For positive findings:
- file_path: File path
- positive_finding: List of good patterns observed
```

---

### Summarizer System Prompt

**File**: `nprompt.py`

**Focus**: *Narrative walkthrough*

```
You are writing a step-by-step walkthrough of the code review.

Context:
{file_based_context}

Investigation:
{format_messages(messages)}

Produce: file_based_walkthrough

For each file:
- file: File path
- walkthrough_steps: List of observations IN CHRONOLOGICAL ORDER

Example steps:
- "Line 10: function foo validates input correctly"
- "Line 25: handles error case for missing user"
- "Line 40: uses context manager for proper cleanup"
- "Line 55: potential null pointer (see issue #1)"

Mix positives and issues. Be specific. Follow the flow of the code.
```

---

## Graph Wiring

```python
from langgraph.graph import StateGraph, END

def build_code_review_graph(
    query_service: CodeSearchService,
    model: str,
    repo_id: str | None = None
) -> StateGraph:
    tools = get_tools(query_service, repo_id=repo_id)
    llm = load_chat_model(model)
    llm_with_tools = llm.bind_tools(tools)
    
    builder = StateGraph(CodeReviewAgentState)
    
    # Add nodes
    builder.add_node("explorer", explorer_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("extract_sources", extract_sources)
    builder.add_node("increment_rounds", increment_rounds)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("summarizer", summarizer_node)
    
    # Set entry point
    builder.set_entry_point("explorer")
    
    # Add edges
    builder.add_conditional_edges("explorer", should_continue)
    builder.add_edge("tools", "extract_sources")
    builder.add_edge("extract_sources", "increment_rounds")
    builder.add_edge("increment_rounds", "explorer")
    builder.add_edge("reviewer", "summarizer")
    builder.add_edge("summarizer", END)
    
    return builder.compile(name="CodeReviewAgent")
```

---

## Configuration

```python
# In nprompt.py
MAX_TOOL_ROUNDS = 8  # Bounded tool calls

# Default model
DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"  # OpenRouter

# LLM configuration
llm = ChatOpenAI(
    model=DEFAULT_MODEL,
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,  # Deterministic output
)
```

---

## Testing Strategy

### Unit Tests
- Test each node in isolation
- Mock LLM responses
- Verify state transitions
- Test `extract_sources` deduplication

### Integration Tests
- Run full graph with sample context
- Verify output structure
- Check line number accuracy
- Validate confidence scores

### Edge Cases
- No issues found
- All positive findings
- Tool failures
- Max rounds reached
- Empty diff file

---

## File Structure

```
code_review_agent/nagent/
├── nstate.py              # State + Pydantic models (✅)
│   ├── ReviewCodeIssue
│   ├── FileBasedIssues
│   ├── AgentPositiveFinding
│   ├── FileBasedWalkthrough
│   ├── ReviewerOutput (NEW)
│   ├── SummarizerOutput (NEW)
│   ├── _merge_sources()
│   └── CodeReviewAgentState
├── ntools.py              # 19 code exploration tools (✅)
├── nprompt.py             # System prompts (✅)
│   ├── get_explorer_system_prompt()
│   ├── get_reviewer_system_prompt()
│   └── get_summarizer_system_prompt()
├── ngraph.py              # Graph definition (🔄 refactor)
│   ├── _slim_messages()
│   ├── explorer_node()
│   ├── extract_sources()
│   ├── increment_rounds()
│   ├── should_continue()
│   ├── reviewer_node()
│   ├── summarizer_node()
│   └── build_code_review_graph()
└── nrunner.py             # CLI runner (✅)
    ├── run_review_agent()
    └── main()
```

---

## Implementation Order

### ✅ Completed

1. **State Schema** (`nstate.py`)
   - ✅ All models defined
   - ✅ Merge functions added
   - ⏳ Add `ReviewerOutput` and `SummarizerOutput`

2. **Tools** (`ntools.py`)
   - ✅ 19 code exploration tools
   - ✅ Return `(content, sources)` tuples

3. **Prompts** (`nprompt.py`)
   - ✅ Explorer prompt (investigation-focused)
   - ⏳ Reviewer prompt (issues + positives)
   - ⏳ Summarizer prompt (walkthrough)

4. **Runner** (`nrunner.py`)
   - ✅ CLI and programmatic interface
   - ✅ Logging and result printing

### 🔄 Refactor Needed

5. **Graph** (`ngraph.py`)
   - 🔄 Refactor to 3-node architecture
   - 🔄 Remove structured output from explorer
   - 🔄 Add `extract_sources` node
   - 🔄 Add `reviewer_node`
   - 🔄 Add `summarizer_node`
   - 🔄 Wire graph correctly

### ⏳ TODO

6. **Testing**
   - ⏳ Test each node in isolation
   - ⏳ Integration test with real context
   - ⏳ Validate structured output

---

## Key Design Decisions

### 1. **Why MAX_TOOL_ROUNDS = 8?**
- Bounded cost
- Enough for thorough investigation
- Explorer naturally stops early if done
- For large PRs, bump to 12-15

### 2. **Why Split Reviewer + Summarizer?**
- Smaller Pydantic models
- More reliable structured output
- Less hallucination
- Easier to debug
- Two small calls > one big call

### 3. **Why Extract Sources Node?**
- Clean separation of concerns
- Easy to test source extraction
- Doesn't clutter main flow
- Sources available for reviewer

### 4. **Why Full Message History in Reviewer?**
- Reviewer needs raw tool outputs
- Can cite specific line numbers
- No information loss
- Token cost is bounded (explorer bounded)

### 5. **Why `with_structured_output()`?**
- Type-safe results
- Pydantic validation
- Easier than parsing JSON
- Follows LangGraph best practices

---

## OpenRouter Configuration

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="anthropic/claude-sonnet-4-5",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
)
```

**Compatible models**:
- `anthropic/claude-sonnet-4-5` (recommended)
- `anthropic/claude-opus-4`
- `openai/gpt-4-turbo`
- Any OpenRouter model

---

## Next Steps

1. ✅ Add `ReviewerOutput` and `SummarizerOutput` to `nstate.py`
2. ✅ Implement `get_reviewer_system_prompt()` in `nprompt.py`
3. ✅ Implement `get_summarizer_system_prompt()` in `nprompt.py`
4. 🔄 Refactor `ngraph.py` to 3-node architecture
5. 🧪 Test full pipeline with real context
6. 📊 Performance tuning (rounds, tokens, model selection)

---

## Cost Estimate

Typical code review (medium-sized PR):
- **Explorer**: ~8 tool calls × ~500 tokens each = 4,000 tokens
- **Reviewer**: ~1 structured LLM call × ~2,000 tokens = 2,000 tokens
- **Summarizer**: ~1 structured LLM call × ~500 tokens = 500 tokens

**Total**: ~6,500 tokens per file review

With `claude-sonnet-4-5`:
- Input: $3/M tokens → $0.02 per review
- Output: $15/M tokens → $0.10 per review
- **Total**: ~$0.12 per file review

**Cost optimization**:
- Reduce `MAX_TOOL_ROUNDS` for simple files
- Use smaller model for summarizer
- Batch multiple files in one run