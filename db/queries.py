

from typing import Dict, List, Any, Optional
import re
import logging

from .client import Neo4jClient
from .schema import CYPHER_QUERIES

logger = logging.getLogger(__name__)

class CodeQueryService:
    """
    Service for querying code elements from the Neo4j graph.
    
    Provides methods for:
    - Finding method/function usages and callers
    - Analyzing class hierarchies
    - Searching code by name or content
    - Reconstructing files from the graph
    """
    
    def __init__(self, client: Neo4jClient):
        self.db = client
    
    # =========================================================================
    # Graph Statistics
    # =========================================================================
    
    def get_graph_stats(self) -> Dict[str, int]:
        """Get statistics about the code graph."""
        query = CYPHER_QUERIES["get_graph_stats"]
        records, _, _ = self.db.run_query(query)
        
        if records:
            return dict(records[0])
        return {}

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
        
        def convert_datetime(value):
            """Convert Neo4j DateTime to ISO string format."""
            if value is None:
                return None
            # Check if it's a Neo4j DateTime object
            if hasattr(value, 'iso_format'):
                return value.iso_format()
            # If it's already a string or other type, return as is
            return str(value) if value is not None else None
        
        return [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "owner": record.get("owner"),
                "url": record.get("url"),
                "local_path": record.get("local_path"),
                "last_commit": record.get("last_commit"),
                "created_at": convert_datetime(record.get("created_at")),
                "updated_at": convert_datetime(record.get("updated_at")),
                "file_count": record.get("file_count") or 0
            }
            for record in records
        ]

    def delete_repository(self, repo_id: str) -> bool:
        """Delete a repository and all its associated data."""
        try:
            query = """
            MATCH (r:Repository)
            WHERE r.id = $repo_id OR r.repo = $repo_id
            OPTIONAL MATCH (r)-[:CONTAINS*]->(n)
            DETACH DELETE r, n
            RETURN count(r) as deleted_count
            """
            records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
            return records and records[0]["deleted_count"] > 0
        except Exception as e:
            logger.error("Error deleting repository %s: %s", repo_id, e)
            return False
    
    def get_repository_stats(self, repo_id: str) -> Dict[str, Any]:
        """Get statistics for a specific repository."""
        if not self.db.connected:
            # Return mock stats
            return {
                "files": 25,
                "classes": 12,
                "functions": 89,
                "methods": 156,
                "lines": 3420,
                "imports": 67,
                "languages": ["Python", "TypeScript", "JavaScript"]
            }
            
        query = CYPHER_QUERIES["get_repo_stats"]
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        
        if records:
            record = records[0]
            return {
                "files": record["file_count"],
                "classes": record["class_count"],
                "functions": record["function_count"],
                "methods": record["method_count"],
                "lines": record["line_count"],
                "imports": record["import_count"],
                "languages": record["languages"]
            }
        return {}
    
    # =========================================================================
    # Method and Function Queries
    # =========================================================================
    
    def find_method_usages(self, method_name: str) -> Dict[str, Any]:
        """Find all usages of a method by name."""
        query = CYPHER_QUERIES["find_method_usages"]
        records, _, _ = self.db.run_query(query, {"method_name": method_name})

        results = []
        for record in records:
            callers = []
            for caller_dict in record["callers"]:
                if caller_dict and caller_dict.get("caller"):
                    caller_node = caller_dict["caller"]
                    callers.append({
                        "caller": dict(caller_node),
                        "line": caller_dict.get("line"),
                        "file": caller_dict.get("file"),
                    })

            results.append({
                "method": dict(record["m"]) if record["m"] else None,
                "file": record.get("file_path"),
                "callers": callers,
            })
        return {"usages": results}
    
    def find_callers(self, symbol_name: str) -> Dict[str, Any]:
        """Find all methods/functions that call a specific symbol.

        Returns the symbol's definition(s) and call-graph callers.
        Falls back to source_code text search when no CALLS edges exist.
        """
        # 1. Fetch definition(s)
        def_records, _, _ = self.db.run_query(
            CYPHER_QUERIES["find_function_definition"], {"name": symbol_name}
        )
        definitions = [dict(r) for r in def_records]

        # 2. Call-graph edges
        call_records, _, _ = self.db.run_query(
            CYPHER_QUERIES["find_callers"], {"name": symbol_name}
        )
        callers: List[Dict[str, Any]] = [
            {
                "caller": r["caller_name"],
                "type": r["caller_type"],
                "file": r["file_path"],
                "line": r["call_line"],
                "source": "call_graph",
                "source_code": r.get("source_code"),
            }
            for r in call_records
        ]

        # 3. File-content fallback when no CALLS edges exist.
        #    Parses File.source_code in Python to find exact call lines, then
        #    maps each line to its containing Function by line number ordering.
        #    The definition file is excluded to avoid sibling-function noise.
        fallback_used = False
        if not callers:
            def_files = {d["file_path"] for d in definitions if d.get("file_path")}
            callers = self._find_callers_by_file_content(symbol_name, def_files)
            fallback_used = bool(callers)

        return {
            "callers": callers,
            "symbol": symbol_name,
            "total": len(callers),
            "definitions": definitions,
            "fallback_used": fallback_used,
        }
    
    def _find_callers_by_file_content(
        self, symbol_name: str, exclude_file_paths: set
    ) -> List[Dict[str, Any]]:
        """Find callers by parsing File.source_code content.

        Strategy:
          1. Find files whose source_code contains '<symbol_name>(' (the call syntax),
             excluding the file(s) where the symbol is defined.
          2. For each matching file, scan line-by-line to find exact call lines
             (skipping `def` lines which contain the name but aren't calls).
          3. Load the functions defined in each matching file (ordered by start line).
          4. Map each call line to its containing function by finding the function
             whose start_line is the largest value <= the call line.
          5. Extract the function's source from the file content using start/end lines.
        """
        call_pattern = f"{symbol_name}("

        file_query = """
            MATCH (f:File)
            WHERE f.source_code CONTAINS $call_pattern
              AND NOT f.path IN $exclude_paths
              AND (f.is_dependency IS NULL OR f.is_dependency = false)
            RETURN f.path AS file_path, f.source_code AS source_code
            LIMIT 10
        """
        file_records, _, _ = self.db.run_query(file_query, {
            "call_pattern": call_pattern,
            "exclude_paths": list(exclude_file_paths),
        })

        callers: List[Dict[str, Any]] = []
        for file_record in file_records:
            file_path: str = file_record["file_path"]
            source_code: str = file_record.get("source_code") or ""
            if not source_code:
                continue

            # Find lines that contain a genuine call (not the def line itself)
            lines = source_code.split("\n")
            call_lines = [
                i + 1  # 1-indexed
                for i, line in enumerate(lines)
                if call_pattern in line and not line.lstrip().startswith("def ")
            ]
            if not call_lines:
                continue

            # Load functions in this file, sorted by start line
            func_query = """
                MATCH (f:File {path: $file_path})-[:CONTAINS]->(func:Function)
                WHERE func.name <> $name AND func.line_number IS NOT NULL
                RETURN func.name AS func_name, func.line_number AS line_number
                ORDER BY func.line_number
            """
            func_records, _, _ = self.db.run_query(func_query, {
                "file_path": file_path,
                "name": symbol_name,
            })
            funcs = [(r["func_name"], int(r["line_number"])) for r in func_records]
            if not funcs:
                continue

            # Build a quick lookup: func_name → (start_line, end_line)
            func_ranges: Dict[str, tuple] = {}
            for idx, (fname, fstart) in enumerate(funcs):
                fend = funcs[idx + 1][1] - 1 if idx + 1 < len(funcs) else len(lines)
                func_ranges[fname] = (fstart, fend)

            seen: set = set()
            for call_line in call_lines:
                # The containing function is the one with the largest start_line <= call_line
                containing: Optional[tuple] = None
                for fname, fstart in funcs:
                    if fstart <= call_line:
                        containing = (fname, fstart)
                    else:
                        break  # funcs is sorted, no need to continue

                if containing is None or containing[0] in seen:
                    continue

                fname, fstart = containing
                seen.add(fname)
                fstart_idx, fend_idx = func_ranges[fname]
                func_source = "\n".join(lines[fstart_idx - 1 : fend_idx]).rstrip()

                callers.append({
                    "caller": fname,
                    "type": "Function",
                    "file": file_path,
                    "line": call_line,
                    "source": "text_reference",
                    "source_code": func_source or None,
                })

        return callers

    # =========================================================================
    # Class Queries
    # =========================================================================

    def get_class_hierarchy(self, class_name: str) -> Dict[str, Any]:
        """Get the inheritance hierarchy of a class."""
        query = CYPHER_QUERIES["get_class_hierarchy"]
        records, _, _ = self.db.run_query(query, {"class_name": class_name})

        if not records:
            return {"class_name": class_name, "found": False, "ancestors": [], "descendants": []}

        record = records[0]

        def clean_nodes(nodes: list) -> list:
            """Filter out null/empty map entries from collect(DISTINCT {...})."""
            return [
                dict(n) for n in (nodes or [])
                if n and n.get("name")
            ]

        return {
            "class_name": record.get("class_name"),
            "file_path": record.get("file_path"),
            "line_number": record.get("line_number"),
            "docstring": record.get("docstring"),
            "source_code": record.get("source_code"),
            "found": True,
            "ancestors": clean_nodes(record["ancestors"]),
            "descendants": clean_nodes(record["descendants"]),
        }
    
    # =========================================================================
    # File Queries
    # =========================================================================

    def get_repo_overview(self, repo_id: str) -> Dict[str, Any]:
        """Get an overview of a repository."""
        if not self.db.connected:
            # Return mock overview
            return {
                "repo": {
                    "id": repo_id,
                    "name": repo_id.split("/")[-1] if "/" in repo_id else repo_id,
                    "owner": repo_id.split("/")[0] if "/" in repo_id else "unknown"
                },
                "files": 25,
                "classes": 12,
                "functions": 89,
                "languages": ["Python", "TypeScript", "JavaScript"]
            }
            
        query = CYPHER_QUERIES["get_repo_overview"]
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        
        if records:
            record = records[0]
            return {
                "repo": record["repo"],
                "files": record["file_count"],
                "classes": record["class_count"],
                "functions": record["function_count"],
                "languages": record["languages"]
            }
        return {}
    
    # =========================================================================
    # Search Operations
    # =========================================================================
    
    # Python/JS keywords that should not be used as search identifiers
    _CODE_KEYWORDS = frozenset({
        'class', 'def', 'import', 'from', 'return', 'self', 'cls', 'None',
        'True', 'False', 'and', 'or', 'not', 'in', 'is', 'if', 'else',
        'elif', 'for', 'while', 'try', 'except', 'with', 'as', 'pass',
        'break', 'continue', 'raise', 'yield', 'async', 'await', 'lambda',
        'function', 'const', 'let', 'var', 'new', 'this', 'super',
    })

    def _extract_identifiers(self, query: str) -> List[str]:
        """Extract meaningful code identifiers from a raw query string."""
        tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', query)
        seen: set = set()
        result = []
        for t in tokens:
            if t not in self._CODE_KEYWORDS and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _escape_lucene_query(self, query: str) -> str:
        """
        Build a safe Lucene query string.

        Strategy:
        - Simple identifier (word chars only) → phrase search  e.g. "LoginRequest"
        - Complex query with special chars     → AND-keyword strategy
          e.g. "class GitHubRepo(BaseModel)" → "GitHubRepo" AND "BaseModel"
          This avoids Lucene parse errors while still matching meaningful tokens.
        """
        query = query.strip()
        if not query:
            return '*'

        # Simple identifier — use phrase search directly
        if re.match(r'^[A-Za-z0-9_]+$', query):
            return f'"{query}"'

        # Complex query — extract identifiers and build AND search
        identifiers = self._extract_identifiers(query)
        if identifiers:
            # Cap at 3 identifiers to keep the query lean
            return ' AND '.join(f'"{t}"' for t in identifiers[:3])

        # Pure symbols / numbers with no useful identifiers — wrap and hope for best
        escaped = query.replace('"', '\\"')
        return f'"{escaped}"'

    def _name_contains_fallback(
        self,
        search_term: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        CONTAINS fallback against node names.
        Uses the primary identifier extracted from the search term so that
        queries like 'class GitHubRepo(BaseModel)' still find 'GitHubRepo'.
        """
        identifiers = self._extract_identifiers(search_term)
        # Use the first (longest) identifier, falling back to raw term
        name_term = max(identifiers, key=len) if identifiers else search_term

        fallback = """
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
        """
        records, _, _ = self.db.run_query(fallback, {"name_term": name_term, "limit": limit})
        return [
            {
                "type": record["type"],
                "name": record["name"],
                "path": record["path"],
                "line_number": record["line_number"],
                "score": record["score"],
            }
            for record in records
        ]

    def search_code(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Search for code using a three-tier strategy:
        1. code_search fulltext index (symbols: name, docstring, source_code)
        2. Name CONTAINS fallback (uses primary extracted identifier)
        3. File content line search (file_content_search / source_code CONTAINS)
        Returns lean results: type, name, path, line_number, score.
        """
        escaped_term = self._escape_lucene_query(search_term)

        # Tier 1 — fulltext symbol search
        results: List[Dict[str, Any]] = []
        try:
            records, _, _ = self.db.run_query(
                CYPHER_QUERIES["search_code"], {"search_term": escaped_term}
            )
            results = [
                {
                    "type": record["type"],
                    "name": record["name"],
                    "path": record["path"],
                    "line_number": record["line_number"],
                    "score": record["score"],
                }
                for record in records
            ]
        except Exception as e:
            logger.warning("code_search fulltext failed: %s", e)

        # Tier 2 — name CONTAINS (works even when fulltext index misses)
        if not results:
            results = self._name_contains_fallback(search_term, limit=20)

        # Tier 3 — file content line search (raw code snippets, declarations, etc.)
        if not results:
            file_hits = self.search_file_content(search_term, limit=20)
            results = [
                {
                    "type": "line",
                    "name": h["match_line"].strip(),
                    "path": h["path"],
                    "line_number": h["line_number"],
                    "score": 0.5,
                }
                for h in file_hits
            ]

        return results

    # Skip files larger than this (bytes) in content search — avoids scanning minified/generated files
    _MAX_FILE_BYTES = 500_000

    def search_file_content(self, search_term: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Search file source code line by line — all scanning runs server-side in Cypher.

        Tier A: file_content_search fulltext index → split + unwind + CONTAINS in Neo4j.
        Tier B: source_code CONTAINS fallback (also server-side split/unwind).

        Files larger than 500 KB are skipped to avoid exploding intermediate row counts.
        Only matching lines are returned over the wire (not full source blobs).
        """
        escaped_term = self._escape_lucene_query(search_term)
        limit = min(limit, 200)

        # Tier A — fulltext to locate candidate files, then server-side line scan
        try:
            ft_query = """
            CALL db.index.fulltext.queryNodes('file_content_search', $search_term) YIELD node, score
            WHERE node.source_code IS NOT NULL
              AND size(node.source_code) < $max_bytes
            WITH node, score, split(node.source_code, '\n') as lines
            LIMIT 10
            UNWIND range(0, size(lines) - 1) AS idx
            WITH node.path AS path, lines[idx] AS line_content, idx + 1 AS line_number, score
            WHERE toLower(line_content) CONTAINS toLower($raw_term)
            RETURN path, line_number, line_content AS match_line
            ORDER BY score DESC, path, line_number
            LIMIT $limit
            """
            records, _, _ = self.db.run_query(ft_query, {
                "search_term": escaped_term,
                "raw_term": search_term,
                "max_bytes": self._MAX_FILE_BYTES,
                "limit": limit,
            })
            results = [
                {
                    "path": r["path"],
                    "line_number": r["line_number"],
                    "match_line": r["match_line"].rstrip() if r["match_line"] else "",
                }
                for r in records
            ]
            if results:
                return results
        except Exception as e:
            logger.warning("file_content_search fulltext failed: %s", e)

        # Tier B — CONTAINS on source_code, server-side split/unwind (no Python scanning)
        fallback_query = """
        MATCH (f:File)
        WHERE f.source_code IS NOT NULL
          AND f.source_code CONTAINS $raw_term
          AND size(f.source_code) < $max_bytes
        WITH f, split(f.source_code, '\n') AS lines
        LIMIT 5
        UNWIND range(0, size(lines) - 1) AS idx
        WITH f.path AS path, lines[idx] AS line_content, idx + 1 AS line_number
        WHERE line_content CONTAINS $raw_term
        RETURN path, line_number, line_content AS match_line
        ORDER BY path, line_number
        LIMIT $limit
        """
        records, _, _ = self.db.run_query(fallback_query, {
            "raw_term": search_term,
            "max_bytes": self._MAX_FILE_BYTES,
            "limit": limit,
        })
        return [
            {
                "path": r["path"],
                "line_number": r["line_number"],
                "match_line": r["match_line"].rstrip() if r["match_line"] else "",
            }
            for r in records
        ]

    def peek_file_lines(
        self,
        path: str,
        line: int,
        above: int = 10,
        below: int = 10,
    ) -> Dict[str, Any]:
        """
        Return a window of lines around `line` in the given file.
        Lines are 1-indexed. The anchor line is flagged with is_anchor=True.
        Files > 2 MB are rejected to prevent memory spikes.
        """
        query = """
        MATCH (f:File {path: $path})
        WHERE f.source_code IS NOT NULL AND size(f.source_code) < 2000000
        RETURN f.source_code as source_code
        LIMIT 1
        """
        records, _, _ = self.db.run_query(query, {"path": path})
        if not records or not records[0].get("source_code"):
            return {"error": "File not found or too large", "path": path}

        lines = records[0]["source_code"].split("\n")
        total = len(lines)
        start = max(0, line - above - 1)   # 0-indexed inclusive
        end = min(total, line + below)       # 0-indexed exclusive

        window = [
            {
                "line_number": i + 1,
                "content": lines[i],
                "is_anchor": (i + 1) == line,
            }
            for i in range(start, end)
        ]

        return {
            "path": path,
            "anchor_line": line,
            "window": window,
            "total_lines": total,
        }

    # =========================================================================
    # Impact Analysis
    # =========================================================================
    
    def analyze_change_impact(self, target_id: str) -> List[Dict[str, Any]]:
        """Analyze the impact of changing a specific code element."""
        query = CYPHER_QUERIES["analyze_change_impact"]
        records, _, _ = self.db.run_query(query, {"target_id": target_id})

        return [
            {
                "affected": record["affected"],
                "type": record["type"],
                "file": record["file"],
                "distance": record["distance"]
            }
            for record in records
        ]

    # =========================================================================
    # File Reconstruction
    # =========================================================================

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
        return [
            {
                "id": record["id"],
                "path": record["path"],
                "language": record["language"],
                "lines_count": record["lines_count"]
            }
            for record in records
        ]
    
    def reconstruct_file(self, file_id: str) -> Optional[str]:
        """
        Reconstruct a file's complete content from source_code property.

        Returns:
            Complete file source code or None if not found
        """
        query = """
        MATCH (f:File {id: $file_id})
        RETURN f.source_code as source_code, f.path as path
        """
        records, _, _ = self.db.run_query(query, {"file_id": file_id})

        if not records:
            return None

        source_code = records[0].get("source_code")
        if not source_code:
            # Fallback: try to reconstruct from individual code elements
            # This shouldn't be needed but provides a safety net
            print(f"⚠️  Warning: File {records[0].get('path')} has no source_code stored")
            return None

        return source_code

    def verify_repository_reconstruction(self, repo_id: str) -> Dict[str, Any]:
        """
        Verify that all files in a repository can be reconstructed.

        Args:
            repo_id: Repository ID

        Returns:
            Dict with verification stats
        """
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

        record = records[0]
        total_files = record["total_files"] or 0
        files_with_source = record["files_with_source"] or 0
        success_rate = (files_with_source / total_files * 100) if total_files > 0 else 0

        # Get list of files without source code
        problem_files_query = """
        MATCH (r:Repository)
        WHERE r.id = $repo_id OR r.repo = $repo_id
        MATCH (r)-[:CONTAINS*]->(f:File)
        WHERE f.source_code IS NULL
        RETURN f.path as path, f.id as file_id
        LIMIT 10
        """
        problem_records, _, _ = self.db.run_query(problem_files_query, {"repo_id": repo_id})

        return {
            "repo_id": repo_id,
            "total_files": total_files,
            "files_with_source": files_with_source,
            "files_without_source": total_files - files_with_source,
            "success_rate": f"{success_rate:.1f}%",
            "total_lines": record["total_lines"] or 0,
            "total_source_size_mb": (record["total_source_size"] or 0) / 1024 / 1024,
            "status": "✅ All files ready" if success_rate == 100 else f"⚠️  {total_files - files_with_source} files missing source",
            "problem_files": [r["path"] for r in problem_records] if problem_records else []
        }

    # =========================================================================
    # Config File Queries
    # =========================================================================
    
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
            {
                "id": record["id"],
                "path": record["path"],
                "file_type": record["file_type"],
                "project_name": record["project_name"],
                "version": record["version"],
                "lines_count": record["lines_count"]
            }
            for record in records
        ]
    
    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get all dependencies in a repository."""
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
            {
                "name": record["name"],
                "version": record["version"],
                "is_dev": record["is_dev"],
                "source": record["source"],
                "config_file": record["config_file"]
            }
            for record in records
        ]
    
    # =========================================================================
    # Hierarchy Navigation
    # =========================================================================

    def get_module_tree(self, repo_id: str) -> List[Dict[str, Any]]:
        """
        Get the complete module/directory tree for a repository.

        Args:
            repo_id: Repository ID

        Returns:
            List of modules with hierarchy information
        """
        query = CYPHER_QUERIES["get_module_tree"]
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})

        return [
            {
                "path": record["path"],
                "name": record["name"],
                "parent_path": record["parent_path"],
                "is_package": record["is_package"],
                "depth": record["depth"]
            }
            for record in records
        ]

    def get_file_hierarchy(self, file_id: str) -> List[Dict[str, Any]]:
        """
        Get the breadcrumb hierarchy for a file.

        Args:
            file_id: File ID

        Returns:
            Ordered list from repository to file
        """
        query = CYPHER_QUERIES["get_file_hierarchy"]
        records, _, _ = self.db.run_query(query, {"file_id": file_id})

        return [
            {
                "type": record["type"],
                "name": record["name"],
                "path": record["path"],
                "depth": record["depth"]
            }
            for record in records
        ]

    def get_directory_contents(
        self,
        repo_id: str,
        dir_path: str
    ) -> List[Dict[str, Any]]:
        """
        Get contents of a specific directory.

        Args:
            repo_id: Repository ID
            dir_path: Directory path

        Returns:
            List of files and subdirectories
        """
        query = CYPHER_QUERIES["get_directory_contents"]
        records, _, _ = self.db.run_query(query, {
            "repo_id": repo_id,
            "dir_path": dir_path
        })

        return [
            {
                "type": record["type"],
                "name": record["name"],
                "path": record["path"],
                "is_package": record["is_package"]
            }
            for record in records
        ]

    def get_symbols_at_lines(self, file_path: str, start_line: int, end_line: int) -> List[Dict[str, Any]]:
        """
        Find all symbols (functions, classes, variables) that overlap a line range in a file.
        Used to map diff hunks to affected code symbols.
        """
        query = """
        MATCH (f:File {path: $file_path})-[:CONTAINS]->(n)
        WHERE (n:Function OR n:Class OR n:Variable)
          AND n.line_number IS NOT NULL
          AND n.line_number <= $end_line
          AND coalesce(n.end_line, n.line_number) >= $start_line
        RETURN
            CASE
                WHEN n:Function THEN 'function'
                WHEN n:Class THEN 'class'
                ELSE 'variable'
            END as type,
            n.name as name,
            n.line_number as start_line,
            coalesce(n.end_line, n.line_number) as end_line,
            n.source as source,
            n.docstring as docstring,
            n.path as path
        ORDER BY n.line_number
        """
        records, _, _ = self.db.run_query(query, {
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
        })
        return [dict(r) for r in records]

    def get_symbols_at_lines_by_relative_path(self, repo_id: str, relative_path: str, start_line: int, end_line: int) -> List[Dict[str, Any]]:
        """
        Find all symbols overlapping a line range using repo-relative file path.
        """
        query = """
        MATCH (f:File)
        WHERE f.relative_path = $relative_path
          AND f.path CONTAINS $repo_id
        WITH f
        MATCH (f)-[:CONTAINS]->(n)
        WHERE (n:Function OR n:Class OR n:Variable)
          AND n.line_number IS NOT NULL
          AND n.line_number <= $end_line
          AND coalesce(n.end_line, n.line_number) >= $start_line
        RETURN
            CASE
                WHEN n:Function THEN 'function'
                WHEN n:Class THEN 'class'
                ELSE 'variable'
            END as type,
            n.name as name,
            n.line_number as start_line,
            coalesce(n.end_line, n.line_number) as end_line,
            n.source as source,
            n.docstring as docstring,
            f.relative_path as file_path
        ORDER BY n.line_number
        """
        records, _, _ = self.db.run_query(query, {
            "repo_id": repo_id,
            "relative_path": relative_path,
            "start_line": start_line,
            "end_line": end_line,
        })
        return [dict(r) for r in records]

    def get_diff_context(self, repo_id: str, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build full RAG context for a set of file changes (diff).

        Args:
            repo_id: Repository ID (e.g. "owner/repo")
            changes: List of {"file_path": "relative/path.py", "start_line": 10, "end_line": 30}

        Returns:
            Dict with affected_symbols, callers, hierarchy, and file_sources.
        """
        all_affected = []
        all_callers = []
        all_hierarchy = []
        file_sources = {}

        for change in changes:
            file_path = change["file_path"]
            start_line = change.get("start_line", 1)
            end_line = change.get("end_line", 999999)

            # 1. Find affected symbols
            symbols = self.get_symbols_at_lines_by_relative_path(
                repo_id, file_path, start_line, end_line
            )
            for s in symbols:
                s["change_file"] = file_path
            all_affected.extend(symbols)

            # 2. For each affected symbol, find callers
            for sym in symbols:
                caller_query = """
                MATCH (caller)-[:CALLS]->(target)
                WHERE target.name = $name AND target.path CONTAINS $repo_id
                RETURN
                    CASE
                        WHEN caller:Function THEN 'function'
                        WHEN caller:Class THEN 'class'
                        ELSE 'other'
                    END as type,
                    caller.name as name,
                    caller.path as path,
                    caller.line_number as line_number,
                    caller.source as source
                LIMIT 20
                """
                records, _, _ = self.db.run_query(caller_query, {
                    "name": sym["name"],
                    "repo_id": repo_id,
                })
                callers = [dict(r) for r in records]
                if callers:
                    all_callers.append({
                        "symbol": sym["name"],
                        "symbol_type": sym["type"],
                        "callers": callers,
                    })

            # 3. For affected classes, get hierarchy
            class_symbols = [s for s in symbols if s["type"] == "class"]
            for cls in class_symbols:
                hier_query = """
                MATCH (c:Class {name: $name})-[:INHERITS*0..5]->(parent:Class)
                WHERE c.path CONTAINS $repo_id
                RETURN parent.name as name, parent.path as path,
                       parent.source as source, parent.docstring as docstring
                """
                records, _, _ = self.db.run_query(hier_query, {
                    "name": cls["name"],
                    "repo_id": repo_id,
                })
                if records:
                    all_hierarchy.append({
                        "class": cls["name"],
                        "hierarchy": [dict(r) for r in records],
                    })

            # 4. Get file source
            file_src_query = """
            MATCH (f:File)
            WHERE f.relative_path = $relative_path AND f.path CONTAINS $repo_id
            RETURN f.source_code as source_code, f.relative_path as path
            LIMIT 1
            """
            records, _, _ = self.db.run_query(file_src_query, {
                "relative_path": file_path,
                "repo_id": repo_id,
            })
            if records and records[0].get("source_code"):
                file_sources[file_path] = records[0]["source_code"]

        return {
            "affected_symbols": all_affected,
            "callers": all_callers,
            "class_hierarchy": all_hierarchy,
            "file_sources": file_sources,
            "total_affected": len(all_affected),
            "total_files": len(changes),
        }

    def get_diff_context_enhanced(
        self, repo_id: str, changes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build full relationship context for every changed Function, Method, and Class.

        For each symbol in the diff line ranges:
          - Full source code of the symbol itself
          - Methods belonging to each changed Class (with source)
          - Callers: every Function/Method that calls this symbol via CALLS edges
          - Dependencies: every Function/Method this symbol calls via CALLS edges
          - Imports: other in-repo symbols imported by the changed file
          - Class hierarchy: parent/child classes for any changed Class

        Both Function and Method node types are handled throughout — the graph
        stores class methods as Function nodes under the class via CONTAINS.
        """
        from api.services.code_search import CodeFinder

        code_finder = CodeFinder(self.db)

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

            # ── 1. Affected symbols (Function, Class, and Method) ─────────────
            # Method nodes are stored under Class via CONTAINS — we surface them
            # here so the agent sees both the class body and individual methods.
            affected_query = """
            MATCH (f:File {repo: $repo_id})
            WHERE f.path ENDS WITH $file_path
            MATCH (f)-[:CONTAINS]->(n)
            WHERE (n:Function OR n:Class OR n:Method)
              AND n.line_number IS NOT NULL
              AND n.line_number <= $end_line
              AND coalesce(n.end_line, n.line_number) >= $start_line
            RETURN
                CASE
                    WHEN n:Class    THEN 'class'
                    WHEN n:Method   THEN 'method'
                    ELSE                 'function'
                END AS type,
                n.name          AS name,
                n.line_number   AS start_line,
                coalesce(n.end_line, n.line_number) AS end_line,
                n.source        AS source,
                n.docstring     AS docstring,
                n.args          AS args,
                f.path          AS file_path
            ORDER BY n.line_number
            """
            records, _, _ = self.db.run_query(affected_query, {
                "repo_id": repo_id,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
            })
            symbols = [dict(r) for r in records]

            for s in symbols:
                s["change_file"] = file_path
                sym_key = f"{s['file_path']}:{s['name']}:{s['start_line']}"
                if sym_key not in seen_affected:
                    seen_affected.add(sym_key)
                    all_affected.append(s)

            for sym in symbols:
                caller_key = f"{sym['file_path']}:{sym['name']}"
                if caller_key in seen_callers:
                    continue
                seen_callers.add(caller_key)

                # ── 2. Callers: who calls this symbol? ────────────────────────
                # Match both Function and Method callers so class methods that
                # call the changed code are not missed.
                try:
                    caller_query = """
                    MATCH (target)
                    WHERE (target:Function OR target:Method OR target:Class)
                      AND target.name = $name
                      AND coalesce(target.path, '') CONTAINS $repo_id
                    WITH target
                    MATCH (caller)-[call:CALLS]->(target)
                    WHERE (caller:Function OR caller:Method)
                      AND NOT coalesce(caller.is_dependency, false)
                    OPTIONAL MATCH (caller_file:File)-[:CONTAINS]->(caller)
                    RETURN DISTINCT
                        caller.name             AS caller_name,
                        CASE WHEN caller:Method THEN 'method' ELSE 'function' END AS caller_type,
                        coalesce(caller.path, caller_file.path) AS caller_path,
                        caller.line_number      AS caller_line,
                        call.line_number        AS call_line,
                        call.args               AS call_args
                    ORDER BY caller_path, caller.line_number
                    LIMIT 10
                    """
                    caller_records, _, _ = self.db.run_query(
                        caller_query, {"name": sym["name"], "repo_id": repo_id}
                    )
                    callers_list = [dict(r) for r in caller_records]
                    if callers_list:
                        all_callers.append({
                            "symbol": sym["name"],
                            "symbol_type": sym["type"],
                            "callers": callers_list,
                        })
                except Exception as e:
                    logger.warning(f"Error finding callers for {sym['name']}: {e}")

                # ── 3. Dependencies: what does this symbol call? ──────────────
                # Match both Function and Method targets to catch calls to methods.
                try:
                    dep_query = """
                    MATCH (caller)
                    WHERE (caller:Function OR caller:Method)
                      AND caller.name = $name
                      AND caller.path = $path
                    MATCH (caller)-[call:CALLS]->(called)
                    WHERE (called:Function OR called:Method)
                      AND NOT coalesce(called.is_dependency, false)
                    RETURN DISTINCT
                        called.name  AS called_name,
                        CASE WHEN called:Method THEN 'method' ELSE 'function' END AS called_type,
                        called.path  AS called_path,
                        call.line_number AS call_line,
                        call.args    AS call_args
                    ORDER BY call.line_number
                    LIMIT 15
                    """
                    dep_records, _, _ = self.db.run_query(dep_query, {
                        "name": sym["name"],
                        "path": sym["file_path"],
                    })
                    deps_list = [dict(r) for r in dep_records]
                    if deps_list:
                        all_dependencies.append({
                            "symbol": sym["name"],
                            "dependencies": deps_list,
                        })
                except Exception as e:
                    logger.warning(f"Error finding dependencies for {sym['name']}: {e}")

                # ── 4. Class methods: when a Class is affected, fetch all ──────
                # its methods with full source so the agent sees what each
                # method does — not just the class skeleton.
                if sym["type"] == "class":
                    try:
                        methods_query = """
                        MATCH (cls:Class {name: $class_name, path: $path})
                        MATCH (cls)-[:CONTAINS]->(m:Function)
                        RETURN
                            m.name          AS name,
                            m.line_number   AS line_number,
                            coalesce(m.end_line, m.line_number) AS end_line,
                            m.source        AS source,
                            m.docstring     AS docstring,
                            m.args          AS args
                        ORDER BY m.line_number
                        """
                        method_records, _, _ = self.db.run_query(methods_query, {
                            "class_name": sym["name"],
                            "path": sym["file_path"],
                        })
                        methods_list = [dict(r) for r in method_records]
                        if methods_list:
                            sym["methods"] = methods_list
                    except Exception as e:
                        logger.warning(f"Error fetching methods for class {sym['name']}: {e}")

            # ── 5. Imports: resolve imported names to in-repo source ──────────
            import_query = """
            MATCH (f:File {repo: $repo_id})
            WHERE f.path ENDS WITH $file_path
            MATCH (f)-[r:IMPORTS]->(m)
            RETURN
                r.alias          AS alias,
                r.imported_name  AS imported_name,
                m.name           AS module_name,
                r.line_number    AS line_number
            ORDER BY r.line_number
            LIMIT 20
            """
            import_records, _, _ = self.db.run_query(import_query, {
                "repo_id": repo_id,
                "file_path": file_path,
            })

            for imp in import_records:
                imported_name = imp.get("imported_name") or imp.get("alias")
                if not imported_name or imported_name in seen_imports:
                    continue
                seen_imports.add(imported_name)

                try:
                    func_results = code_finder.find_by_function_name(imported_name, fuzzy_search=False)
                    if func_results:
                        for func in func_results[:1]:
                            if func.get("path") and repo_id in func.get("path", ""):
                                all_imports.append({
                                    "name": imported_name,
                                    "type": "function",
                                    "source": func.get("source", ""),
                                    "path": func.get("path"),
                                    "line": func.get("line_number"),
                                    "docstring": func.get("docstring"),
                                    "from_file": file_path,
                                })
                    else:
                        class_results = code_finder.find_by_class_name(imported_name, fuzzy_search=False)
                        if class_results:
                            for cls in class_results[:1]:
                                if cls.get("path") and repo_id in cls.get("path", ""):
                                    all_imports.append({
                                        "name": imported_name,
                                        "type": "class",
                                        "source": cls.get("source", ""),
                                        "path": cls.get("path"),
                                        "line": cls.get("line_number"),
                                        "docstring": cls.get("docstring"),
                                        "from_file": file_path,
                                    })
                except Exception as e:
                    logger.warning(f"Error resolving import {imported_name}: {e}")

            # ── 6. Class hierarchy for affected classes ───────────────────────
            for cls in [s for s in symbols if s["type"] == "class"]:
                try:
                    hierarchy_info = code_finder.find_class_hierarchy(
                        cls["name"], cls.get("file_path")
                    )
                    if hierarchy_info:
                        all_hierarchy.append({
                            "class": cls["name"],
                            "parents": hierarchy_info.get("parent_classes", []),
                            "children": hierarchy_info.get("child_classes", []),
                            "methods": hierarchy_info.get("methods", []),
                        })
                except Exception as e:
                    logger.warning(f"Error finding hierarchy for {cls['name']}: {e}")

        return {
            "affected_symbols": all_affected,
            "callers": all_callers,
            "imports": all_imports,
            "dependencies": all_dependencies,
            "class_hierarchy": all_hierarchy,
            "total_affected": len(all_affected),
            "total_imports": len(all_imports),
            "total_files": len(set(c["file_path"] for c in changes)),
        }