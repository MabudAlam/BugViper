"""
Code query endpoints - Advanced implementation with Neo4j integration.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_neo4j_client
from api.models.semantic import SemanticHit, SemanticInput, SemanticSearchResponse
from db.client import Neo4jClient
from db.code_serarch_layer import CodeSearchService

router = APIRouter()


def get_query_service(db: Neo4jClient = Depends(get_neo4j_client)) -> CodeSearchService:
    """Dependency to get query service."""
    return CodeSearchService(db)


@router.get("/search")
async def search_code(
    query: str = Query(..., description="Search term — any identifier, snippet, or keyword"),
    limit: int = Query(30, description="Maximum results to return"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Unified code search.

    Three-tier strategy:
    1. Fulltext index on symbols (name, docstring, source_code)
    2. Name CONTAINS fallback (uses primary extracted identifier)
    3. File content line search (file_content_search / source_code CONTAINS)

    Results include type ('function' | 'class' | 'variable' | 'line'),
    name, path, line_number, and score. Symbol results come first
    (higher score), file-content line matches follow.
    """
    if len(query) > 500:
        raise HTTPException(status_code=400, detail="Query too long (max 500 characters)")
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.search_code(query, repo_id=repo_id)
        if limit:
            results = results[:limit]
        return {
            "results": results,
            "total": len(results),
            "query": query,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.get("/method-usages")
async def find_method_usages(
    method_name: str = Query(..., description="Name of the method to find usages for"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find all usages of a specific method.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        usages = query_service.find_method_usages(method_name, repo_id=repo_id)
        return usages
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to find method usages: {str(e)}")


@router.get("/find_callers")
async def find_callers(
    symbol_name: str = Query(..., description="Symbol name to find callers for"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find all methods/functions that call a specific symbol.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        return query_service.find_callers(symbol_name, repo_id=repo_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to find callers: {str(e)}")


@router.get("/class_hierarchy")
async def get_class_hierarchy(
    class_name: str = Query(..., description="Name of the class to analyze"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get class hierarchy (inheritance tree).
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        hierarchy = query_service.get_class_hierarchy(class_name, repo_id=repo_id)
        return hierarchy
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get class hierarchy: {str(e)}")


@router.get("/change_impact")
async def analyze_change_impact(
    symbol_name: str = Query(..., description="Symbol to analyze impact for"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Analyze the impact of changing a specific symbol.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        # Get all usages of the symbol to understand impact
        usages = query_service.find_method_usages(symbol_name, repo_id=repo_id)
        callers_result = query_service.find_callers(symbol_name, repo_id=repo_id)
        callers_list = callers_result.get("callers", [])

        return {
            "symbol": symbol_name,
            "usages": usages,
            "callers": callers_list,
            "definitions": callers_result.get("definitions", []),
            "impact_level": "high"
            if len(callers_list) > 5
            else "medium"
            if len(callers_list) > 0
            else "low",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze change impact: {str(e)}")


@router.get("/metrics")
async def get_code_metrics(
    repo_id: str = Query(None, description="Repository ID for repo-specific metrics"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get code metrics and statistics.
    """
    try:
        if repo_id:
            # Get repository-specific stats
            stats = query_service.get_repository_stats(repo_id)
            return {"repository_id": repo_id, "metrics": stats}
        else:
            # Get global graph stats
            stats = query_service.get_graph_stats()
            return {"global_metrics": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get code metrics: {str(e)}")


@router.get("/stats")
async def get_graph_stats(
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get overall graph statistics.
    """
    try:
        stats = query_service.get_graph_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get graph stats: {str(e)}")


# =========================================================================
# CodeFinder Tool Integration Endpoints
# =========================================================================


@router.get("/code-finder/function")
async def find_function_by_name(
    name: str = Query(..., description="Function name to search for"),
    fuzzy: bool = Query(False, description="Enable fuzzy search"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find functions by name using the CodeFinder tool.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_by_function_name(name, fuzzy, repo_id=repo_id)
        return {
            "function_name": name,
            "fuzzy_search": fuzzy,
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Function search failed: {str(e)}")


@router.get("/code-finder/class")
async def find_class_by_name(
    name: str = Query(..., description="Class name to search for"),
    fuzzy: bool = Query(False, description="Enable fuzzy search"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find classes by name using the CodeFinder tool.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_by_class_name(name, fuzzy, repo_id=repo_id)
        return {
            "class_name": name,
            "fuzzy_search": fuzzy,
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Class search failed: {str(e)}")


@router.get("/code-finder/variable")
async def find_variable_by_name(
    name: str = Query(..., description="Variable name to search for"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find variables by name using the CodeFinder tool.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_by_variable_name(name, repo_id=repo_id)
        return {"variable_name": name, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Variable search failed: {str(e)}")


@router.get("/code-finder/content")
async def find_by_content(
    query: str = Query(..., description="Content search term"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find code by content matching using the CodeFinder tool.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_by_content(query, repo_id=repo_id)
        return {"search_query": query, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content search failed: {str(e)}")


@router.get("/code-finder/module")
async def find_module_by_name(
    name: str = Query(..., description="Module name to search for"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find modules by name using the CodeFinder tool.
    """
    try:
        results = query_service.find_by_module_name(name)
        return {"module_name": name, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Module search failed: {str(e)}")


@router.get("/code-finder/imports")
async def find_imports(
    name: str = Query(..., description="Import name to search for"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find import statements using the CodeFinder tool.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_imports(name, repo_id=repo_id)
        return {"import_name": name, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import search failed: {str(e)}")


@router.get("/code-finder/complexity")
async def get_cyclomatic_complexity(
    function_name: str = Query(..., description="Function name to analyze"),
    path: str = Query(None, description="Optional file path to filter by"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get cyclomatic complexity of a function.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        result = query_service.get_cyclomatic_complexity(function_name, path, repo_id=repo_id)
        if not result:
            raise HTTPException(status_code=404, detail="Function not found")
        return {"function_name": function_name, "path_filter": path, "complexity": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Complexity analysis failed: {str(e)}")


@router.get("/code-finder/complexity/top")
async def find_most_complex_functions(
    limit: int = Query(10, description="Number of results to return"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find the most complex functions by cyclomatic complexity.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.find_most_complex_functions(limit, repo_id=repo_id)
        return {"limit": limit, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Complex function search failed: {str(e)}")


@router.get("/code-finder/line")
async def find_by_line(
    query: str = Query(..., description="Search term to find in file content"),
    limit: int = Query(50, description="Maximum number of line matches to return"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Search raw file content line-by-line.
    Uses file_content_search fulltext index (falls back to CONTAINS).
    Returns path + line_number + match_line for each hit — no source dumps.
    Pair with /code-finder/peek to view context around a hit.
    """
    if len(query) > 500:
        raise HTTPException(status_code=400, detail="Query too long (max 500 characters)")
    limit = min(limit, 100)  # hard cap
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        results = query_service.search_file_content(query, limit, repo_id=repo_id)
        return {
            "query": query,
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Line search failed: {str(e)}")


@router.get("/code-finder/peek")
async def peek_file_lines(
    path: str = Query(..., description="Absolute file path (as stored in graph)"),
    line: int = Query(None, description="Anchor line number (1-indexed, optional)"),
    above: int = Query(10, description="Lines to show above the anchor"),
    below: int = Query(10, description="Lines to show below the anchor"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Return a window of lines around a given line in a file.
    The anchor line is flagged with is_anchor=true.
    Use above/below to control the context window size.
    For markdown files, returns the full content regardless of line parameter.
    """
    above = min(above, 200)
    below = min(below, 200)
    line = line or 1
    if line < 1:
        line = 1
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        result = query_service.peek_file_lines(path, line, above, below, repo_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Peek failed: {str(e)}")


@router.get("/language/stats")
async def get_language_statistics(
    language: str = Query(None, description="Optional language filter"),
    repo_owner: str = Query(None, description="Repository owner to filter results"),
    repo_name: str = Query(None, description="Repository name to filter results"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get statistics about programming languages in the codebase.
    """
    repo_id = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else None
    try:
        return query_service.get_language_stats(language, repo_id=repo_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Language statistics query failed: {str(e)}")


# --- Code Review / Diff Context Endpoints ---


@router.get("/symbols-at-lines-relative")
async def get_symbols_at_lines_relative(
    repo_id: str = Query(..., description="Repository ID (e.g. owner/repo)"),
    file_path: str = Query(..., description="Repo-relative file path (e.g. src/main.py)"),
    start_line: int = Query(..., description="Start line number"),
    end_line: int = Query(..., description="End line number"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Find all symbols overlapping a line range using repo-relative path.
    """
    try:
        results = query_service.get_symbols_at_lines_by_relative_path(
            repo_id, file_path, start_line, end_line
        )
        return {
            "repo_id": repo_id,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "symbols": results,
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Symbol lookup failed: {str(e)}")


class FileChange(BaseModel):
    file_path: str
    start_line: int = 1
    end_line: int = 999999


class DiffContextRequest(BaseModel):
    repo_id: str
    changes: List[FileChange]


@router.post("/diff-context")
async def get_diff_context(
    request: DiffContextRequest, query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Build full RAG context for a code diff.

    Given a repository and a list of file changes (path + line ranges),
    returns affected symbols with source code, their callers, class hierarchy,
    and full file sources. Designed for AI-powered code review.

    Example request:
    ```json
    {
        "repo_id": "owner/repo",
        "changes": [
            {"file_path": "src/main.py", "start_line": 10, "end_line": 30},
            {"file_path": "src/utils.py", "start_line": 1, "end_line": 50}
        ]
    }
    ```
    """
    try:
        changes_dicts = [c.model_dump() for c in request.changes]
        result = query_service.get_diff_context(request.repo_id, changes_dicts)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diff context failed: {str(e)}")


@router.get("/file-source")
async def get_file_source(
    repo_id: str = Query(..., description="Repository ID"),
    file_path: str = Query(..., description="Repo-relative file path"),
    query_service: CodeSearchService = Depends(get_query_service),
) -> Dict[str, Any]:
    """
    Get the full source code of a file from the graph.
    """
    try:
        result = query_service.get_file_source(repo_id, file_path)
        if not result:
            raise HTTPException(status_code=404, detail="File not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File source retrieval failed: {str(e)}")


@router.post("/semantic")
async def semantic_search(
    body: SemanticInput,
    query_service: CodeSearchService = Depends(get_query_service),
) -> SemanticSearchResponse:
    """
    Semantic / natural-language code search via vector embeddings.
    Embeds the question, queries Neo4j vector indexes, returns ranked code chunks.
    No LLM involved — pure vector similarity.
    """
    import asyncio

    from common.embedder import embed_texts

    vectors: list[list[float]] = await asyncio.to_thread(embed_texts, [body.question])
    embedding = vectors[0]
    repo_id = f"{body.repoOwner}/{body.repoName}" if body.repoOwner and body.repoName else None

    results = query_service.semantic_search(embedding, repo_id=repo_id)

    hits = [
        SemanticHit(
            name=r.get("name"),
            type=r.get("type", "unknown"),
            path=r.get("path"),
            line_number=r.get("line_number") or None,
            source_code=r.get("source_code"),
            docstring=r.get("docstring"),
            score=float(r.get("score") or 0.0),
        )
        for r in results[:10]
    ]
    return SemanticSearchResponse(results=hits, total=len(hits))
