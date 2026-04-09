"""Simple 3-node agent executor.

This module provides a clean interface to run the 3-node code review agent
(Explorer → Reviewer → Summarizer) on a single file.

Architecture:
    review_service.py (gathers context & builds prompt)
        ↓
    agent_executor.py (runs 3-node agent)
        ↓
    ngraph.py (builds graph & executes nodes)
        ↓
    returns results to review_service.py
"""

import logging
from pathlib import Path
from typing import Any

from code_review_agent.config import config
from code_review_agent.nagent.ngraph import build_code_review_graph
from code_review_agent.nagent.nstate import CodeReviewAgentState
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


async def execute_review_agent(
    file_path: str,
    file_context: str,
    query_service: CodeSearchService,
    repo_id: str,
    model: str = "anthropic/claude-sonnet-4-5",
    file_content: str = "",
    previous_issues: list[dict[str, Any]] | None = None,
    review_dir: Path | None = None,
    safe_filename: str | None = None,
    entity_risk_context: str = "",
) -> dict[str, Any]:
    """Execute the 3-node code review agent on a single file.

    This is the main entry point for running the agent.

    Args:
        file_path: Path to the file being reviewed
        file_context: Full markdown context (from build_file_context)
        query_service: Neo4j query service for tools
        repo_id: Repository ID (e.g., "owner/repo")
        model: LLM model to use
        file_content: Raw file content for validator node
        previous_issues: Previous issues to validate (for incremental reviews)
        review_dir: Directory to write debug files
        safe_filename: Safe filename for debug files
        entity_risk_context: JSON string of entity risk from inspect-style triage

    Returns:
        Dictionary with:
        - file_based_issues: List of FileBasedIssues
        - file_based_positive_findings: List of AgentPositiveFinding
        - file_based_walkthrough: List of FileBasedWalkthrough
        - validated_previous_issues: List of validated previous issues
        - tool_rounds: Number of tool rounds used
        - sources: List of sources collected
        - error: Error message if failed (only present on failure)
    """
    logger.info(f"Executing 3-node agent for {file_path}")

    # Build the graph
    graph = build_code_review_graph(
        query_service=query_service,
        model=config.synthesis_model,
        repo_id=repo_id,
    )

    # Initial state
    initial_state: CodeReviewAgentState = {
        "file_based_context": file_context,
        "file_content": file_content,
        "previous_issues": previous_issues or [],
        "validated_previous_issues": [],
        "entity_risk_context": entity_risk_context,
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
        return {
            "file_based_issues": [],
            "file_based_positive_findings": [],
            "file_based_walkthrough": [],
            "validated_previous_issues": [],
            "tool_rounds": 0,
            "sources": [],
            "error": str(e),
        }

    # Write debug info
    if review_dir and safe_filename:
        _write_debug_output(final_state, file_path, review_dir, safe_filename)

    # Extract results
    import json

    result = {
        "file_based_issues": final_state.get("file_based_issues", []),
        "file_based_positive_findings": final_state.get("file_based_positive_findings", []),
        "file_based_walkthrough": final_state.get("file_based_walkthrough", []),
        "validated_previous_issues": final_state.get("validated_previous_issues", []),
        "tool_rounds": final_state.get("tool_rounds", 0),
        "sources": final_state.get("sources", []),
        "raw_output_json": json.dumps(
            {
                "file_based_issues": final_state.get("file_based_issues", []),
                "file_based_positive_findings": final_state.get("file_based_positive_findings", []),
                "file_based_walkthrough": final_state.get("file_based_walkthrough", []),
                "validated_previous_issues": final_state.get("validated_previous_issues", []),
                "tool_rounds": final_state.get("tool_rounds", 0),
            },
            indent=2,
        ),
    }

    # Log summary
    total_issues = sum(len(fi.get("issues", [])) for fi in result["file_based_issues"])
    validated_count = len(result["validated_previous_issues"])
    logger.info(
        f"3-node agent completed for {file_path}: "
        f"{len(result['file_based_issues'])} files with issues, "
        f"{total_issues} total issues, "
        f"{len(result['file_based_positive_findings'])} positive findings, "
        f"{len(result['file_based_walkthrough'])} walkthroughs, "
        f"{validated_count} validated previous issues, "
        f"{result['tool_rounds']} tool rounds"
    )

    return result


def _write_debug_output(
    state: CodeReviewAgentState,
    file_path: str,
    review_dir: Path,
    safe_filename: str,
) -> None:
    """Write debug output to file."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    lines = [
        f"# 3-Node Agent Output: {file_path}",
        "",
        "## Summary",
        "",
        f"- **Tool Rounds**: {state.get('tool_rounds', 0)}",
        f"- **Sources**: {len(state.get('sources', []))}",
        f"- **Files with Issues**: {len(state.get('file_based_issues', []))}",
        f"- **Positive Findings**: {len(state.get('file_based_positive_findings', []))}",
        f"- **Walkthroughs**: {len(state.get('file_based_walkthrough', []))}",
        "",
        "---",
        "",
    ]

    # Issues
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
                lines.append(f"**Status**: {issue.get('status', 'new')}")
                lines.append(
                    f"**Location**: `{issue.get('file', '?')}:{issue.get('line_start', '?')}`"
                )
                lines.append("")
                lines.append(f"**Description**: {issue.get('description', 'No description')}")
                lines.append("")
                if issue.get("code_snippet"):
                    lines.append("**Code**:")
                    lines.append("```")
                    lines.append(issue["code_snippet"])
                    lines.append("```")
                    lines.append("")
            lines.append("")

    # Validated Previous Issues
    if state.get("validated_previous_issues"):
        lines.append("## Validated Previous Issues")
        lines.append("")
        for v in state["validated_previous_issues"]:
            lines.append(f"### {v.get('title', 'Untitled')}")
            lines.append(f"- **Status**: {v.get('status', '?')}")
            lines.append(f"- **File**: `{v.get('file', '?')}:{v.get('line_start', '?')}`")
            lines.append(f"- **Reason**: {v.get('reason', 'No reason provided')}")
            lines.append(f"- **Confidence**: {v.get('confidence', 0)}/10")
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
        lines.append("## Message History")
        lines.append("")
        for i, msg in enumerate(state["messages"][:20], 1):  # First 20
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

    debug_file = review_dir / f"05_agent_output_{safe_filename}.md"
    debug_file.write_text("\n".join(lines))
    logger.info(f"Wrote debug output to {debug_file}")
