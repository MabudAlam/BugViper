from typing import Any, Dict, List, Literal, Optional
import re
import logging

from .client import Neo4jClient
from .schema import CYPHER_QUERIES

logger = logging.getLogger(__name__)


class CodeSearchService:
    """
    Single query service for all code graph operations.

    Covers:
    - Repository management (list, delete, stats)
    - Code search (fulltext, content, symbol lookup)
    - Analysis (callers, class hierarchy, change impact)
    - CodeFinder queries (function/class/variable/module lookup, complexity)
    - Diff context for the AI review pipeline
    """

    # Properties that are internal/large and must never appear in API responses.
    _EXCLUDED_NODE_PROPS = frozenset({"embedding", "embeddings", "vector"})

    def __init__(self, client: Neo4jClient):
        self.db = client
        self.driver = client.driver

    @classmethod
    def _safe_node(cls, node) -> Dict[str, Any]:
        """Convert a Neo4j node to a plain dict, stripping embedding/vector properties."""
        if node is None:
            return {}
        return {k: v for k, v in dict(node).items() if k not in cls._EXCLUDED_NODE_PROPS}

    # =========================================================================
    # Graph Statistics
    # =========================================================================

    def get_graph_stats(self) -> Dict[str, int]:
        """Get total node counts across the entire graph."""
        records, _, _ = self.db.run_query(CYPHER_QUERIES["get_graph_stats"])
        return dict(records[0]) if records else {}

    def get_repository_stats(self, repo_id: str) -> Dict[str, Any]:
        """Get statistics for a specific repository."""
        if not self.db.connected:
            return {
                "files": 25, "classes": 12, "functions": 89,
                "methods": 156, "lines": 3420, "imports": 67,
                "languages": ["Python", "TypeScript", "JavaScript"],
            }
        records, _, _ = self.db.run_query(CYPHER_QUERIES["get_repo_stats"], {"repo_id": repo_id})
        if records:
            r = records[0]
            return {
                "files": r["file_count"], "classes": r["class_count"],
                "functions": r["function_count"], "methods": r["method_count"],
                "lines": r["line_count"], "imports": r["import_count"],
                "languages": r["languages"],
            }
        return {}

    # =========================================================================
    # Repository Management
    # =========================================================================

    def list_repositories(self) -> List[Dict[str, Any]]:
        """List all repositories in the database."""
        query = """
        MATCH (r:Repository)
        OPTIONAL MATCH (r)-[:CONTAINS*]->(f:File)
        RETURN r.id as id, r.name as name, r.owner as owner,
               r.url as url, r.path as local_path,
               r.last_commit_hash as last_commit,
               r.created_at as created_at,
               r.updated_at as updated_at,
               count(DISTINCT f) as file_count
        ORDER BY r.updated_at DESC, r.name
        """
        records, _, _ = self.db.run_query(query)

        def _dt(value):
            if value is None:
                return None
            return value.iso_format() if hasattr(value, "iso_format") else str(value)

        return [
            {
                "id": r.get("id"), "name": r.get("name"), "owner": r.get("owner"),
                "url": r.get("url"), "local_path": r.get("local_path"),
                "last_commit": r.get("last_commit"),
                "created_at": _dt(r.get("created_at")),
                "updated_at": _dt(r.get("updated_at")),
                "file_count": r.get("file_count") or 0,
            }
            for r in records
        ]

    def delete_repository(self, repo_id: str) -> bool:
        """Delete a repository and all its associated nodes."""
        try:
            query = """
            MATCH (r:Repository)
            WHERE r.id = $repo_id OR r.repo = $repo_id
            OPTIONAL MATCH (r)-[:CONTAINS*]->(n)
            DETACH DELETE r, n
            RETURN count(r) as deleted_count
            """
            records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
            return bool(records and records[0]["deleted_count"] > 0)
        except Exception as e:
            logger.error("Error deleting repository %s: %s", repo_id, e)
            return False

    def get_repo_overview(self, repo_id: str) -> Dict[str, Any]:
        """Get a high-level overview of a repository."""
        if not self.db.connected:
            return {
                "repo": {"id": repo_id, "name": repo_id.split("/")[-1], "owner": repo_id.split("/")[0]},
                "files": 25, "classes": 12, "functions": 89,
                "languages": ["Python", "TypeScript", "JavaScript"],
            }
        records, _, _ = self.db.run_query(CYPHER_QUERIES["get_repo_overview"], {"repo_id": repo_id})
        if records:
            r = records[0]
            return {
                "repo": r["repo"], "files": r["file_count"],
                "classes": r["class_count"], "functions": r["function_count"],
                "languages": r["languages"],
            }
        return {}

    def get_repository_files(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get all files in a repository."""
        query = """
        MATCH (r:Repository)
        WHERE r.id = $repo_id OR r.repo = $repo_id
        MATCH (r)-[:CONTAINS*]->(f:File)
        RETURN f.id as id, f.path as path, f.language as language,
               f.lines_count as lines_count
        ORDER BY f.path
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        return [{"id": r["id"], "path": r["path"], "language": r["language"], "lines_count": r["lines_count"]} for r in records]

    def reconstruct_file(self, file_id: str) -> Optional[str]:
        """Return a file's source_code stored in the graph."""
        records, _, _ = self.db.run_query(
            "MATCH (f:File {id: $file_id}) RETURN f.source_code as source_code, f.path as path",
            {"file_id": file_id},
        )
        if not records:
            return None
        source = records[0].get("source_code")
        if not source:
            logger.warning("File %s has no source_code stored", records[0].get("path"))
        return source or None

    def verify_repository_reconstruction(self, repo_id: str) -> Dict[str, Any]:
        """Verify that all files in a repository have source_code stored."""
        query = """
        MATCH (r:Repository)
        WHERE r.id = $repo_id OR r.repo = $repo_id
        MATCH (r)-[:CONTAINS*]->(f:File)
        RETURN count(f) as total_files,
               sum(CASE WHEN f.source_code IS NOT NULL THEN 1 ELSE 0 END) as files_with_source,
               sum(f.lines_count) as total_lines,
               sum(size(f.source_code)) as total_source_size
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        if not records:
            return {"error": "Repository not found", "repo_id": repo_id}

        r = records[0]
        total = r["total_files"] or 0
        with_source = r["files_with_source"] or 0
        rate = (with_source / total * 100) if total > 0 else 0

        problem_records, _, _ = self.db.run_query(
            """
            MATCH (r:Repository) WHERE r.id = $repo_id OR r.repo = $repo_id
            MATCH (r)-[:CONTAINS*]->(f:File) WHERE f.source_code IS NULL
            RETURN f.path as path LIMIT 10
            """,
            {"repo_id": repo_id},
        )
        return {
            "repo_id": repo_id, "total_files": total, "files_with_source": with_source,
            "files_without_source": total - with_source,
            "success_rate": f"{rate:.1f}%",
            "total_lines": r["total_lines"] or 0,
            "total_source_size_mb": (r["total_source_size"] or 0) / 1024 / 1024,
            "status": "All files ready" if rate == 100 else f"{total - with_source} files missing source",
            "problem_files": [r["path"] for r in problem_records],
        }

    def get_repo_config_files(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get all config files in a repository."""
        query = """
        MATCH (r:Repository)
        WHERE r.id = $repo_id OR r.repo = $repo_id
        MATCH (r)-[:HAS_CONFIG]->(cf:ConfigFile)
        RETURN cf.id as id, cf.path as path, cf.file_type as file_type,
               cf.project_name as project_name, cf.version as version,
               cf.lines_count as lines_count
        ORDER BY cf.path
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        return [
            {"id": r["id"], "path": r["path"], "file_type": r["file_type"],
             "project_name": r["project_name"], "version": r["version"], "lines_count": r["lines_count"]}
            for r in records
        ]

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get all package dependencies declared in a repository."""
        query = """
        MATCH (r:Repository)
        WHERE r.id = $repo_id OR r.repo = $repo_id
        MATCH (r)-[:HAS_CONFIG]->(cf:ConfigFile)-[:HAS_DEPENDENCY]->(d:Dependency)
        RETURN d.name as name, d.version_spec as version, d.is_dev as is_dev,
               d.source as source, cf.path as config_file
        ORDER BY d.is_dev, d.name
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        return [
            {"name": r["name"], "version": r["version"], "is_dev": r["is_dev"],
             "source": r["source"], "config_file": r["config_file"]}
            for r in records
        ]

    # =========================================================================
    # Method / Function Queries
    # =========================================================================

    def find_method_usages(self, method_name: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Find all usages of a method by name."""
        records, _, _ = self.db.run_query(CYPHER_QUERIES["find_method_usages"], {"method_name": method_name, "repo_id": repo_id})
        results = []
        for record in records:
            callers = [
                {"caller": c.get("caller_name"), "type": c.get("caller_type"), "line": c.get("line"), "file": c.get("file")}
                for c in (record["callers"] or [])
                if c and c.get("caller_name")
            ]
            results.append({
                "method": {
                    "name": record.get("method_name"),
                    "line_number": record.get("line_number"),
                    "docstring": record.get("docstring"),
                    "source_code": record.get("source_code"),
                    "complexity": record.get("complexity"),
                },
                "file": record.get("file_path"),
                "callers": callers,
            })
        return {"usages": results}

    def find_callers(self, symbol_name: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Find all functions/methods that call a specific symbol.

        Falls back to source_code text search when no CALLS edges exist.
        """
        def_records, _, _ = self.db.run_query(CYPHER_QUERIES["find_function_definition"], {"name": symbol_name, "repo_id": repo_id})
        definitions = [dict(r) for r in def_records]

        call_records, _, _ = self.db.run_query(CYPHER_QUERIES["find_callers"], {"name": symbol_name, "repo_id": repo_id})
        callers: List[Dict[str, Any]] = [
            {
                "caller": r["caller_name"], "type": r["caller_type"],
                "file": r["file_path"], "line": r["call_line"],
                "source": "call_graph", "source_code": r.get("source_code"),
            }
            for r in call_records
        ]

        fallback_used = False
        if not callers:
            def_files = {d["file_path"] for d in definitions if d.get("file_path")}
            callers = self._find_callers_by_file_content(symbol_name, def_files, repo_id=repo_id)
            fallback_used = bool(callers)

        return {"callers": callers, "symbol": symbol_name, "total": len(callers), "definitions": definitions, "fallback_used": fallback_used}

    def _find_callers_by_file_content(self, symbol_name: str, exclude_file_paths: set, repo_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Find callers by scanning File.source_code content when no CALLS edges exist."""
        call_pattern = f"{symbol_name}("
        file_records, _, _ = self.db.run_query(
            """
            MATCH (f:File)
            WHERE f.source_code CONTAINS $call_pattern
              AND NOT f.path IN $exclude_paths
              AND (f.is_dependency IS NULL OR f.is_dependency = false)
              AND ($repo_id IS NULL OR f.repo = $repo_id)
            RETURN f.path AS file_path, f.source_code AS source_code
            LIMIT 10
            """,
            {"call_pattern": call_pattern, "exclude_paths": list(exclude_file_paths), "repo_id": repo_id},
        )

        callers: List[Dict[str, Any]] = []
        for file_record in file_records:
            file_path: str = file_record["file_path"]
            source_code: str = file_record.get("source_code") or ""
            if not source_code:
                continue

            lines = source_code.split("\n")
            call_lines = [
                i + 1 for i, line in enumerate(lines)
                if call_pattern in line and not line.lstrip().startswith("def ")
            ]
            if not call_lines:
                continue

            func_records, _, _ = self.db.run_query(
                """
                MATCH (f:File {path: $file_path})-[:CONTAINS]->(func:Function)
                WHERE func.name <> $name AND func.line_number IS NOT NULL
                RETURN func.name AS func_name, func.line_number AS line_number
                ORDER BY func.line_number
                """,
                {"file_path": file_path, "name": symbol_name},
            )
            funcs = [(r["func_name"], int(r["line_number"])) for r in func_records]
            if not funcs:
                continue

            func_ranges: Dict[str, tuple] = {
                fname: (fstart, funcs[idx + 1][1] - 1 if idx + 1 < len(funcs) else len(lines))
                for idx, (fname, fstart) in enumerate(funcs)
            }

            seen: set = set()
            for call_line in call_lines:
                containing: Optional[tuple] = None
                for fname, fstart in funcs:
                    if fstart <= call_line:
                        containing = (fname, fstart)
                    else:
                        break
                if containing is None or containing[0] in seen:
                    continue
                fname, _ = containing
                seen.add(fname)
                fstart_idx, fend_idx = func_ranges[fname]
                func_source = "\n".join(lines[fstart_idx - 1: fend_idx]).rstrip()
                callers.append({
                    "caller": fname, "type": "Function", "file": file_path,
                    "line": call_line, "source": "text_reference", "source_code": func_source or None,
                })

        return callers

    # =========================================================================
    # Class Queries
    # =========================================================================

    def get_class_hierarchy(self, class_name: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Get the inheritance hierarchy of a class (ancestors + descendants)."""
        records, _, _ = self.db.run_query(CYPHER_QUERIES["get_class_hierarchy"], {"class_name": class_name, "repo_id": repo_id})
        if not records:
            return {"class_name": class_name, "found": False, "ancestors": [], "descendants": []}

        r = records[0]
        clean = lambda nodes: [dict(n) for n in (nodes or []) if n and n.get("name")]
        return {
            "class_name": r.get("class_name"), "file_path": r.get("file_path"),
            "line_number": r.get("line_number"), "docstring": r.get("docstring"),
            "source_code": r.get("source_code"), "found": True,
            "ancestors": clean(r["ancestors"]), "descendants": clean(r["descendants"]),
        }

    # =========================================================================
    # Search Operations
    # =========================================================================

    _CODE_KEYWORDS = frozenset({
        'class', 'def', 'import', 'from', 'return', 'self', 'cls', 'None',
        'True', 'False', 'and', 'or', 'not', 'in', 'is', 'if', 'else',
        'elif', 'for', 'while', 'try', 'except', 'with', 'as', 'pass',
        'break', 'continue', 'raise', 'yield', 'async', 'await', 'lambda',
        'function', 'const', 'let', 'var', 'new', 'this', 'super',
    })

    def _extract_identifiers(self, query: str) -> List[str]:
        tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', query)
        seen: set = set()
        result = []
        for t in tokens:
            if t not in self._CODE_KEYWORDS and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _escape_lucene_query(self, query: str) -> str:
        query = query.strip()
        if not query:
            return '*'
        if re.match(r'^[A-Za-z0-9_]+$', query):
            return f'"{query}"'
        identifiers = self._extract_identifiers(query)
        if identifiers:
            return ' AND '.join(f'"{t}"' for t in identifiers[:3])
        return f'"{query.replace(chr(34), chr(92) + chr(34))}"'

    def _name_contains_fallback(self, search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        identifiers = self._extract_identifiers(search_term)
        name_term = max(identifiers, key=len) if identifiers else search_term
        records, _, _ = self.db.run_query(
            """
            MATCH (node)
            WHERE (node:Function OR node:Class OR node:Variable)
              AND node.name CONTAINS $name_term
            OPTIONAL MATCH (f:File)-[:CONTAINS]->(node)
            RETURN
                CASE WHEN node:Function THEN 'function'
                     WHEN node:Class THEN 'class'
                     ELSE 'variable' END as type,
                node.name as name,
                coalesce(f.path, node.path) as path,
                coalesce(node.line_number, 0) as line_number,
                1.0 as score
            ORDER BY node.name
            LIMIT $limit
            """,
            {"name_term": name_term, "limit": limit},
        )
        return [{"type": r["type"], "name": r["name"], "path": r["path"], "line_number": r["line_number"], "score": r["score"]} for r in records]

    # Matches queries that look like code patterns: method calls, paths, indexing
    _CODE_PATTERN_RE = re.compile(r'[.()\[\]{}/<>]')

    def search_code(self, search_term: str, repo_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Three-tier code search: fulltext index → name CONTAINS → file content.

        When the query contains code-pattern characters (dots, parens, slashes)
        it likely refers to a specific call site or route, not just a symbol
        declaration.  In that case file-content results are appended even when
        symbol results were already found, so the actual call line is visible.

        If repo_id is provided, results are filtered to that repository only.
        """
        escaped = self._escape_lucene_query(search_term)
        is_code_pattern = bool(self._CODE_PATTERN_RE.search(search_term))
        results: List[Dict[str, Any]] = []

        try:
            if repo_id:
                # Scoped search: filter by repo_id on the File node
                query = """
                    CALL db.index.fulltext.queryNodes('code_search', $search_term)
                    YIELD node, score
                    WHERE $repo_id IS NULL OR node.repo = $repo_id
                    OPTIONAL MATCH (f:File)-[:CONTAINS]->(node)
                    RETURN
                        CASE WHEN node:Function THEN 'function'
                             WHEN node:Class THEN 'class'
                             ELSE 'variable' END as type,
                        node.name as name,
                        coalesce(f.path, node.path) as path,
                        coalesce(node.line_number, 0) as line_number,
                        score
                    ORDER BY score DESC
                    LIMIT 20
                """
                records, _, _ = self.db.run_query(query, {"search_term": escaped, "repo_id": repo_id})
            else:
                records, _, _ = self.db.run_query(CYPHER_QUERIES["search_code"], {"search_term": escaped})
            results = [{"type": r["type"], "name": r["name"], "path": r["path"], "line_number": r["line_number"], "score": r["score"]} for r in records]
        except Exception as e:
            logger.warning("code_search fulltext failed: %s", e)

        if not results:
            results = self._name_contains_fallback(search_term)

        # For code-pattern queries always also search file content so that
        # the matching call site / route line is included in the results.
        if not results or is_code_pattern:
            file_hits = self.search_file_content(search_term, limit=20)
            line_results = [
                {"type": "line", "name": h["match_line"].strip(), "path": h["path"],
                 "line_number": h["line_number"], "score": 0.5}
                for h in file_hits
            ]
            if not results:
                results = line_results
            elif line_results:
                seen = {(r["path"], r["line_number"]) for r in results}
                results += [r for r in line_results if (r["path"], r["line_number"]) not in seen]

        return results

    _MAX_FILE_BYTES = 500_000

    def search_file_content(self, search_term: str, limit: int = 50, repo_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search file source code line by line, server-side in Cypher."""
        escaped = self._escape_lucene_query(search_term)
        limit = min(limit, 200)

        try:
            records, _, _ = self.db.run_query(
                """
                CALL db.index.fulltext.queryNodes('file_content_search', $search_term) YIELD node, score
                WHERE node.source_code IS NOT NULL AND size(node.source_code) < $max_bytes
                  AND ($repo_id IS NULL OR node.repo = $repo_id)
                WITH node, score, split(node.source_code, '\n') as lines
                LIMIT 10
                UNWIND range(0, size(lines) - 1) AS idx
                WITH node.path AS path, lines[idx] AS line_content, idx + 1 AS line_number, score
                WHERE toLower(line_content) CONTAINS toLower($raw_term)
                RETURN path, line_number, line_content AS match_line
                ORDER BY score DESC, path, line_number
                LIMIT $limit
                """,
                {"search_term": escaped, "raw_term": search_term, "max_bytes": self._MAX_FILE_BYTES, "limit": limit, "repo_id": repo_id},
            )
            results = [{"path": r["path"], "line_number": r["line_number"], "match_line": (r["match_line"] or "").rstrip()} for r in records]
            if results:
                return results
        except Exception as e:
            logger.warning("file_content_search fulltext failed: %s", e)

        records, _, _ = self.db.run_query(
            """
            MATCH (f:File)
            WHERE f.source_code IS NOT NULL AND f.source_code CONTAINS $raw_term
              AND size(f.source_code) < $max_bytes
              AND ($repo_id IS NULL OR f.repo = $repo_id)
            WITH f, split(f.source_code, '\n') AS lines
            LIMIT 5
            UNWIND range(0, size(lines) - 1) AS idx
            WITH f.path AS path, lines[idx] AS line_content, idx + 1 AS line_number
            WHERE line_content CONTAINS $raw_term
            RETURN path, line_number, line_content AS match_line
            ORDER BY path, line_number
            LIMIT $limit
            """,
            {"raw_term": search_term, "max_bytes": self._MAX_FILE_BYTES, "limit": limit, "repo_id": repo_id},
        )
        return [{"path": r["path"], "line_number": r["line_number"], "match_line": (r["match_line"] or "").rstrip()} for r in records]

    def peek_file_lines(self, path: str, line: int, above: int = 10, below: int = 10) -> Dict[str, Any]:
        """Return a window of lines around a specific line in a file."""
        records, _, _ = self.db.run_query(
            "MATCH (f:File {path: $path}) WHERE f.source_code IS NOT NULL AND size(f.source_code) < 2000000 RETURN f.source_code as source_code LIMIT 1",
            {"path": path},
        )
        if not records or not records[0].get("source_code"):
            return {"error": "File not found or too large", "path": path}

        lines = records[0]["source_code"].split("\n")
        total = len(lines)
        start = max(0, line - above - 1)
        end = min(total, line + below)
        return {
            "path": path, "anchor_line": line, "total_lines": total,
            "window": [{"line_number": i + 1, "content": lines[i], "is_anchor": (i + 1) == line} for i in range(start, end)],
        }

    # =========================================================================
    # Impact Analysis
    # =========================================================================

    def analyze_change_impact(self, target_id: str) -> List[Dict[str, Any]]:
        """Analyze the impact of changing a specific code element."""
        records, _, _ = self.db.run_query(CYPHER_QUERIES["analyze_change_impact"], {"target_id": target_id})
        return [{"affected": r["affected"], "type": r["type"], "file": r["file"], "distance": r["distance"]} for r in records]

    # =========================================================================
    # Symbol Lookup (CodeFinder)
    # =========================================================================

    def _fulltext_query(self, find_by: Literal["Class", "Function"], fuzzy_search: bool, repo_id: Optional[str] = None) -> str:
        repo_filter = "AND ($repo_id IS NULL OR node.repo = $repo_id)" if repo_id is not None else ""
        return f"""
            CALL db.index.fulltext.queryNodes("code_search_index", $search_term) YIELD node, score
            WITH node, score
            WHERE node:{find_by} {'AND node.name CONTAINS $search_term' if not fuzzy_search else ''}
              {repo_filter}
            RETURN node.name as name, node.path as path, node.line_number as line_number,
                node.source as source, node.docstring as docstring, node.is_dependency as is_dependency
            ORDER BY score DESC LIMIT 20
        """

    def find_by_function_name(self, search_term: str, fuzzy_search: bool = False, repo_id: Optional[str] = None) -> List[Dict]:
        """Find functions by exact or fuzzy name match."""
        with self.driver.session() as session:
            if not fuzzy_search:
                return session.run(
                    "MATCH (n:Function {name: $name}) WHERE $repo_id IS NULL OR n.repo = $repo_id RETURN n.name as name, n.path as path, n.line_number as line_number, n.source as source, n.docstring as docstring, n.is_dependency as is_dependency LIMIT 20",
                    name=search_term, repo_id=repo_id,
                ).data()
            return session.run(self._fulltext_query("Function", fuzzy_search, repo_id), search_term=f"name:{search_term}", repo_id=repo_id).data()

    def find_by_class_name(self, search_term: str, fuzzy_search: bool = False, repo_id: Optional[str] = None) -> List[Dict]:
        """Find classes by exact or fuzzy name match."""
        with self.driver.session() as session:
            if not fuzzy_search:
                return session.run(
                    "MATCH (n:Class {name: $name}) WHERE $repo_id IS NULL OR n.repo = $repo_id RETURN n.name as name, n.path as path, n.line_number as line_number, n.source as source, n.docstring as docstring, n.is_dependency as is_dependency LIMIT 20",
                    name=search_term, repo_id=repo_id,
                ).data()
            return session.run(self._fulltext_query("Class", fuzzy_search, repo_id), search_term=f"name:{search_term}", repo_id=repo_id).data()

    def find_by_variable_name(self, search_term: str, repo_id: Optional[str] = None) -> List[Dict]:
        """Find variables by name substring."""
        with self.driver.session() as session:
            return session.run(
                "MATCH (v:Variable) WHERE v.name CONTAINS $search_term AND ($repo_id IS NULL OR v.repo = $repo_id) RETURN v.name as name, v.path as path, v.line_number as line_number, v.value as value, v.context as context, v.is_dependency as is_dependency ORDER BY v.is_dependency ASC, v.name LIMIT 20",
                search_term=search_term, repo_id=repo_id,
            ).data()

    def find_by_content(self, search_term: str, repo_id: Optional[str] = None) -> List[Dict]:
        """Find code by content matching in source or docstrings."""
        with self.driver.session() as session:
            try:
                return session.run(
                    """
                    CALL db.index.fulltext.queryNodes("code_search_index", $search_term) YIELD node, score
                    WITH node, score WHERE (node:Function OR node:Class OR node:Variable)
                      AND ($repo_id IS NULL OR node.repo = $repo_id)
                    RETURN CASE WHEN node:Function THEN 'function' WHEN node:Class THEN 'class' ELSE 'variable' END as type,
                        node.name as name, node.path as path, node.line_number as line_number,
                        node.source as source, node.docstring as docstring, node.is_dependency as is_dependency
                    ORDER BY score DESC LIMIT 20
                    """,
                    search_term=search_term, repo_id=repo_id,
                ).data()
            except Exception:
                return session.run(
                    """
                    MATCH (node) WHERE (node:Function OR node:Class OR node:Variable)
                      AND (node.name CONTAINS $search_term OR node.source CONTAINS $search_term OR node.docstring CONTAINS $search_term)
                      AND ($repo_id IS NULL OR node.repo = $repo_id)
                    RETURN CASE WHEN node:Function THEN 'function' WHEN node:Class THEN 'class' ELSE 'variable' END as type,
                        node.name as name, node.path as path, node.line_number as line_number,
                        node.source as source, node.docstring as docstring, node.is_dependency as is_dependency
                    LIMIT 20
                    """,
                    search_term=search_term, repo_id=repo_id,
                ).data()

    def find_by_module_name(self, search_term: str) -> List[Dict]:
        """Find modules by name substring. Module nodes are global — no repo filtering."""
        with self.driver.session() as session:
            return session.run(
                "MATCH (m:Module) WHERE m.name CONTAINS $search_term RETURN m.name as name, m.lang as lang ORDER BY m.name LIMIT 20",
                search_term=search_term,
            ).data()

    def find_imports(self, search_term: str, repo_id: Optional[str] = None) -> List[Dict]:
        """Find import statements by alias or imported name."""
        with self.driver.session() as session:
            return session.run(
                """
                MATCH (f:File)-[r:IMPORTS]->(m:Module)
                WHERE (r.alias = $search_term OR r.imported_name = $search_term)
                  AND ($repo_id IS NULL OR f.repo = $repo_id)
                RETURN r.alias as alias, r.imported_name as imported_name,
                       m.name as module_name, f.path as path, r.line_number as line_number
                ORDER BY f.path LIMIT 20
                """,
                search_term=search_term, repo_id=repo_id,
            ).data()

    def find_class_hierarchy(self, class_name: str, path: str = None) -> Dict[str, Any]:
        """Find parent classes, child classes, and methods for a class."""
        match = "MATCH (child:Class {name: $class_name, path: $path})" if path else "MATCH (child:Class {name: $class_name})"
        params = {"class_name": class_name, "path": path}
        with self.driver.session() as session:
            parents = session.run(f"{match} MATCH (child)-[:INHERITS]->(parent:Class) RETURN DISTINCT parent.name as parent_class, parent.path as parent_file_path, parent.line_number as parent_line_number, parent.docstring as parent_docstring, parent.is_dependency as parent_is_dependency ORDER BY parent.is_dependency ASC, parent.name", **params).data()
            children = session.run(f"{match} MATCH (grandchild:Class)-[:INHERITS]->(child) RETURN DISTINCT grandchild.name as child_class, grandchild.path as child_file_path, grandchild.line_number as child_line_number, grandchild.docstring as child_docstring, grandchild.is_dependency as child_is_dependency ORDER BY grandchild.is_dependency ASC, grandchild.name", **params).data()
            methods = session.run(f"{match} MATCH (child)-[:CONTAINS]->(method:Function) RETURN DISTINCT method.name as method_name, method.path as method_file_path, method.line_number as method_line_number, method.args as method_args, method.docstring as method_docstring, method.is_dependency as method_is_dependency ORDER BY method.is_dependency ASC, method.line_number", **params).data()
        return {"class_name": class_name, "parent_classes": parents, "child_classes": children, "methods": methods}

    def get_cyclomatic_complexity(self, function_name: str, path: str = None, repo_id: Optional[str] = None) -> Optional[Dict]:
        """Get the cyclomatic complexity score for a function."""
        with self.driver.session() as session:
            if path:
                results = session.run(
                    "MATCH (f:Function {name: $name}) WHERE (f.path ENDS WITH $path OR f.path = $path) AND ($repo_id IS NULL OR f.repo = $repo_id) RETURN f.name as function_name, f.cyclomatic_complexity as complexity, f.path as path, f.line_number as line_number",
                    name=function_name, path=path, repo_id=repo_id,
                ).data()
            else:
                results = session.run(
                    "MATCH (f:Function {name: $name}) WHERE $repo_id IS NULL OR f.repo = $repo_id RETURN f.name as function_name, f.cyclomatic_complexity as complexity, f.path as path, f.line_number as line_number",
                    name=function_name, repo_id=repo_id,
                ).data()
        return results[0] if results else None

    def find_most_complex_functions(self, limit: int = 10, repo_id: Optional[str] = None) -> List[Dict]:
        """Find the top N most complex functions in the codebase (excluding dependencies)."""
        with self.driver.session() as session:
            return session.run(
                """
                MATCH (file:File)-[:CONTAINS]->(f:Function)
                WHERE f.cyclomatic_complexity IS NOT NULL
                  AND (f.is_dependency = false OR f.is_dependency IS NULL)
                  AND ($repo_id IS NULL OR file.repo = $repo_id)
                RETURN f.name as function_name, f.path as path, f.cyclomatic_complexity as complexity, f.line_number as line_number
                ORDER BY f.cyclomatic_complexity DESC LIMIT $limit
                """,
                limit=limit, repo_id=repo_id,
            ).data()

    # =========================================================================
    # Symbols at Lines
    # =========================================================================

    def get_symbols_at_lines_by_relative_path(self, repo_id: str, relative_path: str, start_line: int, end_line: int) -> List[Dict[str, Any]]:
        """Find symbols overlapping a line range using repo-relative file path."""
        records, _, _ = self.db.run_query(
            """
            MATCH (f:File)
            WHERE f.relative_path = $relative_path AND f.repo = $repo_id
            MATCH (f)-[:CONTAINS]->(n)
            WHERE (n:Function OR n:Class OR n:Variable)
              AND n.line_number IS NOT NULL
              AND n.line_number <= $end_line
              AND coalesce(n.end_line, n.line_number) >= $start_line
            RETURN
                CASE WHEN n:Function THEN 'function' WHEN n:Class THEN 'class' ELSE 'variable' END as type,
                n.name as name, n.line_number as start_line,
                coalesce(n.end_line, n.line_number) as end_line,
                n.source as source, n.docstring as docstring, f.relative_path as file_path
            ORDER BY n.line_number
            """,
            {"repo_id": repo_id, "relative_path": relative_path, "start_line": start_line, "end_line": end_line},
        )
        return [dict(r) for r in records]

    # =========================================================================
    # Language Statistics
    # =========================================================================

    def get_language_stats(self, language: Optional[str] = None, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Get per-language file/function/class/variable counts.

        If language is provided, returns stats for that language only.
        Otherwise returns a breakdown across all languages.
        If repo_id is provided, scopes results to that repository.
        """
        if language:
            records, _, _ = self.db.run_query(
                """
                MATCH (f:File)
                WHERE f.language = $language
                  AND ($repo_id IS NULL OR f.repo = $repo_id)
                OPTIONAL MATCH (f)-[:CONTAINS]->(func:Function)
                OPTIONAL MATCH (f)-[:CONTAINS]->(cls:Class)
                OPTIONAL MATCH (f)-[:CONTAINS]->(var:Variable)
                RETURN count(DISTINCT f)    AS file_count,
                       count(DISTINCT func) AS function_count,
                       count(DISTINCT cls)  AS class_count,
                       count(DISTINCT var)  AS variable_count
                """,
                {"language": language, "repo_id": repo_id},
            )
            result = dict(records[0]) if records else {}
            result["language"] = language
            return result
        else:
            records, _, _ = self.db.run_query(
                """
                MATCH (f:File)
                WHERE f.language IS NOT NULL
                  AND ($repo_id IS NULL OR f.repo = $repo_id)
                OPTIONAL MATCH (f)-[:CONTAINS]->(func:Function)
                OPTIONAL MATCH (f)-[:CONTAINS]->(cls:Class)
                OPTIONAL MATCH (f)-[:CONTAINS]->(var:Variable)
                RETURN f.language              AS language,
                       count(DISTINCT f)       AS file_count,
                       count(DISTINCT func)    AS function_count,
                       count(DISTINCT cls)     AS class_count,
                       count(DISTINCT var)     AS variable_count
                ORDER BY file_count DESC
                """,
                {"repo_id": repo_id},
            )
            return {
                "languages": [dict(r) for r in records],
                "total_languages": len(records),
            }

    # =========================================================================
    # File Source
    # =========================================================================

    def get_file_source(self, repo_id: str, file_path: str) -> Optional[Dict[str, Any]]:
        """Fetch source code for a file by repo-relative path."""
        records, _, _ = self.db.run_query(
            """
            MATCH (f:File)
            WHERE f.relative_path = $relative_path AND f.repo = $repo_id
            RETURN f.source_code AS source_code,
                   f.relative_path AS path,
                   f.language AS language,
                   f.lines_count AS lines_count
            LIMIT 1
            """,
            {"relative_path": file_path, "repo_id": repo_id},
        )
        if not records:
            return None
        r = records[0]
        return {
            "file_path": r.get("path"),
            "language": r.get("language"),
            "lines_count": r.get("lines_count"),
            "source_code": r.get("source_code"),
        }

    # =========================================================================
    # Semantic Search (vector similarity)
    # =========================================================================

    _SEMANTIC_INDEXES: List[tuple] = [
        ("function_semantic", "function"),
        ("class_semantic",    "class"),
        ("method_semantic",   "method"),
        ("file_semantic",     "file"),
    ]

    def semantic_search(
        self,
        embedding: List[float],
        repo_id: Optional[str] = None,
        k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Query all vector indexes and return ranked, deduplicated results.

        The caller is responsible for producing the embedding vector.
        If repo_id is given, results are filtered via node.repo = $repo_id in Cypher.
        """
        all_results: List[Dict[str, Any]] = []

        for index_name, node_type in self._SEMANTIC_INDEXES:
            try:
                records, _, _ = self.db.run_query(
                    f"""
                    CALL db.index.vector.queryNodes('{index_name}', $k, $embedding)
                    YIELD node, score
                    WHERE $repo_id IS NULL OR node.repo = $repo_id
                    OPTIONAL MATCH (f:File)-[:CONTAINS]->(node)
                    RETURN node.name                               AS name,
                           '{node_type}'                           AS type,
                           coalesce(f.path, node.path)             AS path,
                           coalesce(node.line_number, 0)           AS line_number,
                           coalesce(node.source_code, node.source) AS source_code,
                           node.docstring                          AS docstring,
                           score
                    """,
                    {"k": k, "embedding": embedding, "repo_id": repo_id},
                )
                all_results.extend([dict(r) for r in records])
            except Exception as exc:
                logger.warning("Vector index '%s' unavailable: %s", index_name, exc)

        all_results.sort(key=lambda r: r.get("score") or 0.0, reverse=True)

        seen: set = set()
        deduped: List[Dict[str, Any]] = []
        for r in all_results:
            key = (r.get("name"), r.get("path"))
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        return deduped

    # =========================================================================
    # Diff Context (Review Pipeline)
    # =========================================================================

    def get_diff_context(self, repo_id: str, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build RAG context for a set of file changes (lightweight version)."""
        all_affected, all_callers, all_hierarchy, file_sources = [], [], [], {}

        for change in changes:
            file_path = change["file_path"]
            start_line = change.get("start_line", 1)
            end_line = change.get("end_line", 999999)

            symbols = self.get_symbols_at_lines_by_relative_path(repo_id, file_path, start_line, end_line)
            for s in symbols:
                s["change_file"] = file_path
            all_affected.extend(symbols)

            for sym in symbols:
                records, _, _ = self.db.run_query(
                    """
                    MATCH (caller)-[:CALLS]->(target)
                    WHERE target.name = $name AND target.repo = $repo_id
                    RETURN CASE WHEN caller:Function THEN 'function' WHEN caller:Class THEN 'class' ELSE 'other' END as type,
                        caller.name as name, caller.path as path,
                        caller.line_number as line_number, caller.source as source
                    LIMIT 20
                    """,
                    {"name": sym["name"], "repo_id": repo_id},
                )
                if records:
                    all_callers.append({"symbol": sym["name"], "symbol_type": sym["type"], "callers": [dict(r) for r in records]})

            for cls in [s for s in symbols if s["type"] == "class"]:
                records, _, _ = self.db.run_query(
                    "MATCH (c:Class {name: $name})-[:INHERITS*0..5]->(parent:Class) WHERE c.repo = $repo_id RETURN parent.name as name, parent.path as path, parent.source as source, parent.docstring as docstring",
                    {"name": cls["name"], "repo_id": repo_id},
                )
                if records:
                    all_hierarchy.append({"class": cls["name"], "hierarchy": [dict(r) for r in records]})

            records, _, _ = self.db.run_query(
                "MATCH (f:File) WHERE f.relative_path = $relative_path AND f.repo = $repo_id RETURN f.source_code as source_code LIMIT 1",
                {"relative_path": file_path, "repo_id": repo_id},
            )
            if records and records[0].get("source_code"):
                file_sources[file_path] = records[0]["source_code"]

        return {
            "affected_symbols": all_affected, "callers": all_callers,
            "class_hierarchy": all_hierarchy, "file_sources": file_sources,
            "total_affected": len(all_affected), "total_files": len(changes),
        }

    def get_diff_context_enhanced(self, repo_id: str, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build full relationship context for every changed symbol (used by the review pipeline)."""
        all_affected: List[Dict[str, Any]] = []
        all_callers: List[Dict[str, Any]] = []
        all_imports: List[Dict[str, Any]] = []
        all_dependencies: List[Dict[str, Any]] = []
        all_hierarchy: List[Dict[str, Any]] = []
        seen_affected: set = set()
        seen_callers: set = set()
        seen_imports: set = set()

        for change in changes:
            file_path = change["file_path"]
            start_line = change.get("start_line", 1)
            end_line = change.get("end_line", 999999)

            # 1. Affected symbols
            records, _, _ = self.db.run_query(
                """
                MATCH (f:File {repo: $repo_id}) WHERE f.path ENDS WITH $file_path
                MATCH (f)-[:CONTAINS]->(n)
                WHERE (n:Function OR n:Class OR n:Method)
                  AND n.line_number IS NOT NULL
                  AND n.line_number <= $end_line
                  AND coalesce(n.end_line, n.line_number) >= $start_line
                RETURN
                    CASE WHEN n:Class THEN 'class' WHEN n:Method THEN 'method' ELSE 'function' END AS type,
                    n.name AS name, n.line_number AS start_line,
                    coalesce(n.end_line, n.line_number) AS end_line,
                    n.source AS source, n.docstring AS docstring,
                    n.args AS args, f.path AS file_path
                ORDER BY n.line_number
                """,
                {"repo_id": repo_id, "file_path": file_path, "start_line": start_line, "end_line": end_line},
            )
            symbols = [dict(r) for r in records]

            for s in symbols:
                s["change_file"] = file_path
                key = f"{s['file_path']}:{s['name']}:{s['start_line']}"
                if key not in seen_affected:
                    seen_affected.add(key)
                    all_affected.append(s)

            for sym in symbols:
                caller_key = f"{sym['file_path']}:{sym['name']}"
                if caller_key in seen_callers:
                    continue
                seen_callers.add(caller_key)

                # 2. Callers
                try:
                    records, _, _ = self.db.run_query(
                        """
                        MATCH (target) WHERE (target:Function OR target:Method OR target:Class)
                          AND target.name = $name AND coalesce(target.path, '') CONTAINS $repo_id
                        MATCH (caller)-[call:CALLS]->(target)
                        WHERE (caller:Function OR caller:Method) AND NOT coalesce(caller.is_dependency, false)
                        OPTIONAL MATCH (cf:File)-[:CONTAINS]->(caller)
                        RETURN DISTINCT caller.name AS caller_name,
                            CASE WHEN caller:Method THEN 'method' ELSE 'function' END AS caller_type,
                            coalesce(caller.path, cf.path) AS caller_path,
                            caller.line_number AS caller_line, call.line_number AS call_line, call.args AS call_args
                        ORDER BY caller_path, caller.line_number LIMIT 10
                        """,
                        {"name": sym["name"], "repo_id": repo_id},
                    )
                    callers_list = [dict(r) for r in records]
                    if callers_list:
                        all_callers.append({"symbol": sym["name"], "symbol_type": sym["type"], "callers": callers_list})
                except Exception as e:
                    logger.warning("Error finding callers for %s: %s", sym["name"], e)

                # 3. Dependencies
                try:
                    records, _, _ = self.db.run_query(
                        """
                        MATCH (caller) WHERE (caller:Function OR caller:Method)
                          AND caller.name = $name AND caller.path = $path
                        MATCH (caller)-[call:CALLS]->(called)
                        WHERE (called:Function OR called:Method) AND NOT coalesce(called.is_dependency, false)
                        RETURN DISTINCT called.name AS called_name,
                            CASE WHEN called:Method THEN 'method' ELSE 'function' END AS called_type,
                            called.path AS called_path, call.line_number AS call_line, call.args AS call_args
                        ORDER BY call.line_number LIMIT 15
                        """,
                        {"name": sym["name"], "path": sym["file_path"]},
                    )
                    deps = [dict(r) for r in records]
                    if deps:
                        all_dependencies.append({"symbol": sym["name"], "dependencies": deps})
                except Exception as e:
                    logger.warning("Error finding dependencies for %s: %s", sym["name"], e)

                # 4. Class methods
                if sym["type"] == "class":
                    try:
                        records, _, _ = self.db.run_query(
                            "MATCH (cls:Class {name: $name, path: $path})-[:CONTAINS]->(m:Function) RETURN m.name AS name, m.line_number AS line_number, coalesce(m.end_line, m.line_number) AS end_line, m.source AS source, m.docstring AS docstring, m.args AS args ORDER BY m.line_number",
                            {"name": sym["name"], "path": sym["file_path"]},
                        )
                        methods = [dict(r) for r in records]
                        if methods:
                            sym["methods"] = methods
                    except Exception as e:
                        logger.warning("Error fetching methods for class %s: %s", sym["name"], e)

            # 5. Imports → resolve to in-repo source
            import_records, _, _ = self.db.run_query(
                """
                MATCH (f:File {repo: $repo_id}) WHERE f.path ENDS WITH $file_path
                MATCH (f)-[r:IMPORTS]->(m)
                RETURN r.alias AS alias, r.imported_name AS imported_name,
                       m.name AS module_name, r.line_number AS line_number
                ORDER BY r.line_number LIMIT 20
                """,
                {"repo_id": repo_id, "file_path": file_path},
            )
            for imp in import_records:
                imported_name = imp.get("imported_name") or imp.get("alias")
                if not imported_name or imported_name in seen_imports:
                    continue
                seen_imports.add(imported_name)
                try:
                    func_results = self.find_by_function_name(imported_name, fuzzy_search=False)
                    if func_results:
                        f = func_results[0]
                        if f.get("path") and repo_id in f.get("path", ""):
                            all_imports.append({"name": imported_name, "type": "function", "source": f.get("source", ""), "path": f.get("path"), "line": f.get("line_number"), "docstring": f.get("docstring"), "from_file": file_path})
                    else:
                        cls_results = self.find_by_class_name(imported_name, fuzzy_search=False)
                        if cls_results:
                            c = cls_results[0]
                            if c.get("path") and repo_id in c.get("path", ""):
                                all_imports.append({"name": imported_name, "type": "class", "source": c.get("source", ""), "path": c.get("path"), "line": c.get("line_number"), "docstring": c.get("docstring"), "from_file": file_path})
                except Exception as e:
                    logger.warning("Error resolving import %s: %s", imported_name, e)

            # 6. Class hierarchy
            for cls in [s for s in symbols if s["type"] == "class"]:
                try:
                    h = self.find_class_hierarchy(cls["name"], cls.get("file_path"))
                    if h:
                        all_hierarchy.append({"class": cls["name"], "parents": h.get("parent_classes", []), "children": h.get("child_classes", []), "methods": h.get("methods", [])})
                except Exception as e:
                    logger.warning("Error finding hierarchy for %s: %s", cls["name"], e)

        return {
            "affected_symbols": all_affected, "callers": all_callers,
            "imports": all_imports, "dependencies": all_dependencies,
            "class_hierarchy": all_hierarchy,
            "total_affected": len(all_affected), "total_imports": len(all_imports),
            "total_files": len({c["file_path"] for c in changes}),
        }
