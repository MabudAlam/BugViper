"""Runner for the code review LangGraph agent.

This module provides a simple interface to run the code review agent
with a file context markdown file.
"""

import logging
from pathlib import Path

from langchain_core.messages import HumanMessage

from code_review_agent.nagent.ngraph import build_code_review_graph
from db.client import get_neo4j_client
from db.code_serarch_layer import CodeSearchService

logger = logging.getLogger(__name__)


def run_review_agent(
    review_context_file: str | Path,
    repo_id: str | None = None,
    model: str = "openai/gpt-4-turbo",
) -> dict:
    """Run the code review agent on a file.

    Args:
        review_context_file: Path to the markdown file with review context
        repo_id: Repository ID to scope queries
        model: LLM model to use

    Returns:
        Final state with file_based_issues, positive_findings, walkthrough

    Example:
        >>> result = run_review_agent(
        ...     review_context_file=(
        ...         "output/review-2026-04-02_20-32-14"
        ...         "/04_review_prompt_api_routers_webhook_py.md"
        ...     ),
        ...     repo_id="owner/repo"
        ... )
        >>> print(result["file_based_issues"])
    """
    review_context_file = Path(review_context_file)

    if not review_context_file.exists():
        raise FileNotFoundError(f"Review context file not found: {review_context_file}")

    # Read the markdown context
    file_based_context = review_context_file.read_text()
    logger.info(f"Loaded review context from: {review_context_file}")

    # Initialize database and query service
    neo4j_client = get_neo4j_client()
    query_service = CodeSearchService(neo4j_client)

    # Build the graph
    graph = build_code_review_graph(
        query_service=query_service,
        model=model,
        repo_id=repo_id,
    )

    # Initial state
    initial_state = {
        "file_based_context": file_based_context,
        "messages": [],
        "tool_rounds": 0,
        "sources": [],
        "file_based_issues": [],
        "file_based_positive_findings": [],
        "file_based_walkthrough": [],
    }

    # Add initial human message to start the agent
    initial_state["messages"] = [
        HumanMessage(
            content=(
                "Please review this code change and identify any issues, "
                "positive findings, and provide a walkthrough."
            )
        )
    ]

    logger.info("Starting code review agent...")
    logger.info(f"File: {review_context_file}")
    logger.info(f"Model: {model}")
    logger.info(f"Repo: {repo_id or 'all repositories'}")

    # Run the graph
    final_state = graph.invoke(initial_state)

    logger.info("Code review agent completed")
    logger.info(f"Tool rounds used: {final_state['tool_rounds']}")
    logger.info(f"Sources collected: {len(final_state['sources'])}")

    return final_state


def main():
    """CLI entry point for running the code review agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Run code review agent")
    parser.add_argument(
        "review_context_file",
        type=str,
        help="Path to the markdown file with review context",
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        default=None,
        help="Repository ID to scope queries (e.g., 'owner/repo')",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai/gpt-4-turbo",
        help="LLM model to use (default: openai/gpt-4-turbo)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run the agent
    result = run_review_agent(
        review_context_file=args.review_context_file,
        repo_id=args.repo_id,
        model=args.model,
    )

    # Print results
    print("\n" + "=" * 80)
    print("CODE REVIEW RESULTS")
    print("=" * 80)

    # Print issues
    if result["file_based_issues"]:
        print("\n## ISSUES FOUND\n")
        for file_issues in result["file_based_issues"]:
            print(f"### File: {file_issues['file']}\n")
            for issue in file_issues["issues"]:
                print(f"- [{issue['issue_type']}] {issue['title']}")
                print(f"  Line: {issue['line_start']}")
                print(f"  Confidence: {issue['confidence']}/10")
                print(f"  Description: {issue['description']}")
                print(f"  Suggestion: {issue['suggestion']}")
                print()
    else:
        print("\n## NO ISSUES FOUND\n")

    # Print positive findings
    if result["file_based_positive_findings"]:
        print("\n## POSITIVE FINDINGS\n")
        for finding in result["file_based_positive_findings"]:
            print(f"### File: {finding['file_path']}\n")
            for pos in finding["positive_finding"]:
                print(f"- {pos}")
            print()

    # Print walkthrough
    if result["file_based_walkthrough"]:
        print("\n## WALKTHROUGH\n")
        for walk in result["file_based_walkthrough"]:
            print(f"### File: {walk['file']}\n")
            for i, step in enumerate(walk["walkthrough_steps"], 1):
                print(f"{i}. {step}")
            print()

    print("\n" + "=" * 80)
    print(f"Tool rounds used: {result['tool_rounds']}")
    print(f"Sources collected: {len(result['sources'])}")
    print("=" * 80)


if __name__ == "__main__":
    main()
