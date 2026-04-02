"""
Neo4j Graph Schema for Code Ingestion

Defines the graph structure, constraints, indexes, and Cypher queries
for storing and querying code in Neo4j.

Graph Structure:
================
(:User) -[:OWNS]-> (:Repository) -[:CONTAINS]-> (:Module) -[:CONTAINS]-> (:File)
(:File) -[:DEFINES]-> (:Class|:Function|:Variable)
(:File) -[:HAS_IMPORT]-> (:Import)
(:File) -[:IMPORTS]-> (:File|:Module)
(:Class) -[:HAS_METHOD]-> (:Method)
(:Class) -[:HAS_ATTRIBUTE]-> (:Attribute)
(:Class) -[:INHERITS]-> (:Class)
(:Method|:Function) -[:CALLS]-> (:Symbol)
(:Symbol) - Persistent symbol table for all code elements with qualified names
(:Module) -[:CONTAINS {depth: int}]-> (:Module|:File) - Hierarchical navigation
"""

from typing import Any, Dict, Optional

from .client import Neo4jClient


class CodeGraphSchema:
    """Manages the Neo4j schema for code ingestion."""

    def __init__(self, client: Neo4jClient):
        """
        Initialize schema manager.

        Args:
            client: Neo4j database client
        """
        self.db = client

    def create_constraints_and_indexes(self) -> None:
        """Create all necessary constraints and indexes for the code graph."""

        constraints = [
            # Unique constraints
            "CREATE CONSTRAINT user_username IF NOT EXISTS FOR (u:User) REQUIRE u.username IS UNIQUE",
            # Repository uniqueness is handled by graph_builder's constraint on r.repo (owner/name format)
            # "CREATE CONSTRAINT repo_unique IF NOT EXISTS FOR (r:Repository) REQUIRE (r.owner, r.name) IS UNIQUE",
            "CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE (f.repo_id, f.path) IS UNIQUE",
            "CREATE CONSTRAINT branch_unique IF NOT EXISTS FOR (b:Branch) REQUIRE (b.repo_id, b.name) IS UNIQUE",
            "CREATE CONSTRAINT config_file_path IF NOT EXISTS FOR (cf:ConfigFile) REQUIRE (cf.repo_id, cf.path) IS UNIQUE",
            "CREATE CONSTRAINT symbol_id IF NOT EXISTS FOR (s:Symbol) REQUIRE s.id IS UNIQUE",
        ]

        indexes = [
            # Performance indexes
            "CREATE INDEX file_language IF NOT EXISTS FOR (f:File) ON (f.language)",
            "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.name)",
            "CREATE INDEX function_name IF NOT EXISTS FOR (f:Function) ON (f.name)",
            "CREATE INDEX method_name IF NOT EXISTS FOR (m:Method) ON (m.name)",
            "CREATE INDEX module_path IF NOT EXISTS FOR (m:Module) ON (m.path)",
            "CREATE INDEX module_hierarchy IF NOT EXISTS FOR (m:Module) ON (m.repo_id, m.depth)",
            "CREATE INDEX import_module IF NOT EXISTS FOR (i:Import) ON (i.module)",
            "CREATE INDEX variable_name IF NOT EXISTS FOR (v:Variable) ON (v.name)",
            # Symbol table indexes
            "CREATE INDEX symbol_name IF NOT EXISTS FOR (s:Symbol) ON (s.name)",
            "CREATE INDEX symbol_qualified_name IF NOT EXISTS FOR (s:Symbol) ON (s.qualified_name)",
            "CREATE INDEX symbol_scope_lookup IF NOT EXISTS FOR (s:Symbol) ON (s.file_id, s.scope)",
            "CREATE INDEX symbol_type IF NOT EXISTS FOR (s:Symbol) ON (s.type)",
            # Config file indexes
            "CREATE INDEX config_file_type IF NOT EXISTS FOR (cf:ConfigFile) ON (cf.file_type)",
            "CREATE INDEX dependency_name IF NOT EXISTS FOR (d:Dependency) ON (d.name)",
            "CREATE INDEX dependency_source IF NOT EXISTS FOR (d:Dependency) ON (d.source)",
            # Full-text search indexes for code search (including source_code)
            "CREATE FULLTEXT INDEX code_search IF NOT EXISTS FOR (n:Class|Function|Method|Variable|Symbol) ON EACH [n.name, n.docstring, n.source_code]",
            "CREATE FULLTEXT INDEX symbol_search IF NOT EXISTS FOR (s:Symbol) ON EACH [s.name, s.qualified_name, s.docstring]",
            "CREATE FULLTEXT INDEX file_content_search IF NOT EXISTS FOR (f:File) ON EACH [f.source_code]",
            # Vector indexes for semantic search (embedding stored on each node)
            # dimensions=1536 matches text-embedding-3-small output size
            "CREATE VECTOR INDEX function_semantic IF NOT EXISTS FOR (n:Function) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
            "CREATE VECTOR INDEX class_semantic IF NOT EXISTS FOR (n:Class) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
            "CREATE VECTOR INDEX method_semantic IF NOT EXISTS FOR (n:Method) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
            "CREATE VECTOR INDEX file_semantic IF NOT EXISTS FOR (n:File) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
        ]

        for constraint in constraints:
            try:
                self.db.run_query(constraint)
                print("✓ Created constraint")
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"⚠ Constraint warning: {e}")

        for index in indexes:
            try:
                self.db.run_query(index)
                print("✓ Created index")
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"⚠ Index warning: {e}")

    def clear_repository(self, repo_id: str) -> None:
        """
        Clear all data for a specific repository before re-ingestion.

        Args:
            repo_id: Repository ID (format: username/repo_name)
        """
        query = """
        MATCH (r:Repository {id: $repo_id})
        OPTIONAL MATCH (r)-[*]->(n)
        DETACH DELETE n, r
        """
        self.db.run_query(query, {"repo_id": repo_id})
        print(f"✓ Cleared repository: {repo_id}")

    def update_repository_commit(self, repo_id: str, commit_hash: str) -> None:
        """
        Update the last commit hash for a repository.

        Args:
            repo_id: Repository ID
            commit_hash: Git commit hash
        """
        query = """
        MATCH (r:Repository {id: $repo_id})
        SET r.last_commit_hash = $commit_hash,
            r.last_updated_at = datetime()
        RETURN r
        """
        self.db.run_query(query, {"repo_id": repo_id, "commit_hash": commit_hash})

    def get_repository_commit(self, repo_id: str) -> Optional[str]:
        """
        Get the last ingested commit hash for a repository.

        Args:
            repo_id: Repository ID

        Returns:
            Commit hash or None if not set
        """
        query = """
        MATCH (r:Repository {id: $repo_id})
        RETURN r.last_commit_hash as commit_hash
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        if records and records[0]["commit_hash"]:
            return records[0]["commit_hash"]
        return None

    def get_repository_metadata(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """
        Get repository metadata including local path, URL, etc.

        Args:
            repo_id: Repository ID

        Returns:
            Dictionary with repository metadata or None if not found
        """
        query = """
        MATCH (r:Repository {id: $repo_id})
        RETURN r.id as id,
               r.name as name,
               r.owner as owner,
               r.path as local_path,
               r.url as url,
               r.default_branch as default_branch,
               r.last_commit_hash as last_commit_hash
        """
        records, _, _ = self.db.run_query(query, {"repo_id": repo_id})
        if records:
            return dict(records[0])
        return None

    def delete_file_nodes(self, repo_id: str, file_path: str) -> None:
        """
        Delete all nodes associated with a specific file.

        Deletes the File node and all code entities (classes, functions, methods, variables)
        that belong to this file, including their relationships.

        Args:
            repo_id: Repository ID
            file_path: Path to the file
        """
        # Delete all nodes associated with the file in a single transaction
        query = """
        // Find the file
        MATCH (f:File {repo_id: $repo_id, path: $file_path})

        // Find all classes in the file
        OPTIONAL MATCH (f)-[:CONTAINS_CLASS]->(c:Class)
        OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Method)

        // Find all functions in the file
        OPTIONAL MATCH (f)-[:CONTAINS_FUNCTION]->(func:Function)

        // Find all variables in the file
        OPTIONAL MATCH (f)-[:DEFINES_VARIABLE]->(v:Variable)

        // Find all imports in the file
        OPTIONAL MATCH (f)-[:IMPORTS]->(i:Import)

        // Detach and delete all nodes (removes all relationships automatically)
        DETACH DELETE f, c, m, func, v, i
        """
        self.db.run_query(query, {"repo_id": repo_id, "file_path": file_path})

    def rename_file_in_graph(self, repo_id: str, old_path: str, new_path: str) -> None:
        """
        Rename a file in the graph database.

        Updates the File node path and all related code entities that reference
        the file path (qualified names, etc.).

        Args:
            repo_id: Repository ID
            old_path: Old file path
            new_path: New file path
        """
        query = """
        // Update File node
        MATCH (f:File {repo_id: $repo_id, path: $old_path})
        SET f.path = $new_path,
            f.name = split($new_path, '/')[-1],
            f.last_updated = datetime()

        // Update all classes in the file
        WITH f
        OPTIONAL MATCH (f)-[:CONTAINS_CLASS]->(c:Class)
        SET c.file_path = $new_path

        // Update all methods in those classes
        WITH f, c
        OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Method)
        SET m.file_path = $new_path

        // Update all functions in the file
        WITH f
        OPTIONAL MATCH (f)-[:CONTAINS_FUNCTION]->(func:Function)
        SET func.file_path = $new_path

        // Update all variables in the file
        WITH f
        OPTIONAL MATCH (f)-[:DEFINES_VARIABLE]->(v:Variable)
        SET v.file_path = $new_path

        RETURN f
        """
        self.db.run_query(query, {"repo_id": repo_id, "old_path": old_path, "new_path": new_path})

    def delete_module_nodes(self, repo_id: str, module_path: str) -> None:
        """
        Delete a module (folder) node and its relationships.

        Args:
            repo_id: Repository ID
            module_path: Path to the module/folder
        """
        query = """
        MATCH (m:Module {repo_id: $repo_id, path: $module_path})
        DETACH DELETE m
        """
        self.db.run_query(query, {"repo_id": repo_id, "module_path": module_path})

    def rename_module_in_graph(self, repo_id: str, old_path: str, new_path: str) -> None:
        """
        Rename a module (folder) in the graph database.

        Updates the Module node path and all descendant modules/files.

        Args:
            repo_id: Repository ID
            old_path: Old module path
            new_path: New module path
        """
        query = """
        // Update Module node
        MATCH (m:Module {repo_id: $repo_id, path: $old_path})
        SET m.path = $new_path,
            m.name = split($new_path, '/')[-1],
            m.parent_path = CASE
                WHEN size(split($new_path, '/')) > 1
                THEN reduce(s = '', part IN split($new_path, '/')[0..-2] |
                    CASE WHEN s = '' THEN part ELSE s + '/' + part END)
                ELSE null
            END

        // Update all child modules
        WITH m
        MATCH (child:Module)
        WHERE child.repo_id = $repo_id
          AND child.path STARTS WITH $old_path + '/'
        WITH child, $old_path as old, $new_path as new
        SET child.path = new + substring(child.path, size(old)),
            child.parent_path = CASE
                WHEN child.parent_path = old THEN new
                WHEN child.parent_path STARTS WITH old + '/'
                THEN new + substring(child.parent_path, size(old))
                ELSE child.parent_path
            END

        RETURN child
        """
        self.db.run_query(query, {"repo_id": repo_id, "old_path": old_path, "new_path": new_path})


# =============================================================================
# Cypher Queries
# =============================================================================

CYPHER_QUERIES = {
    # -------------------------------------------------------------------------
    # Graph Statistics
    # -------------------------------------------------------------------------
    "get_graph_stats": """
        MATCH (r:Repository)
        OPTIONAL MATCH (r)-[:CONTAINS*]->(f:File)
        OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)
        OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function)
        OPTIONAL MATCH (f)-[:CONTAINS]->(v:Variable)
        OPTIONAL MATCH (f)-[:IMPORTS]->(m:Module)
        RETURN
            count(DISTINCT r) as repositories,
            count(DISTINCT f) as files,
            count(DISTINCT c) as classes,
            count(DISTINCT fn) as functions,
            count(DISTINCT v) as variables,
            count(DISTINCT m) as modules
    """,
    "get_repo_stats": """
        MATCH (r:Repository)
        WHERE r.repo = $repo_id OR r.id = $repo_id
        OPTIONAL MATCH (r)-[:CONTAINS*]->(f:File)
        WITH r, collect(DISTINCT f) as files
        WITH r, files,
             reduce(s = 0, f IN files | s + COALESCE(f.lines_count, 0)) as line_count
        UNWIND CASE WHEN size(files) = 0 THEN [null] ELSE files END AS f
        OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)
        OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function)
        OPTIONAL MATCH (f)-[:IMPORTS]->(m:Module)
        RETURN
            count(DISTINCT f) as file_count,
            count(DISTINCT c) as class_count,
            count(DISTINCT fn) as function_count,
            0 as method_count,
            line_count,
            count(DISTINCT m) as import_count,
            [lang IN collect(DISTINCT f.language) WHERE lang IS NOT NULL AND lang <> 'unknown'] as languages
    """,
    # -------------------------------------------------------------------------
    # User Operations
    # -------------------------------------------------------------------------
    "create_user": """
        MERGE (u:User {username: $username})
        ON CREATE SET u.email = $email, u.created_at = datetime()
        RETURN u
    """,
    # -------------------------------------------------------------------------
    # Symbol Table Operations
    # -------------------------------------------------------------------------
    "create_symbol": """
        MERGE (s:Symbol {id: $symbol_id})
        ON CREATE SET
            s.name = $name,
            s.qualified_name = $qualified_name,
            s.type = $type,
            s.scope = $scope,
            s.file_id = $file_id,
            s.line_start = $line_start,
            s.line_end = $line_end,
            s.visibility = $visibility,
            s.is_exported = $is_exported,
            s.docstring = $docstring,
            s.source_code = $source_code,
            s.created_at = datetime()
        ON MATCH SET
            s.docstring = $docstring,
            s.source_code = $source_code,
            s.line_start = $line_start,
            s.line_end = $line_end,
            s.updated_at = datetime()
        RETURN s
    """,
    "link_symbol_to_definition": """
        MATCH (s:Symbol {id: $symbol_id})
        MATCH (def) WHERE def.id = $definition_id
        MERGE (s)-[:REFERENCES]->(def)
        RETURN s, def
    """,
    # -------------------------------------------------------------------------
    # Repository Operations
    # -------------------------------------------------------------------------
    "create_repository": """
        MATCH (u:User {username: $username})
        MERGE (r:Repository {repo: $repo_id})
        ON CREATE SET
            r.owner = $username,
            r.name = $repo_name,
            r.url = $url,
            r.path = $local_path,
            r.description = $description,
            r.default_branch = $default_branch,
            r.created_at = datetime()
        SET r.last_ingested = datetime()
        MERGE (u)-[:OWNS]->(r)
        RETURN r
    """,
    "create_branch": """
        MATCH (r:Repository {id: $repo_id})
        MERGE (b:Branch {repo_id: $repo_id, name: $branch_name})
        ON CREATE SET b.commit_sha = $commit_sha, b.is_default = $is_default
        MERGE (r)-[:HAS_BRANCH]->(b)
        RETURN b
    """,
    # -------------------------------------------------------------------------
    # Module Operations
    # -------------------------------------------------------------------------
    "create_module": """
        MATCH (r:Repository {id: $repo_id})
        MERGE (m:Module {repo_id: $repo_id, path: $path})
        ON CREATE SET
            m.name = $name,
            m.type = $type,
            m.is_package = $is_package,
            m.depth = $depth,
            m.parent_path = $parent_path
        ON MATCH SET
            m.depth = $depth,
            m.parent_path = $parent_path
        WITH r, m
        CALL (r, m) {
            WITH r, m WHERE $parent_path IS NULL
            MERGE (r)-[:CONTAINS {depth: 0}]->(m)
            RETURN 1 as done
            UNION
            WITH r, m WHERE $parent_path IS NOT NULL
            MATCH (parent:Module {repo_id: $repo_id, path: $parent_path})
            MERGE (parent)-[:CONTAINS {depth: $depth}]->(m)
            RETURN 1 as done
        }
        RETURN m
    """,
    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------
    "create_file": """
        MATCH (r:Repository {id: $repo_id})
        MERGE (f:File {repo_id: $repo_id, path: $path})
        ON CREATE SET
            f.id = $file_id,
            f.extension = $extension
        SET f.name = $name,
            f.language = $language,
            f.lines_count = $lines_count,
            f.sha = $sha,
            f.size = $size,
            f.source_code = $source_code,
            f.last_updated = datetime()
        WITH r, f
        OPTIONAL MATCH (m:Module {repo_id: $repo_id, path: $module_path})
        FOREACH (_ IN CASE WHEN m IS NOT NULL THEN [1] ELSE [] END |
            MERGE (m)-[:CONTAINS]->(f)
        )
        FOREACH (_ IN CASE WHEN m IS NULL THEN [1] ELSE [] END |
            MERGE (r)-[:CONTAINS]->(f)
        )
        RETURN f
    """,
    # -------------------------------------------------------------------------
    # Import Operations
    # -------------------------------------------------------------------------
    "create_import": """
        MATCH (f:File {id: $file_id})
        MERGE (i:Import {file_id: $file_id, module: $module, line_start: $line_start})
        ON CREATE SET
            i.alias = $alias,
            i.line_end = $line_end,
            i.is_from_import = $is_from_import,
            i.imported_names = $imported_names
        MERGE (f)-[:HAS_IMPORT]->(i)
        RETURN i
    """,
    # -------------------------------------------------------------------------
    # Class Operations
    # -------------------------------------------------------------------------
    "create_class": """
        MATCH (f:File {id: $file_id})
        MERGE (c:Class {file_id: $file_id, name: $name, line_start: $line_start})
        ON CREATE SET
            c.id = $class_id,
            c.line_end = $line_end,
            c.docstring = $docstring,
            c.decorators = $decorators,
            c.base_classes = $base_classes,
            c.is_abstract = $is_abstract,
            c.source_code = $source_code
        SET c.source_code = $source_code
        MERGE (f)-[:DEFINES]->(c)
        RETURN c
    """,
    "create_method": """
        MATCH (c:Class {id: $class_id})
        MERGE (m:Method {class_id: $class_id, name: $name, line_start: $line_start})
        ON CREATE SET
            m.id = $method_id,
            m.line_end = $line_end,
            m.params = $params,
            m.return_type = $return_type,
            m.docstring = $docstring,
            m.decorators = $decorators,
            m.is_async = $is_async,
            m.is_static = $is_static,
            m.is_classmethod = $is_classmethod,
            m.is_property = $is_property,
            m.visibility = $visibility,
            m.source_code = $source_code
        SET m.source_code = $source_code
        MERGE (c)-[:HAS_METHOD]->(m)
        RETURN m
    """,
    "create_attribute": """
        MATCH (c:Class {id: $class_id})
        MERGE (a:Attribute {class_id: $class_id, name: $name})
        ON CREATE SET
            a.line_start = $line_start,
            a.line_end = $line_end,
            a.type_annotation = $type_annotation,
            a.default_value = $default_value,
            a.visibility = $visibility
        MERGE (c)-[:HAS_ATTRIBUTE]->(a)
        RETURN a
    """,
    # -------------------------------------------------------------------------
    # Function & Variable Operations
    # -------------------------------------------------------------------------
    "create_function": """
        MATCH (f:File {id: $file_id})
        MERGE (fn:Function {file_id: $file_id, name: $name, line_start: $line_start})
        ON CREATE SET
            fn.id = $function_id,
            fn.line_end = $line_end,
            fn.params = $params,
            fn.return_type = $return_type,
            fn.docstring = $docstring,
            fn.decorators = $decorators,
            fn.is_async = $is_async,
            fn.source_code = $source_code
        SET fn.source_code = $source_code
        MERGE (f)-[:DEFINES]->(fn)
        RETURN fn
    """,
    "create_variable": """
        MATCH (f:File {id: $file_id})
        MERGE (v:Variable {file_id: $file_id, name: $name, line_start: $line_start})
        ON CREATE SET
            v.id = $variable_id,
            v.line_end = $line_end,
            v.type_annotation = $type_annotation,
            v.is_constant = $is_constant,
            v.scope = $scope
        MERGE (f)-[:DEFINES]->(v)
        RETURN v
    """,
    # -------------------------------------------------------------------------
    # Relationship Operations
    # -------------------------------------------------------------------------
    "create_call_relationship": """
        MATCH (caller) WHERE caller.id = $caller_id
        MATCH (s:Symbol {id: $symbol_id})
        MERGE (caller)-[r:CALLS {line_number: $line_number}]->(s)
        ON CREATE SET
            r.arguments = $arguments,
            r.column = $column,
            r.qualified_target = $qualified_target,
            r.is_resolved = true
        RETURN r
    """,
    "create_call_relationship_unresolved": """
        MATCH (caller) WHERE caller.id = $caller_id
        MERGE (u:UnresolvedSymbol {name: $callee_name, caller_id: $caller_id, line: $line_number})
        ON CREATE SET u.created_at = datetime()
        MERGE (caller)-[r:CALLS {line_number: $line_number}]->(u)
        ON CREATE SET
            r.arguments = $arguments,
            r.column = $column,
            r.is_resolved = false
        RETURN r
    """,
    "create_inheritance": """
        MATCH (child:Class {id: $child_id})
        MATCH (parent:Class {name: $parent_name})
        WHERE parent.file_id STARTS WITH split($child_id, ':')[0]
           OR parent.repo_id = $repo_id
        MERGE (child)-[:INHERITS]->(parent)
    """,
    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------
    "find_method_usages": """
        MATCH (m:Function {name: $method_name})
        WHERE $repo_id IS NULL OR m.repo = $repo_id
        OPTIONAL MATCH (f:File)-[:CONTAINS]->(m)
        OPTIONAL MATCH (caller:Function)-[call:CALLS]->(m)
        WHERE $repo_id IS NULL OR caller.repo = $repo_id
        OPTIONAL MATCH (caller_file:File)-[:CONTAINS]->(caller)
        RETURN m.name                              AS method_name,
               coalesce(m.line_number, m.line_start) AS line_number,
               m.docstring                         AS docstring,
               coalesce(m.source_code, m.source)   AS source_code,
               m.cyclomatic_complexity             AS complexity,
               f.path                              AS file_path,
               collect(DISTINCT {
                   caller_name: caller.name,
                   caller_type: labels(caller)[0],
                   line: call.line_number,
                   file: caller_file.path
               }) AS callers
    """,
    "find_function_definition": """
        MATCH (m)
        WHERE (m:Function OR m:Class) AND m.name = $name
          AND ($repo_id IS NULL OR m.repo = $repo_id)
        OPTIONAL MATCH (f:File)-[:CONTAINS]->(m)
        OPTIONAL MATCH (f2:File)-[:DEFINES]->(m)
        OPTIONAL MATCH (cls:Class)-[:CONTAINS]->(m)
        RETURN m.name as name,
               coalesce(m.line_number, m.line_start) as line_number,
               coalesce(m.end_line_number, m.line_end) as end_line_number,
               m.docstring as docstring,
               m.cyclomatic_complexity as complexity,
               coalesce(m.source_code, m.source) as source_code,
               coalesce(f.path, f2.path) as file_path,
               coalesce(f.path, f2.path) as relative_path,
               cls.name as class_name,
               labels(m)[0] as symbol_type
        ORDER BY coalesce(f.path, f2.path)
        LIMIT 5
    """,
    "find_callers": """
        MATCH (m {name: $name})
        WHERE m:Function OR m:Class
        MATCH (caller)-[c:CALLS]->(m)
        WHERE $repo_id IS NULL OR caller.repo = $repo_id
        OPTIONAL MATCH (f:File)-[:CONTAINS]->(caller)
        RETURN caller.name as caller_name, labels(caller)[0] as caller_type,
               f.path as file_path, c.line_number as call_line,
               caller.source_code as source_code
        ORDER BY f.path, c.line_number
    """,
    "get_class_hierarchy": """
        MATCH (c:Class {name: $class_name})
        WHERE $repo_id IS NULL OR c.repo = $repo_id
        OPTIONAL MATCH (self_file:File)-[:CONTAINS]->(c)
        OPTIONAL MATCH (self_file2:File)-[:DEFINES]->(c)
        OPTIONAL MATCH (c)-[:INHERITS*]->(ancestor:Class)
        OPTIONAL MATCH (ancestor_file:File)-[:CONTAINS]->(ancestor)
        OPTIONAL MATCH (ancestor_file2:File)-[:DEFINES]->(ancestor)
        OPTIONAL MATCH (descendant:Class)-[:INHERITS*]->(c)
        OPTIONAL MATCH (descendant_file:File)-[:CONTAINS]->(descendant)
        OPTIONAL MATCH (descendant_file2:File)-[:DEFINES]->(descendant)
        RETURN c.name as class_name,
               coalesce(c.line_number, c.line_start) as line_number,
               c.docstring as docstring,
               coalesce(c.source_code, c.source) as source_code,
               coalesce(self_file.path, self_file2.path) as file_path,
               collect(DISTINCT {
                   name: ancestor.name,
                   file_path: coalesce(ancestor_file.path, ancestor_file2.path),
                   line_number: coalesce(ancestor.line_number, ancestor.line_start),
                   source_code: coalesce(ancestor.source_code, ancestor.source),
                   docstring: ancestor.docstring
               }) as ancestors,
               collect(DISTINCT {
                   name: descendant.name,
                   file_path: coalesce(descendant_file.path, descendant_file2.path),
                   line_number: coalesce(descendant.line_number, descendant.line_start),
                   source_code: coalesce(descendant.source_code, descendant.source),
                   docstring: descendant.docstring
               }) as descendants
    """,
    "get_repo_overview": """
        MATCH (r:Repository {id: $repo_id})
        OPTIONAL MATCH (r)-[:CONTAINS*]->(f:File)
        OPTIONAL MATCH (f)-[:DEFINES]->(c:Class)
        OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
        RETURN r.name as repo,
               count(DISTINCT f) as file_count,
               count(DISTINCT c) as class_count,
               count(DISTINCT fn) as function_count,
               collect(DISTINCT f.language) as languages
    """,
    "get_module_tree": """
        MATCH (r:Repository {id: $repo_id})
        OPTIONAL MATCH path = (r)-[:CONTAINS*]->(m:Module)
        WITH m, length(path) as depth
        WHERE m IS NOT NULL
        RETURN m.path as path,
               m.name as name,
               m.parent_path as parent_path,
               m.is_package as is_package,
               depth
        ORDER BY depth, m.path
    """,
    "get_file_hierarchy": """
        MATCH (f:File {id: $file_id})
        MATCH path = (r:Repository)-[:CONTAINS*]->(f)
        WITH nodes(path) as hierarchy
        UNWIND range(0, size(hierarchy)-1) as idx
        WITH hierarchy[idx] as node, idx
        RETURN labels(node)[0] as type,
               CASE
                   WHEN 'Repository' IN labels(node) THEN node.name
                   WHEN 'Module' IN labels(node) THEN node.name
                   WHEN 'File' IN labels(node) THEN node.name
               END as name,
               CASE
                   WHEN 'Repository' IN labels(node) THEN node.id
                   WHEN 'Module' IN labels(node) THEN node.path
                   WHEN 'File' IN labels(node) THEN node.path
               END as path,
               idx as depth
        ORDER BY idx
    """,
    "get_directory_contents": """
        MATCH (m:Module {repo_id: $repo_id, path: $dir_path})
        OPTIONAL MATCH (m)-[:CONTAINS]->(child)
        WHERE child:Module OR child:File
        RETURN labels(child)[0] as type,
               child.name as name,
               CASE
                   WHEN 'Module' IN labels(child) THEN child.path
                   WHEN 'File' IN labels(child) THEN child.path
               END as path,
               CASE
                   WHEN 'Module' IN labels(child) THEN child.is_package
                   ELSE false
               END as is_package
        ORDER BY type DESC, name
    """,
    "search_code": """
        CALL db.index.fulltext.queryNodes('code_search', $search_term)
        YIELD node, score
        OPTIONAL MATCH (f:File)-[:CONTAINS]->(node)
        RETURN
            CASE WHEN node:Function THEN 'function'
                 WHEN node:Class THEN 'class'
                 ELSE 'variable' END as type,
            node.name as name,
            coalesce(f.path, node.path) as path,
            coalesce(node.line_number, 0) as line_number,
            coalesce(node.source_code, node.source) as source_code,
            node.docstring as docstring,
            score
        ORDER BY score DESC
        LIMIT 20
    """,
    "analyze_change_impact": """
        MATCH (target {id: $target_id})
        MATCH path = (caller)-[:CALLS*1..3]->(target)
        WITH caller, length(path) as distance
        MATCH (f:File)-[:DEFINES*1..2]->(caller)
        RETURN DISTINCT caller.name as affected, labels(caller)[0] as type,
               f.path as file, distance
        ORDER BY distance, f.path
    """,
    # -------------------------------------------------------------------------
    # Config File Operations
    # -------------------------------------------------------------------------
    "create_config_file": """
        MATCH (r:Repository {id: $repo_id})
        MERGE (cf:ConfigFile {repo_id: $repo_id, path: $path})
        ON CREATE SET
            cf.id = $config_id,
            cf.file_type = $file_type,
            cf.sha = $sha,
            cf.lines_count = $lines_count,
            cf.project_name = $project_name,
            cf.version = $version,
            cf.description = $description,
            cf.source_code = $source_code,
            cf.created_at = datetime()
        ON MATCH SET
            cf.sha = $sha,
            cf.lines_count = $lines_count,
            cf.project_name = $project_name,
            cf.version = $version,
            cf.description = $description,
            cf.source_code = $source_code,
            cf.updated_at = datetime()
        MERGE (r)-[:HAS_CONFIG]->(cf)
        RETURN cf.id as config_id
    """,
    "create_dependency": """
        MATCH (cf:ConfigFile {id: $config_id})
        MERGE (d:Dependency {config_id: $config_id, name: $name})
        ON CREATE SET
            d.id = $dep_id,
            d.version_spec = $version_spec,
            d.is_dev = $is_dev,
            d.source = $source,
            d.extras = $extras,
            d.created_at = datetime()
        ON MATCH SET
            d.version_spec = $version_spec,
            d.is_dev = $is_dev,
            d.source = $source,
            d.extras = $extras,
            d.updated_at = datetime()
        MERGE (cf)-[:HAS_DEPENDENCY]->(d)
        RETURN d.id as dep_id
    """,
    "create_script": """
        MATCH (cf:ConfigFile {id: $config_id})
        MERGE (s:Script {config_id: $config_id, name: $name})
        ON CREATE SET
            s.id = $script_id,
            s.command = $command,
            s.created_at = datetime()
        ON MATCH SET
            s.command = $command,
            s.updated_at = datetime()
        MERGE (cf)-[:HAS_SCRIPT]->(s)
        RETURN s.id as script_id
    """,
    # -------------------------------------------------------------------------
    # Batch Operations (Performance Optimization)
    # -------------------------------------------------------------------------
    "batch_create_classes": """
        UNWIND $classes as cls
        MATCH (f:File {id: $file_id})
        MERGE (c:Class {file_id: $file_id, name: cls.name, line_start: cls.line_start})
        ON CREATE SET
            c.id = cls.class_id,
            c.line_end = cls.line_end,
            c.docstring = cls.docstring,
            c.decorators = cls.decorators,
            c.base_classes = cls.base_classes,
            c.is_abstract = cls.is_abstract,
            c.source_code = cls.source_code,
            c.qualified_name = cls.qualified_name,
            c.visibility = cls.visibility
        ON MATCH SET
            c.source_code = cls.source_code,
            c.docstring = cls.docstring
        MERGE (f)-[:DEFINES]->(c)

        // Create Symbol
        MERGE (s:Symbol {id: 'symbol:' + cls.qualified_name})
        ON CREATE SET
            s.name = cls.name,
            s.qualified_name = cls.qualified_name,
            s.type = 'class',
            s.scope = 'module',
            s.file_id = $file_id,
            s.line_start = cls.line_start,
            s.line_end = cls.line_end,
            s.visibility = cls.visibility,
            s.docstring = cls.docstring,
            s.created_at = datetime()
        ON MATCH SET
            s.line_start = cls.line_start,
            s.line_end = cls.line_end,
            s.updated_at = datetime()
        MERGE (s)-[:REFERENCES]->(c)

        RETURN c.id as class_id
    """,
    "batch_create_methods": """
        UNWIND $methods as m
        MATCH (c:Class {id: $class_id})
        MERGE (method:Method {class_id: $class_id, name: m.name, line_start: m.line_start})
        ON CREATE SET
            method.id = m.method_id,
            method.line_end = m.line_end,
            method.params = m.params,
            method.return_type = m.return_type,
            method.docstring = m.docstring,
            method.decorators = m.decorators,
            method.is_async = m.is_async,
            method.is_static = m.is_static,
            method.is_classmethod = m.is_classmethod,
            method.is_property = m.is_property,
            method.visibility = m.visibility,
            method.source_code = m.source_code,
            method.qualified_name = m.qualified_name
        ON MATCH SET
            method.source_code = m.source_code,
            method.docstring = m.docstring
        MERGE (c)-[:HAS_METHOD]->(method)

        // Create Symbol
        MERGE (s:Symbol {id: 'symbol:' + m.qualified_name})
        ON CREATE SET
            s.name = m.name,
            s.qualified_name = m.qualified_name,
            s.type = 'method',
            s.scope = 'class',
            // Retrieve file_id from class_id which is typically file_id:class:ClassName
            s.file_id = split($class_id, ':class:')[0],
            s.line_start = m.line_start,
            s.line_end = m.line_end,
            s.visibility = m.visibility,
            s.docstring = m.docstring,
            s.created_at = datetime()
        ON MATCH SET
            s.line_start = m.line_start,
            s.line_end = m.line_end,
            s.updated_at = datetime()
        MERGE (s)-[:REFERENCES]->(method)

        RETURN method.id as method_id
    """,
    "batch_create_functions": """
        UNWIND $functions as fn
        MATCH (f:File {id: $file_id})
        MERGE (func:Function {file_id: $file_id, name: fn.name, line_start: fn.line_start})
        ON CREATE SET
            func.id = fn.function_id,
            func.line_end = fn.line_end,
            func.params = fn.params,
            func.return_type = fn.return_type,
            func.docstring = fn.docstring,
            func.decorators = fn.decorators,
            func.is_async = fn.is_async,
            func.source_code = fn.source_code,
            func.qualified_name = fn.qualified_name,
            func.visibility = fn.visibility
        ON MATCH SET
            func.source_code = fn.source_code,
            func.docstring = fn.docstring
        MERGE (f)-[:DEFINES]->(func)

        // Create Symbol
        MERGE (s:Symbol {id: 'symbol:' + fn.qualified_name})
        ON CREATE SET
            s.name = fn.name,
            s.qualified_name = fn.qualified_name,
            s.type = 'function',
            s.scope = 'module',
            s.file_id = $file_id,
            s.line_start = fn.line_start,
            s.line_end = fn.line_end,
            s.visibility = fn.visibility,
            s.docstring = fn.docstring,
            s.created_at = datetime()
        ON MATCH SET
            s.line_start = fn.line_start,
            s.line_end = fn.line_end,
            s.updated_at = datetime()
        MERGE (s)-[:REFERENCES]->(func)

        RETURN func.id as function_id
    """,
    "batch_create_imports": """
        UNWIND $imports as imp
        MATCH (f:File {id: $file_id})
        MERGE (i:Import {file_id: $file_id, module: imp.module, line_start: imp.line_start})
        ON CREATE SET
            i.alias = imp.alias,
            i.line_end = imp.line_end,
            i.is_from_import = imp.is_from_import,
            i.imported_names = imp.imported_names
        MERGE (f)-[:HAS_IMPORT]->(i)
        RETURN i.module as module
    """,
    "batch_create_variables": """
        UNWIND $variables as v
        MATCH (f:File {id: $file_id})
        MERGE (var:Variable {file_id: $file_id, name: v.name, line_start: v.line_start})
        ON CREATE SET
            var.id = v.variable_id,
            var.line_end = v.line_end,
            var.type_annotation = v.type_annotation,
            var.is_constant = v.is_constant,
            var.scope = v.scope
        MERGE (f)-[:DEFINES]->(var)
        RETURN var.id as variable_id
    """,
    "batch_create_call_relationships": """
        UNWIND $calls as call
        MATCH (caller) WHERE caller.id = call.caller_id

        // Try to find target: Methods don't have file_id, so match them separately
        OPTIONAL MATCH (target)
        WHERE target.name = call.callee_name
          AND (
            // For Classes and Functions, check file_id
            (
              (target:Class OR target:Function)
              AND target.file_id STARTS WITH $repo_id
            )
            OR
            // For Methods, check through their class relationship
            (
              target:Method
              AND exists((target)<-[:HAS_METHOD]-(:Class)-[:DEFINES]-(:File))
            )
          )

        WITH caller, call, target

        FOREACH (_ IN CASE WHEN target IS NOT NULL THEN [1] ELSE [] END |
            MERGE (caller)-[r:CALLS {line_number: call.line_number}]->(target)
            ON CREATE SET
                r.arguments = call.arguments,
                r.column = call.column,
                r.is_resolved = true
        )
        FOREACH (_ IN CASE WHEN target IS NULL THEN [1] ELSE [] END |
            MERGE (u:UnresolvedSymbol {name: call.callee_name})
            ON CREATE SET u.created_at = datetime()
            MERGE (caller)-[r:CALLS {line_number: call.line_number}]->(u)
            ON CREATE SET r.is_resolved = false, r.arguments = call.arguments, r.column = call.column
        )
        RETURN call.callee_name as callee
    """,
    "batch_create_inheritance": """
        UNWIND $relationships as rel
        MATCH (child:Class {id: rel.child_id})
        OPTIONAL MATCH (parent:Class {name: rel.parent_name})
        WHERE parent.file_id STARTS WITH $repo_id OR parent.repo_id = $repo_id
        WITH child, parent
        WHERE parent IS NOT NULL
        MERGE (child)-[:INHERITS]->(parent)
        RETURN child.id as child_id
    """,
}
