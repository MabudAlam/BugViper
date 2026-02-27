
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from .client import Neo4jClient
from .schema import CodeGraphSchema, CYPHER_QUERIES


@dataclass
class IngestionStats:
    """Statistics for an ingestion run."""
    files_processed: int = 0
    files_skipped: int = 0
    classes_created: int = 0
    methods_created: int = 0
    functions_created: int = 0
    imports_created: int = 0
    variables_created: int = 0
    lines_created: int = 0
    relationships_created: int = 0
    config_files: int = 0
    dependencies: int = 0
    errors: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        return f"""
Ingestion Statistics:
=====================
Files Processed: {self.files_processed}
Files Skipped: {self.files_skipped}
Classes Created: {self.classes_created}
Methods Created: {self.methods_created}
Functions Created: {self.functions_created}
Imports Created: {self.imports_created}
Variables Created: {self.variables_created}
Lines Created: {self.lines_created}
Relationships Created: {self.relationships_created}
Config Files: {self.config_files}
Dependencies: {self.dependencies}
Errors: {len(self.errors)}
"""


def _serialize_params(params: Optional[List[Dict[str, Any]]]) -> str:
    """Convert params list to JSON string for Neo4j storage."""
    if not params:
        return "[]"
    cleaned = [
        {"name": p.get("name", ""), "type": p.get("type") or ""}
        for p in params
    ]
    return json.dumps(cleaned)


class GraphIngestionService:
    """
    Service for ingesting analyzed code into Neo4j graph database.
    
    Creates nodes and relationships representing:
    - Users, Repositories, Branches
    - Modules/Directories, Files
    - Classes, Methods, Functions, Variables
    - Imports, Call relationships, References
    """
    
    def __init__(self, client: Neo4jClient):
        self.db = client
        self.schema = CodeGraphSchema(client)
        self.stats = IngestionStats()

        # Cache for deferred relationship creation
        self._call_relationships: List[Dict[str, Any]] = []
        self._inheritance_relationships: List[Dict[str, Any]] = []

        # Current repository context for building qualified names
        self._current_repo_id: Optional[str] = None
        self._current_file_path: Optional[str] = None
        self._current_module_path: Optional[str] = None
    
    def setup_schema(self) -> None:
        """Initialize database schema with constraints and indexes."""
        self.schema.create_constraints_and_indexes()
    
    def clear_repository(self, repo_id: str) -> None:
        """Clear all data for a repository before re-ingestion."""
        self.schema.clear_repository(repo_id)
    
    def get_stats(self) -> IngestionStats:
        """Get current ingestion statistics."""
        return self.stats
    
    def reset_stats(self) -> None:
        """Reset ingestion statistics."""
        self.stats = IngestionStats()

    def _build_qualified_name(self, name: str, scope: str, class_name: Optional[str] = None) -> str:
        """
        Build a fully qualified name for a symbol.

        Examples:
            - module.function_name
            - module.ClassName.method_name
            - module.variable_name
        """
        parts = []

        # Add module path (convert file path to module path)
        if self._current_module_path:
            module = self._current_module_path.replace('/', '.').replace('\\', '.')
            if module.endswith('.py'):
                module = module[:-3]
            parts.append(module)
        elif self._current_file_path:
            file_module = self._current_file_path.replace('/', '.').replace('\\', '.')
            if file_module.endswith('.py'):
                file_module = file_module[:-3]
            parts.append(file_module)

        # Add class name if in class scope
        if scope == "method" and class_name:
            parts.append(class_name)

        # Add symbol name
        parts.append(name)

        return '.'.join(parts)

    def create_symbol(
        self,
        name: str,
        qualified_name: str,
        symbol_type: str,
        scope: str,
        file_id: str,
        line_start: int,
        line_end: int,
        definition_id: str,
        visibility: str = "public",
        is_exported: bool = True,
        docstring: Optional[str] = None,
        source_code: Optional[str] = None
    ) -> str:
        """
        Create a Symbol node for fast lookups and resolution.

        Args:
            name: Simple name of the symbol
            qualified_name: Full qualified name (e.g., module.Class.method)
            symbol_type: class, function, method, variable
            scope: module, class, function
            file_id: File where symbol is defined
            line_start: Starting line number
            line_end: Ending line number
            definition_id: ID of the actual definition node (Class, Function, etc.)
            visibility: public, protected, private
            is_exported: Whether symbol is exported/public
            docstring: Documentation string
            source_code: Source code of the symbol

        Returns:
            Symbol ID
        """
        symbol_id = f"symbol:{qualified_name}"

        query = CYPHER_QUERIES["create_symbol"]
        self.db.run_query(query, {
            "symbol_id": symbol_id,
            "name": name,
            "qualified_name": qualified_name,
            "type": symbol_type,
            "scope": scope,
            "file_id": file_id,
            "line_start": line_start,
            "line_end": line_end,
            "visibility": visibility,
            "is_exported": is_exported,
            "docstring": docstring,
            "source_code": source_code
        })

        # Link symbol to its definition
        link_query = CYPHER_QUERIES["link_symbol_to_definition"]
        self.db.run_query(link_query, {
            "symbol_id": symbol_id,
            "definition_id": definition_id
        })

        return symbol_id
    
    # =========================================================================
    # User Operations
    # =========================================================================
    
    def create_user(self, username: str, email: Optional[str] = None) -> Dict[str, Any]:
        """Create or update a user node."""
        query = CYPHER_QUERIES["create_user"]
        records, _, _ = self.db.run_query(query, {
            "username": username,
            "email": email
        })
        return records[0] if records else None
    
    # =========================================================================
    # Repository Operations
    # =========================================================================
    
    def create_repository(
        self,
        username: str,
        repo_name: str,
        url: Optional[str] = None,
        local_path: Optional[str] = None,
        description: Optional[str] = None,
        default_branch: str = "main"
    ) -> str:
        """Create or update a repository node. Returns repo_id."""
        repo_id = f"{username}/{repo_name}"
        
        query = CYPHER_QUERIES["create_repository"]
        self.db.run_query(query, {
            "username": username,
            "repo_name": repo_name,
            "repo_id": repo_id,
            "url": url,
            "local_path": local_path,
            "description": description,
            "default_branch": default_branch
        })
        return repo_id
    
    def create_branch(
        self,
        repo_id: str,
        branch_name: str,
        commit_sha: Optional[str] = None,
        is_default: bool = False
    ) -> None:
        """Create a branch node."""
        query = CYPHER_QUERIES["create_branch"]
        self.db.run_query(query, {
            "repo_id": repo_id,
            "branch_name": branch_name,
            "commit_sha": commit_sha,
            "is_default": is_default
        })
    
    # =========================================================================
    # Module Operations
    # =========================================================================
    
    def create_module(
        self,
        repo_id: str,
        path: str,
        name: str,
        parent_path: Optional[str] = None,
        is_package: bool = False
    ) -> None:
        """Create a module/directory node."""
        # Calculate depth from path
        depth = path.count('/') if path else 0

        query = CYPHER_QUERIES["create_module"]
        self.db.run_query(query, {
            "repo_id": repo_id,
            "path": path,
            "name": name,
            "parent_path": parent_path,
            "type": "package" if is_package else "directory",
            "is_package": is_package,
            "depth": depth
        })
    
    # =========================================================================
    # File Operations
    # =========================================================================
    
    def create_file(
        self,
        repo_id: str,
        file_path: str,
        language: str,
        sha: Optional[str] = None,
        lines_count: int = 0,
        size: int = 0,
        source_code: Optional[str] = None
    ) -> str:
        """Create a file node. Returns file_id."""
        import os

        file_id = f"{repo_id}:{file_path}"
        name = os.path.basename(file_path)
        extension = os.path.splitext(file_path)[1]
        module_path = os.path.dirname(file_path) or None

        query = CYPHER_QUERIES["create_file"]
        self.db.run_query(query, {
            "repo_id": repo_id,
            "file_id": file_id,
            "path": file_path,
            "name": name,
            "language": language,
            "extension": extension,
            "lines_count": lines_count,
            "sha": sha,
            "size": size,
            "module_path": module_path,
            "source_code": source_code
        })

        self.stats.files_processed += 1
        return file_id
    
    
    # =========================================================================
    # Import Operations
    # =========================================================================
    
    def create_import(
        self,
        file_id: str,
        module: str,
        alias: Optional[str] = None,
        imported_names: Optional[List[str]] = None,
        is_from_import: bool = False,
        line_start: int = 0,
        line_end: int = 0
    ) -> None:
        """Create an import node."""
        query = CYPHER_QUERIES["create_import"]
        self.db.run_query(query, {
            "file_id": file_id,
            "module": module,
            "alias": alias,
            "imported_names": imported_names or [],
            "is_from_import": is_from_import,
            "line_start": line_start,
            "line_end": line_end or line_start
        })
        self.stats.imports_created += 1
    
    # =========================================================================
    # Class Operations
    # =========================================================================
    
    def create_class(
        self,
        file_id: str,
        name: str,
        line_start: int,
        line_end: int,
        base_classes: Optional[List[str]] = None,
        docstring: Optional[str] = None,
        decorators: Optional[List[str]] = None,
        is_abstract: bool = False,
        source_code: Optional[str] = None
    ) -> str:
        """Create a class node and corresponding Symbol. Returns class_id."""
        class_id = f"{file_id}:class:{name}:{line_start}"

        query = CYPHER_QUERIES["create_class"]
        self.db.run_query(query, {
            "file_id": file_id,
            "class_id": class_id,
            "name": name,
            "line_start": line_start,
            "line_end": line_end,
            "base_classes": base_classes or [],
            "docstring": docstring,
            "decorators": decorators or [],
            "is_abstract": is_abstract,
            "source_code": source_code
        })

        # Create Symbol node for this class
        qualified_name = self._build_qualified_name(name, scope="class")
        self.create_symbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type="class",
            scope="module",
            file_id=file_id,
            line_start=line_start,
            line_end=line_end,
            definition_id=class_id,
            visibility="public",
            is_exported=not name.startswith("_"),
            docstring=docstring,
            source_code=source_code
        )

        # Queue inheritance relationships
        if base_classes:
            for base in base_classes:
                self._inheritance_relationships.append({
                    "child_id": class_id,
                    "parent_name": base,
                    "repo_id": file_id.split(":")[0]
                })

        self.stats.classes_created += 1
        return class_id
    
    def create_method(
        self,
        class_id: str,
        name: str,
        line_start: int,
        line_end: int,
        params: Optional[List[Dict[str, Any]]] = None,
        return_type: Optional[str] = None,
        docstring: Optional[str] = None,
        decorators: Optional[List[str]] = None,
        is_async: bool = False,
        is_static: bool = False,
        is_classmethod: bool = False,
        is_property: bool = False,
        visibility: str = "public",
        source_code: Optional[str] = None
    ) -> str:
        """Create a method node and corresponding Symbol. Returns method_id."""
        method_id = f"{class_id}:method:{name}:{line_start}"

        query = CYPHER_QUERIES["create_method"]
        self.db.run_query(query, {
            "class_id": class_id,
            "method_id": method_id,
            "name": name,
            "line_start": line_start,
            "line_end": line_end,
            "params": _serialize_params(params),
            "return_type": return_type,
            "docstring": docstring,
            "decorators": decorators or [],
            "is_async": is_async,
            "is_static": is_static,
            "is_classmethod": is_classmethod,
            "is_property": is_property,
            "visibility": visibility,
            "source_code": source_code
        })

        # Extract class name from class_id
        class_name = class_id.split(":class:")[1].split(":")[0] if ":class:" in class_id else None
        file_id = ":".join(class_id.split(":")[:2])  # Extract file_id from class_id

        # Create Symbol node for this method
        qualified_name = self._build_qualified_name(name, scope="method", class_name=class_name)
        self.create_symbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type="method",
            scope="class",
            file_id=file_id,
            line_start=line_start,
            line_end=line_end,
            definition_id=method_id,
            visibility=visibility,
            is_exported=visibility == "public",
            docstring=docstring,
            source_code=source_code
        )

        self.stats.methods_created += 1
        return method_id
    
    def create_attribute(
        self,
        class_id: str,
        name: str,
        line_start: int,
        line_end: int = 0,
        type_annotation: Optional[str] = None,
        default_value: Optional[str] = None,
        visibility: str = "public"
    ) -> None:
        """Create a class attribute node."""
        query = CYPHER_QUERIES["create_attribute"]
        self.db.run_query(query, {
            "class_id": class_id,
            "name": name,
            "line_start": line_start,
            "line_end": line_end or line_start,
            "type_annotation": type_annotation,
            "default_value": default_value,
            "visibility": visibility
        })
    
    # =========================================================================
    # Function Operations
    # =========================================================================
    
    def create_function(
        self,
        file_id: str,
        name: str,
        line_start: int,
        line_end: int,
        params: Optional[List[Dict[str, Any]]] = None,
        return_type: Optional[str] = None,
        docstring: Optional[str] = None,
        decorators: Optional[List[str]] = None,
        is_async: bool = False,
        source_code: Optional[str] = None
    ) -> str:
        """Create a function node and corresponding Symbol. Returns function_id."""
        function_id = f"{file_id}:func:{name}:{line_start}"

        query = CYPHER_QUERIES["create_function"]
        self.db.run_query(query, {
            "file_id": file_id,
            "function_id": function_id,
            "name": name,
            "line_start": line_start,
            "line_end": line_end,
            "params": _serialize_params(params),
            "return_type": return_type,
            "docstring": docstring,
            "decorators": decorators or [],
            "is_async": is_async,
            "source_code": source_code
        })

        # Create Symbol node for this function
        qualified_name = self._build_qualified_name(name, scope="function")
        self.create_symbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type="function",
            scope="module",
            file_id=file_id,
            line_start=line_start,
            line_end=line_end,
            definition_id=function_id,
            visibility="public",
            is_exported=not name.startswith("_"),
            docstring=docstring,
            source_code=source_code
        )

        self.stats.functions_created += 1
        return function_id
    
    # =========================================================================
    # Variable Operations
    # =========================================================================
    
    def create_variable(
        self,
        file_id: str,
        name: str,
        line_start: int,
        line_end: int = 0,
        type_annotation: Optional[str] = None,
        is_constant: bool = False,
        scope: str = "module"
    ) -> str:
        """Create a variable node. Returns variable_id."""
        variable_id = f"{file_id}:var:{name}:{line_start}"
        
        query = CYPHER_QUERIES["create_variable"]
        self.db.run_query(query, {
            "file_id": file_id,
            "variable_id": variable_id,
            "name": name,
            "line_start": line_start,
            "line_end": line_end or line_start,
            "type_annotation": type_annotation,
            "is_constant": is_constant,
            "scope": scope
        })
        
        self.stats.variables_created += 1
        return variable_id
    
    # =========================================================================
    # Batch Operations (Performance Optimization)
    # =========================================================================
    
    def batch_create_classes(
        self,
        file_id: str,
        classes_data: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Create multiple class nodes in a single query.
        
        Args:
            file_id: File ID these classes belong to
            classes_data: List of class data dicts
            
        Returns:
            List of created class IDs
        """
        if not classes_data:
            return []
        
        query = CYPHER_QUERIES["batch_create_classes"]
        records, _, _ = self.db.run_query(query, {
            "file_id": file_id,
            "classes": classes_data
        })
        
        self.stats.classes_created += len(classes_data)
        return [r["class_id"] for r in records] if records else []
    
    def batch_create_methods(
        self,
        class_id: str,
        methods_data: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Create multiple method nodes in a single query.
        
        Args:
            class_id: Class ID these methods belong to
            methods_data: List of method data dicts
            
        Returns:
            List of created method IDs
        """
        if not methods_data:
            return []
        
        query = CYPHER_QUERIES["batch_create_methods"]
        records, _, _ = self.db.run_query(query, {
            "class_id": class_id,
            "methods": methods_data
        })
        
        self.stats.methods_created += len(methods_data)
        return [r["method_id"] for r in records] if records else []
    
    def batch_create_functions(
        self,
        file_id: str,
        functions_data: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Create multiple function nodes in a single query.
        
        Args:
            file_id: File ID these functions belong to
            functions_data: List of function data dicts
            
        Returns:
            List of created function IDs
        """
        if not functions_data:
            return []
        
        query = CYPHER_QUERIES["batch_create_functions"]
        records, _, _ = self.db.run_query(query, {
            "file_id": file_id,
            "functions": functions_data
        })
        
        self.stats.functions_created += len(functions_data)
        return [r["function_id"] for r in records] if records else []
    
    def batch_create_imports(
        self,
        file_id: str,
        imports_data: List[Dict[str, Any]]
    ) -> None:
        """Create multiple import nodes in a single query."""
        if not imports_data:
            return
        
        query = CYPHER_QUERIES["batch_create_imports"]
        self.db.run_query(query, {
            "file_id": file_id,
            "imports": imports_data
        })
        
        self.stats.imports_created += len(imports_data)
    
    def batch_create_variables(
        self,
        file_id: str,
        variables_data: List[Dict[str, Any]]
    ) -> None:
        """Create multiple variable nodes in a single query."""
        if not variables_data:
            return
        
        query = CYPHER_QUERIES["batch_create_variables"]
        self.db.run_query(query, {
            "file_id": file_id,
            "variables": variables_data
        })
        
        self.stats.variables_created += len(variables_data)
    
    def process_deferred_relationships_batch(self) -> None:
        """Process all queued relationships using batch operations."""
        print("\n" + "="*60)
        print("PROCESSING DEFERRED RELATIONSHIPS (BATCH)")
        print("="*60)
        print(f"Call relationships queued: {len(self._call_relationships)}")
        print(f"Inheritance relationships queued: {len(self._inheritance_relationships)}")

        # Process CALLS relationships in batch
        if self._call_relationships:
            try:
                query = CYPHER_QUERIES["batch_create_call_relationships"]
                self.db.run_query(query, {
                    "calls": self._call_relationships,
                    "repo_id": self._current_repo_id or ""
                })
                print(f"  ✓ Processed {len(self._call_relationships)} call relationships")
            except Exception as e:
                print(f"  ✗ Batch call relationships failed: {e}")

        # Process INHERITS relationships in batch
        if self._inheritance_relationships:
            try:
                query = CYPHER_QUERIES["batch_create_inheritance"]
                self.db.run_query(query, {
                    "relationships": self._inheritance_relationships,
                    "repo_id": self._current_repo_id or ""
                })
                print(f"  ✓ Processed {len(self._inheritance_relationships)} inheritance relationships")
            except Exception as e:
                print(f"  ✗ Batch inheritance failed: {e}")

        self.stats.relationships_created = len(self._call_relationships) + len(self._inheritance_relationships)

        # Clear caches
        self._call_relationships.clear()
        self._inheritance_relationships.clear()
        print("="*60 + "\n")

    # =========================================================================
    # Relationship Operations
    # =========================================================================
    
    def queue_call_relationship(
        self,
        caller_id: str,
        callee_name: str,
        line_number: int,
        arguments: Optional[List[str]] = None,
        column: int = 0
    ) -> None:
        """Queue a CALLS relationship for deferred processing."""
        self._call_relationships.append({
            "caller_id": caller_id,
            "callee_name": callee_name,
            "line_number": line_number,
            "arguments": arguments or [],
            "column": column
        })
    
    def process_deferred_relationships(self) -> None:
        """Process all queued relationships using Symbol table."""
        print("\n" + "="*60)
        print("PROCESSING DEFERRED RELATIONSHIPS")
        print("="*60)
        print(f"Call relationships queued: {len(self._call_relationships)}")
        print(f"Inheritance relationships queued: {len(self._inheritance_relationships)}")

        created_calls = 0
        created_inheritance = 0
        failed_calls = 0
        failed_inheritance = 0
        unresolved_calls = 0

        # Process CALLS relationships
        print(f"\nProcessing {len(self._call_relationships)} call relationships...")
        for i, rel in enumerate(self._call_relationships):
            symbol_id = self._find_symbol_by_name(
                rel["callee_name"],
                self._current_repo_id
            )

            if symbol_id:
                query = CYPHER_QUERIES["create_call_relationship"]
                try:
                    self.db.run_query(query, {
                        "caller_id": rel["caller_id"],
                        "symbol_id": symbol_id,
                        "qualified_target": rel["callee_name"],
                        "line_number": rel["line_number"],
                        "arguments": rel["arguments"],
                        "column": rel["column"]
                    })
                    created_calls += 1
                    if (i + 1) % 100 == 0:
                        print(f"  Created {created_calls} call relationships...")
                except Exception as e:
                    failed_calls += 1
                    if failed_calls <= 3:
                        print(f"  ✗ Failed to create CALLS: {rel['caller_id']} -> {symbol_id}")
                        print(f"    Error: {e}")
            else:
                # Create unresolved symbol placeholder
                try:
                    query = CYPHER_QUERIES["create_call_relationship_unresolved"]
                    self.db.run_query(query, {
                        "caller_id": rel["caller_id"],
                        "callee_name": rel["callee_name"],
                        "line_number": rel["line_number"],
                        "arguments": rel["arguments"],
                        "column": rel["column"]
                    })
                    unresolved_calls += 1
                except Exception as e:
                    failed_calls += 1

        # Process INHERITS relationships
        print(f"\nProcessing {len(self._inheritance_relationships)} inheritance relationships...")
        for i, rel in enumerate(self._inheritance_relationships):
            query = CYPHER_QUERIES["create_inheritance"]
            try:
                self.db.run_query(query, rel)
                created_inheritance += 1
                if (i + 1) % 100 == 0:
                    print(f"  Created {created_inheritance} inheritance relationships...")
            except Exception as e:
                failed_inheritance += 1
                if failed_inheritance <= 3:
                    print(f"  ✗ Failed to create INHERITS: {rel.get('child_id')} -> {rel.get('parent_name')}")
                    print(f"    Error: {e}")

        total_created = created_calls + created_inheritance
        total_failed = failed_calls + failed_inheritance

        self.stats.relationships_created = total_created

        print(f"\n" + "="*60)
        print(f"RELATIONSHIP SUMMARY:")
        print(f"  ✓ Resolved:    {created_calls} call relationships")
        print(f"  ⚠ Unresolved:  {unresolved_calls} call relationships")
        print(f"  ✓ Inheritance: {created_inheritance} relationships")
        print(f"  ✗ Failed:      {total_failed} relationships")
        success_rate = (total_created / (total_created + total_failed) * 100) if (total_created + total_failed) > 0 else 0
        print(f"  Success rate:  {success_rate:.1f}%")
        print("="*60 + "\n")

        # Clear caches
        self._call_relationships.clear()
        self._inheritance_relationships.clear()

    def _find_symbol_by_name(self, name: str, repo_id: str) -> Optional[str]:
        """
        Find symbol ID by name using Symbol table in database.

        Searches in order:
        1. Exact qualified name match
        2. Simple name match in current file/module
        3. Simple name match in repository
        """
        if not name:
            return None

        # Try as qualified name first
        query = """
        MATCH (s:Symbol {qualified_name: $name})
        WHERE s.file_id STARTS WITH $repo_id
        RETURN s.id as symbol_id
        LIMIT 1
        """
        records, _, _ = self.db.run_query(query, {"name": name, "repo_id": repo_id})
        if records and records[0].get("symbol_id"):
            return records[0]["symbol_id"]

        # Try simple name match in current file
        if self._current_file_path:
            file_id = f"{repo_id}:{self._current_file_path}"
            query = """
            MATCH (s:Symbol {name: $name, file_id: $file_id})
            RETURN s.id as symbol_id
            LIMIT 1
            """
            records, _, _ = self.db.run_query(query, {"name": name, "file_id": file_id})
            if records and records[0].get("symbol_id"):
                return records[0]["symbol_id"]

        # Try simple name match anywhere in repo
        query = """
        MATCH (s:Symbol {name: $name})
        WHERE s.file_id STARTS WITH $repo_id
        RETURN s.id as symbol_id
        LIMIT 1
        """
        records, _, _ = self.db.run_query(query, {"name": name, "repo_id": repo_id})
        if records and records[0].get("symbol_id"):
            return records[0]["symbol_id"]

        return None
    
    # =========================================================================
    # File Analysis Ingestion
    # =========================================================================
    
    def ingest_file_analysis(self, file_analysis: Any, repo_id: str) -> str:
        """
        Ingest a complete file analysis into the graph using BATCH operations.

        This optimized version reduces DB queries from 50-100 per file to 5-10.

        Args:
            file_analysis: The analyzed file data (FileAnalysis object)
            repo_id: The repository ID

        Returns:
            The created file_id
        """
        import os
        
        # Set context for qualified name building
        self._current_repo_id = repo_id
        self._current_file_path = file_analysis.file_path
        module_path = os.path.dirname(file_analysis.file_path) if file_analysis.file_path else None
        self._current_module_path = module_path

        # Skip HTML/CSS deep element extraction - just store the file
        language = file_analysis.language
        if language in ("html", "css"):
            # For HTML/CSS, only create file node with source code
            # Do NOT create nodes for every element/rule (this was causing node explosion)
            file_id = self.create_file(
                repo_id=repo_id,
                file_path=file_analysis.file_path,
                language=language,
                sha=file_analysis.sha,
                lines_count=file_analysis.lines_count,
                source_code=file_analysis.source_code
            )
            return file_id

        # Create file node
        file_id = self.create_file(
            repo_id=repo_id,
            file_path=file_analysis.file_path,
            language=file_analysis.language,
            sha=file_analysis.sha,
            lines_count=file_analysis.lines_count,
            source_code=file_analysis.source_code
        )
        
        # Batch create imports
        if file_analysis.imports:
            imports_data = [
                {
                    "module": imp.module,
                    "alias": imp.alias,
                    "imported_names": imp.imported_names or [],
                    "is_from_import": imp.is_from_import,
                    "line_start": imp.range.line_start if imp.range else 0,
                    "line_end": imp.range.line_end if imp.range else 0
                }
                for imp in file_analysis.imports
            ]
            self.batch_create_imports(file_id, imports_data)
        
        # Batch create classes (without redundant Symbol nodes)
        for cls in file_analysis.classes:
            class_id = f"{file_id}:class:{cls.name}:{cls.range.line_start if cls.range else 0}"
            qualified_name = self._build_qualified_name(cls.name, scope="class")
            
            # Create class data
            class_data = [{
                "class_id": class_id,
                "name": cls.name,
                "line_start": cls.range.line_start if cls.range else 0,
                "line_end": cls.range.line_end if cls.range else 0,
                "base_classes": cls.base_classes or [],
                "docstring": cls.docstring,
                "decorators": [d.name for d in cls.decorators] if cls.decorators else [],
                "is_abstract": cls.is_abstract,
                "source_code": cls.source_code,
                "qualified_name": qualified_name,
                "visibility": "public" if not cls.name.startswith("_") else "private"
            }]
            self.batch_create_classes(file_id, class_data)
            
            # Queue inheritance relationships
            if cls.base_classes:
                for base in cls.base_classes:
                    self._inheritance_relationships.append({
                        "child_id": class_id,
                        "parent_name": base,
                        "repo_id": repo_id
                    })
            
            # Batch create methods for this class
            if cls.methods:
                methods_data = []
                for method in cls.methods:
                    method_id = f"{class_id}:method:{method.name}:{method.range.line_start if method.range else 0}"
                    method_qualified_name = self._build_qualified_name(method.name, scope="method", class_name=cls.name)
                    
                    methods_data.append({
                        "method_id": method_id,
                        "name": method.name,
                        "line_start": method.range.line_start if method.range else 0,
                        "line_end": method.range.line_end if method.range else 0,
                        "params": _serialize_params([{"name": p.name, "type": p.type_annotation} for p in method.parameters]),
                        "return_type": method.return_type,
                        "docstring": method.docstring,
                        "decorators": [d.name for d in method.decorators] if method.decorators else [],
                        "is_async": method.is_async,
                        "is_static": method.is_static,
                        "is_classmethod": method.is_classmethod,
                        "is_property": method.is_property,
                        "visibility": method.visibility,
                        "source_code": method.source_code,
                        "qualified_name": method_qualified_name
                    })
                    
                    # Queue call relationships
                    for call in method.calls:
                        self._call_relationships.append({
                            "caller_id": method_id,
                            "callee_name": call.name,
                            "line_number": call.range.line_start if call.range else 0,
                            "arguments": call.arguments or [],
                            "column": call.range.start.column if call.range and call.range.start else 0
                        })
                
                self.batch_create_methods(class_id, methods_data)
            
            # Create attributes (typically few per class, keep individual)
            for attr in cls.attributes:
                self.create_attribute(
                    class_id=class_id,
                    name=attr.name,
                    line_start=attr.range.line_start if attr.range else 0,
                    type_annotation=attr.type_annotation,
                    default_value=attr.default_value,
                    visibility=attr.visibility
                )
        
        # Batch create functions (without redundant Symbol nodes)
        if file_analysis.functions:
            functions_data = []
            for func in file_analysis.functions:
                func_id = f"{file_id}:func:{func.name}:{func.range.line_start if func.range else 0}"
                qualified_name = self._build_qualified_name(func.name, scope="function")
                
                functions_data.append({
                    "function_id": func_id,
                    "name": func.name,
                    "line_start": func.range.line_start if func.range else 0,
                    "line_end": func.range.line_end if func.range else 0,
                    "params": _serialize_params([{"name": p.name, "type": p.type_annotation} for p in func.parameters]),
                    "return_type": func.return_type,
                    "docstring": func.docstring,
                    "decorators": [d.name for d in func.decorators] if func.decorators else [],
                    "is_async": func.is_async,
                    "source_code": func.source_code,
                    "qualified_name": qualified_name,
                    "visibility": "public" if not func.name.startswith("_") else "private"
                })
                
                # Queue call relationships
                for call in func.calls:
                    self._call_relationships.append({
                        "caller_id": func_id,
                        "callee_name": call.name,
                        "line_number": call.range.line_start if call.range else 0,
                        "arguments": call.arguments or [],
                        "column": call.range.start.column if call.range and call.range.start else 0
                    })
            
            self.batch_create_functions(file_id, functions_data)
        
        # Batch create module-level variables
        if file_analysis.variables:
            variables_data = [
                {
                    "variable_id": f"{file_id}:var:{var.name}:{var.range.line_start if var.range else 0}",
                    "name": var.name,
                    "line_start": var.range.line_start if var.range else 0,
                    "line_end": var.range.line_end if var.range else 0,
                    "type_annotation": var.type_annotation,
                    "is_constant": var.is_constant,
                    "scope": "module"
                }
                for var in file_analysis.variables
            ]
            self.batch_create_variables(file_id, variables_data)

        return file_id
    
    # =========================================================================
    # Config File Ingestion
    # =========================================================================
    
    def create_config_file(
        self,
        repo_id: str,
        file_path: str,
        file_type: str,
        sha: str,
        lines_count: int,
        source_code: Optional[str] = None,
        project_name: Optional[str] = None,
        version: Optional[str] = None,
        description: Optional[str] = None
    ) -> str:
        """
        Create or update a config file node.

        Args:
            source_code: Full content of the config file

        Returns:
            The config file ID
        """
        config_id = f"{repo_id}:config:{file_path}"

        query = CYPHER_QUERIES["create_config_file"]
        records, _, _ = self.db.run_query(query, {
            "repo_id": repo_id,
            "path": file_path,
            "config_id": config_id,
            "file_type": file_type,
            "sha": sha,
            "lines_count": lines_count,
            "source_code": source_code,
            "project_name": project_name,
            "version": version,
            "description": description
        })

        self.stats.config_files += 1
        return config_id
    
    def create_dependency(
        self,
        config_id: str,
        name: str,
        version_spec: Optional[str] = None,
        is_dev: bool = False,
        source: Optional[str] = None,
        extras: Optional[List[str]] = None
    ) -> str:
        """
        Create or update a dependency node.
        
        Returns:
            The dependency ID
        """
        dep_id = f"{config_id}:dep:{name}"
        
        query = CYPHER_QUERIES["create_dependency"]
        records, _, _ = self.db.run_query(query, {
            "config_id": config_id,
            "dep_id": dep_id,
            "name": name,
            "version_spec": version_spec,
            "is_dev": is_dev,
            "source": source or "unknown",
            "extras": extras or []
        })
        
        self.stats.dependencies += 1
        return dep_id
    
    def create_script(
        self,
        config_id: str,
        name: str,
        command: str
    ) -> str:
        """
        Create or update a script node.
        
        Returns:
            The script ID
        """
        script_id = f"{config_id}:script:{name}"
        
        query = CYPHER_QUERIES["create_script"]
        records, _, _ = self.db.run_query(query, {
            "config_id": config_id,
            "script_id": script_id,
            "name": name,
            "command": command
        })
        
        return script_id
    
    def ingest_config_file(self, config_analysis: Any, repo_id: str) -> str:
        """
        Ingest a complete config file analysis into the graph.
        
        Args:
            config_analysis: The analyzed config file data (ConfigFileAnalysis object)
            repo_id: The repository ID
        
        Returns:
            The created config_id
        """
        # Create config file node
        config_id = self.create_config_file(
            repo_id=repo_id,
            file_path=config_analysis.file_path,
            file_type=config_analysis.file_type,
            sha=config_analysis.sha,
            lines_count=config_analysis.lines_count,
            source_code=config_analysis.content,
            project_name=config_analysis.project_name,
            version=config_analysis.version,
            description=config_analysis.description
        )
        
        # Create dependencies
        for dep in config_analysis.all_dependencies:
            self.create_dependency(
                config_id=config_id,
                name=dep.name,
                version_spec=dep.version_spec,
                is_dev=dep.is_dev,
                source=dep.source,
                extras=dep.extras
            )
        
        # Create scripts
        for script in config_analysis.scripts:
            self.create_script(
                config_id=config_id,
                name=script.name,
                command=script.command
            )
        
        return config_id
