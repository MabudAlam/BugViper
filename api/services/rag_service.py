"""
RAG service — LangGraph Graph API pattern.

Graph structure:
                    ┌─────────────┐
    START ─────────►│  llm_call   │
                    └──────┬──────┘
                           │
              tool_calls?  │  no tool calls?
                    ┌──────┴──────┐
                    │             │
                    ▼             ▼
             ┌────────────┐     END
             │ tool_node  │
             └──────┬─────┘
                    │
                    └──────► llm_call  (loop back)
"""

import os
from typing import Annotated, Literal
import operator

from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

from db import CodeSearchService


# ── 1. State ──────────────────────────────────────────────────────────────────
# MessagesState holds the conversation history.
# operator.add means new messages are APPENDED, not replaced.

class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]


# ── 2. Tool ───────────────────────────────────────────────────────────────────
# The LLM will call this tool when it needs to search the codebase.
# It must be created with access to the search_service, so we use a factory.

def make_search_tool(search_service: CodeSearchService):
    @tool
    def search_code(query: str) -> str:
        """
        Search the codebase for functions, classes, variables, and files.
        Use this whenever you need to find code relevant to the user's question.
        Returns matching symbols with their file paths and line numbers.
        """
        results = search_service.search_code(query)

        if not results:
            return f"No code found for query: '{query}'"

        lines = []
        for r in results[:10]:  # limit to top 10 results
            lines.append(f"[{r['type']}] {r['name']}  →  {r['path']}:{r['line_number']}")

        return "\n".join(lines)

    return search_code


# ── 3. Graph builder ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a code intelligence assistant for BugViper.
You have access to a search_code tool that searches a Neo4j graph of the codebase.
When a user asks about code, always search first, then answer based on what you find.
Be concise and reference specific file paths when possible."""


def build_rag_graph(search_service: CodeSearchService):
    """
    Build and compile the LangGraph agent graph.

    Steps:
      1. LLM decides whether to call the search_code tool or answer directly.
      2. If it calls the tool  → tool_node runs the search → back to LLM.
      3. If no tool call       → LLM writes the final answer → END.
    """

    # Set up the LLM and bind the search tool so the LLM knows it exists
    llm = init_chat_model(
        model=f"openrouter/{os.getenv('REVIEW_MODEL', 'openai/gpt-4o-mini')}",
        model_provider="openai",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )

    search_tool = make_search_tool(search_service)
    tools_by_name = {search_tool.name: search_tool}
    llm_with_tools = llm.bind_tools([search_tool])

    # ── Node 1: LLM call ──────────────────────────────────────────────────────
    # The LLM reads the conversation and either calls a tool or produces an answer.
    def llm_call(state: MessagesState):
        response = llm_with_tools.invoke(
            [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        )
        return {"messages": [response]}

    # ── Node 2: Tool execution ────────────────────────────────────────────────
    # Runs every tool the LLM requested and collects the results.
    def tool_node(state: MessagesState):
        results = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_fn = tools_by_name[tool_call["name"]]
            output = tool_fn.invoke(tool_call["args"])
            results.append(ToolMessage(content=str(output), tool_call_id=tool_call["id"]))
        return {"messages": results}

    # ── Routing: should we call a tool or stop? ───────────────────────────────
    def should_continue(state: MessagesState) -> Literal["tool_node", "__end__"]:
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tool_node"   # LLM wants to search → run the tool
        return END               # LLM wrote a final answer → stop

    # ── Assemble the graph ────────────────────────────────────────────────────
    graph = StateGraph(MessagesState)

    graph.add_node("llm_call", llm_call)
    graph.add_node("tool_node", tool_node)

    graph.add_edge(START, "llm_call")                          # always start with LLM
    graph.add_conditional_edges("llm_call", should_continue)   # branch: tool or end
    graph.add_edge("tool_node", "llm_call")                    # after tool → back to LLM

    return graph.compile()


# ── 4. Public entry point ─────────────────────────────────────────────────────

async def run_rag(question: str, search_service: CodeSearchService) -> str:
    """
    Run the RAG graph on a user question and return the final answer string.

    Args:
        question:       The user's question about the codebase.
        search_service: Injected CodeSearchService connected to Neo4j.

    Returns:
        The LLM's final answer as a plain string.
    """
    agent = build_rag_graph(search_service)

    result = await agent.ainvoke({
        "messages": [HumanMessage(content=question)]
    })

    # result["messages"] contains the full conversation history.
    # The last message is always the LLM's final answer.
    return result["messages"][-1].content
