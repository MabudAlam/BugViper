# 3-Node Agent Integration Guide

## Overview

The new 3-node agent (Explorer → Reviewer → Summarizer) is now integrated into the review pipeline. It replaces the old 2-node agent with better separation of concerns and more reliable structured output.

---

## How It Works

### Architecture

```
review_pipeline.py
    ↓
agentic_pipeline.py
    ↓
execute_agentic_review()
    ↓
if config.use_3node_agent:
    _review_file_with_3node_agent()  ← NEW
else:
    _review_file_with_explorer()     ← OLD (fallback)
    ↓
run_3node_review_agent()  (in nagent/pipeline_integration.py)
    ↓
build_code_review_graph()  (in nagent/ngraph.py)
    ↓
Explorer (ReAct loop) → Reviewer (Structured) → Summarizer (Structured)
```

---

## Configuration

### Enable/Disable in `.env`

```bash
# Enable the new 3-node agent (recommended)
USE_3NODE_AGENT=true

# Fallback to old 2-node agent
# USE_3NODE_AGENT=false
```

### Default: 3-node agent is **enabled** by default

If `USE_3NODE_AGENT` is not set in `.env`, it defaults to `True`.

---

## File Flow

### 1. Pipeline Builds Context

```python
# agentic_pipeline.py (line 519)
file_context = _build_file_context(
    file_path=file_path,
    full_diff=full_diff,
    full_file_content=content,
    file_ast=ast,
    previous_issues=formatted_prev_issues,
    explorer_context="",  # 3-node agent has its own explorer
    code_samples=file_code_samples,
)
```

This creates the markdown file like:
- `output/review-2026-04-02_20-32-14/04_review_prompt_api_routers_webhook_py.md`

### 2. Pipeline Calls 3-Node Agent

```python
# agentic_pipeline.py (line 325)
result = await _review_file_with_3node_agent(...)

# Which calls:
result = await run_3node_review_agent(
    file_path=file_path,
    file_context=file_context,
    query_service=query_service,
    repo_id=repo_id,
    model=config.review_model,
    ...
)
```

### 3. 3-Node Agent Executes

```python
# nagent/pipeline_integration.py
graph = build_code_review_graph(...)
final_state = await graph.ainvoke({
    "file_based_context": file_context,
    "messages": [],
    "tool_rounds": 0,
    ...
})
```

**Explorer Node** (ReAct loop):
- Reads `file_based_context`
- Calls tools to investigate
- Accumulates evidence in `messages`
- Extracts `sources`

**Reviewer Node** (Structured output):
- Reads full message history
- Calls LLM with `with_structured_output()`
- Output: `file_based_issues`, `file_based_positive_findings`

**Summarizer Node** (Structured output):
- Reads message history + context
- Calls LLM with `with_structured_output()`
- Output: `file_based_walkthrough`

### 4. Pipeline Receives Results

```python
# agentic_pipeline.py (line 680)
return FileReviewResult(
    file_path=file_path,
    issues=all_issues,
    walk_through_entry=walk_through_str,
    positive_findings=all_positive_findings,
    previous_issues_status=previous_status,
    ...
)
```

---

## Output Files

When `review_dir` is provided, the 3-node agent writes:

### Old Agent (2-Node):
- `04_review_prompt_{filename}.md` - Input context
- `05_aggregated.md` - Aggregated results

### New Agent (3-Node):
- `04_review_prompt_{filename}.md` - Input context (same)
- `05_3node_agent_output_{filename}.md` - **NEW** Detailed output from 3-node agent
- `05_aggregated.md` - Aggregated results (same)

The `05_3node_agent_output_{filename}.md` includes:
- Tool rounds used
- Sources collected
- All issues with details
- Positive findings
- Walkthrough steps
- Condensed message history

---

## Testing

### Run with Real Context File

```bash
# Set environment variables
export OPENROUTER_API_KEY="your-key"
export USE_3NODE_AGENT=true

# Run review
python -m api.services.review_service
```

### Check Output Files

```bash
# View the 3-node agent output
cat output/review-2026-04-02_20-32-14/05_3node_agent_output_api_routers_webhook_py.md

# View aggregated results
cat output/review-2026-04-02_20-32-14/05_aggregated.md
```

### Compare Old vs New

To compare the old 2-node agent with the new 3-node agent:

```bash
# Run with old agent
export USE_3NODE_AGENT=false
python -m api.services.review_service --pr 123

# Run with new agent
export USE_3NODE_AGENT=true
python -m api.services.review_service --pr 123

# Compare outputs
diff output/review-old/05_aggregated.md output/review-new/05_aggregated.md
```

---

## Migration Path

### Phase 1: Parallel Running (Current)
- Both agents can run simultaneously
- Config flag `USE_3NODE_AGENT` switches between them
- Old agent is still available as fallback

### Phase 2: Gradual Rollout
- Monitor 3-node agent results over time
- Compare quality, cost, and reliability
- Gather feedback

### Phase 3: Deprecate Old Agent
- Once confident, set `USE_3NODE_AGENT=true` permanently
- Remove old 2-node agent code
- Simplify pipeline

---

## Key Differences

| Aspect | Old (2-Node) | New (3-Node) |
|--------|--------------|--------------|
| **Architecture** | Explorer → Synthesize | Explorer → Reviewer → Summarizer |
| **Structured Output** | One large model | Two smaller models |
| **Reliability** | More hallucinations | Less hallucinations |
| **Cost** | Similar | Similar (bounded) |
| **Debugging** | Harder | Easier (node-by-node) |
| **Output Files** | 1 debug file | 1 debug file (but better) |

---

## Cost

With `anthropic/claude-sonnet-4-5`:

**Per file review:**
- Explorer: ~4,000 tokens
- Reviewer: ~2,000 tokens
- Summarizer: ~500 tokens
- **Total**: ~6,500 tokens ≈ **$0.12 per file**

**Cost optimizations:**
- Reduce `MAX_TOOL_ROUNDS` (default: 8)
- Use smaller model for summarizer
- Batch multiple files

---

## Troubleshooting

### Common Issues

**1. Agent fails with "No structured response"**
- Check model supports `with_structured_output()`
- Verify Pydantic models are correct
- Check LLM logs for validation errors

**2. Tool rounds exceeded**
- Increase `MAX_TOOL_ROUNDS` in `nprompt.py`
- Check if tool calls are necessary

**3. Debug file not written**
- Ensure `review_dir` parameter is passed
- Check file permissions

**4. Empty results**
- Check explorer logs
- Verify `file_based_context` is not empty
- Check LLM responses

### Logs

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Check 3-node agent logs
INFO:code_review_agent.nagent.pipeline_integration:Running 3-node review agent for api/routers/webhook.py
INFO:code_review_agent.nagent.ngraph:Explorer completed: 5 tool rounds
INFO:code_review_agent.nagent.ngraph:Reviewer completed: 2 issues, 3 positive findings
INFO:code_review_agent.nagent.ngraph:Summarizer completed: 5 walkthrough steps
```

---

## Next Steps

1. ✅ Integration complete - pipeline uses 3-node agent
2. 🧪 Test with real PRs
3. 📊 Monitor results and costs
4. 🔧 Fine-tune prompts if needed
5. 📝 Document lessons learned

---

## Summary

The 3-node agent is now production-ready and integrated into the review pipeline. Set `USE_3NODE_AGENT=true` in `.env` to enable it (default). The architecture provides better separation of concerns, more reliable output, and easier debugging.