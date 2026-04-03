"""Integration with the review pipeline.

This module provides a function to run the 3-node code review agent
on a single file and return results compatible with the existing pipeline.
"""

import logging
from pathlib import Path
from typing import Any

from code_review_agent.nagent.ngraph import build_code_review_graph
from code_review_agent.nagent.nstate import CodeReviewAgentState
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


async def run_3node_review_agent(
    file_path: str,
    file_context: str,
    query_service: CodeSearchService,
    repo_id: str,
    model: str = "anthropic/claude-sonnet-4-5",
    review_dir: Path | None = None,
    safe_filename: str | None = None,
) -> dict[str, Any]:
    """Run the 3-node code review agent on a single file.

    This is designed to be called from agentic_pipeline.py as a replacement
    for the old 2-node agent.

    Args:
        file_path: Path to the file being reviewed
        file_context: Full markdown context (from _build_file_context)
        query_service: Neo4j query service
        repo_id: Repository ID (e.g., "owner/repo")
        model: LLM model to use
        review_dir: Directory to write debug files
        safe_filename: Safe filename for debug files

    Returns:
        Dictionary with:
        - file_based_issues: List[FileBasedIssues]
        - file_based_positive_findings: List[AgentPositiveFinding]
        - file_based_walkthrough: List[FileBasedWalkthrough]
        - tool_rounds: int
        - sources: int (count)
    """
    logger.info(f"Running 3-node review agent for {file_path}")

    # Build the 3-node graph
    graph = build_code_review_graph(
        query_service=query_service,
        model=model,
        repo_id=repo_id,
    )

    # Initial state
    initial_state: CodeReviewAgentState = {
        "file_based_context": file_context,
        "messages": [],
        "tool_rounds": 0,
        "sources": [],
        "file_based_issues": [],
        "file_based_positive_findings": [],
        "file_based_walkthrough": [],
    }

    # Invoke the graph
    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.exception(f"3-node agent failed for {file_path}")
        # Return empty results on failure
        return {
            "file_based_issues": [],
            "file_based_positive_findings": [],
            "file_based_walkthrough": [],
            "tool_rounds": 0,
            "sources": [],
            "error": str(e),
        }

    # Write debug info
    if review_dir and safe_filename:
        debug_content = _format_debug_output(final_state, file_path)
        debug_file = review_dir / f"05_3node_agent_output_{safe_filename}.md"
        debug_file.write_text(debug_content)
        logger.info(f"Wrote debug output to {debug_file}")

    # Extract results
    result = {
        "file_based_issues": final_state.get("file_based_issues", []),
        "file_based_positive_findings": final_state.get("file_based_positive_findings", []),
        "file_based_walkthrough": final_state.get("file_based_walkthrough", []),
        "tool_rounds": final_state.get("tool_rounds", 0),
        "sources": final_state.get("sources", []),
    }

    logger.info(
        f"3-node agent completed for {file_path}: "
        f"{len(result['file_based_issues'])} files with issues, "
        f"{sum(len(fi.get('issues', [])) for fi in result['file_based_issues'])} total issues, "
        f"{len(result['file_based_positive_findings'])} positive findings, "
        f"{len(result['file_based_walkthrough'])} walkthroughs, "
        f"{result['tool_rounds']} tool rounds"
    )

    return result


def _format_debug_output(state: CodeReviewAgentState, file_path: str) -> str:
    """Format the final state for debugging."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    lines = [
        f"# 3-Node Agent Output: {file_path}",
        "",
        "## Results Summary",
        "",
        f"- **Tool Rounds Used**: {state.get('tool_rounds', 0)}",
        f"- **Sources Collected**: {len(state.get('sources', []))}",
        f"- **Files with Issues**: {len(state.get('file_based_issues', []))}",
        f"- **Positive Findings**: {len(state.get('file_based_positive_findings', []))}",
        f"- **Walkthroughs**: {len(state.get('file_based_walkthrough', []))}",
        "",
        "---",
        "",
    ]

    # File-based issues
    if state.get("file_based_issues"):
        lines.append("## Issues")
        lines.append("")
        for file_issues in state["file_based_issues"]:
            lines.append(f"### File: `{file_issues['file']}`")
            lines.append("")
            for i, issue in enumerate(file_issues.get("issues", []), 1):
                lines.append(f"#### Issue #{i}: {issue.get('title', 'Untitled')}")
                lines.append("")
                lines.append(f"**Type**: {issue.get('issue_type', '?')}")
                lines.append(f"**Category**: {issue.get('category', '?')}")
                lines.append(f"**Confidence**: {issue.get('confidence', 0)}/10")
                lines.append(
                    f"**Location**: `{issue.get('file', '?')}:{issue.get('line_start', '?')}`"
                )
                lines.append("")
                lines.append(f"**Description**: {issue.get('description', 'No description')}")
                lines.append("")
                lines.append(f"**Suggestion**: {issue.get('suggestion', 'No suggestion')}")
                lines.append("")
                lines.append(f"**Impact**: {issue.get('impact', 'No impact')}")
                lines.append("")
                if issue.get("code_snippet"):
                    lines.append("**Code**:")
                    lines.append("```")
                    lines.append(issue["code_snippet"])
                    lines.append("```")
                    lines.append("")
                if issue.get("ai_fix"):
                    lines.append("**Fix**:")
                    lines.append("```")
                    lines.append(issue["ai_fix"])
                    lines.append("```")
                    lines.append("")
            lines.append("")

    # Positive findings
    if state.get("file_based_positive_findings"):
        lines.append("## Positive Findings")
        lines.append("")
        for finding in state["file_based_positive_findings"]:
            lines.append(f"### File: `{finding['file_path']}`")
            lines.append("")
            for pf in finding.get("positive_finding", []):
                lines.append(f"- {pf}")
            lines.append("")

    # Walkthrough
    if state.get("file_based_walkthrough"):
        lines.append("## Walkthrough")
        lines.append("")
        for walk in state["file_based_walkthrough"]:
            lines.append(f"### File: `{walk['file']}`")
            lines.append("")
            for i, step in enumerate(walk.get("walkthrough_steps", []), 1):
                lines.append(f"{i}. {step}")
            lines.append("")

    # Messages (condensed)
    if state.get("messages"):
        lines.append("## Message History (Condensed)")
        lines.append("")
        for i, msg in enumerate(state["messages"], 1):
            if isinstance(msg, HumanMessage):
                lines.append(f"**{i}. Human**: {msg.content[:100]}...")
            elif isinstance(msg, AIMessage):
                if msg.tool_calls:
                    tools = [tc.get("name", "?") for tc in msg.tool_calls]
                    lines.append(f"**{i}. AI**: Called tools: {', '.join(tools)}")
                else:
                    lines.append(f"**{i}. AI**: {msg.content[:100]}...")
            elif isinstance(msg, ToolMessage):
                lines.append(f"**{i}. Tool**: {msg.name}: {str(msg.content)[:50]}...")
        lines.append("")

    return "\n".join(lines)
