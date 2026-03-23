# Advanced Graph Builder for Multi-Language Code Ingestion
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pathspec
from tree_sitter import Language, Parser

from common.config_manager import get_config_value
from common.debug_log import debug_log, error_logger, info_logger, warning_logger
from common.job_models import JobStatus
from common.languages import EXT_TO_LANG
from common.tree_sitter_manager import get_tree_sitter_manager
from db import Neo4jClient

from .jobs import JobManager


class TreeSitterParser:
    """A generic parser wrapper for a specific language using tree-sitter."""

    def __init__(self, language_name: str):
        self.language_name = language_name
        self.ts_manager = get_tree_sitter_manager()

        # Get the language (cached) and create a new parser for this instance
        self.language: Language = self.ts_manager.get_language_safe(language_name)

        # Create parser with the properly wrapped language
        try:
            # Try newer version API (0.23.0+)
            self.parser = Parser(language=self.language)
        except (TypeError, AttributeError):
            try:
                # Try alternative newer API
                self.parser = Parser()
                self.parser.language = self.language
            except AttributeError:
                # Fall back to older version API (pre-0.23.0)
                self.parser = Parser()
                if hasattr(self.parser, "set_language"):
                    self.parser.set_language(self.language)
                else:
                    raise RuntimeError("Unable to set parser language with any known API")

        self.language_specific_parser = None
        if self.language_name == "python":
            from ..languages.python import PythonLangTreeSitterParser

            self.language_specific_parser = PythonLangTreeSitterParser(self)
        elif self.language_name == "javascript":
            from ..languages.javascript import JavascriptLangTreeSitterParser

            self.language_specific_parser = JavascriptLangTreeSitterParser(self)
        elif self.language_name == "go":
            from ..languages.go import GoLangTreeSitterParser

            self.language_specific_parser = GoLangTreeSitterParser(self)
        elif self.language_name == "typescript":
            from ..languages.typescript import TypescriptLangTreeSitterParser

            self.language_specific_parser = TypescriptLangTreeSitterParser(self)
        elif self.language_name == "cpp":
            from ..languages.cpp import CppLangTreeSitterParser

            self.language_specific_parser = CppLangTreeSitterParser(self)
        elif self.language_name == "rust":
            from ..languages.rust import RustLangTreeSitterParser

            self.language_specific_parser = RustLangTreeSitterParser(self)
        elif self.language_name == "c":
            from ..languages.c import CLangTreeSitterParser

            self.language_specific_parser = CLangTreeSitterParser(self)
        elif self.language_name == "java":
            from ..languages.java import JavaLangTreeSitterParser

            self.language_specific_parser = JavaLangTreeSitterParser(self)
        elif self.language_name == "ruby":
            from ..languages.ruby import RubyLangTreeSitterParser

            self.language_specific_parser = RubyLangTreeSitterParser(self)
        elif self.language_name == "c_sharp":
            from ..languages.csharp import CSharpLangTreeSitterParser

            self.language_specific_parser = CSharpLangTreeSitterParser(self)
        elif self.language_name == "php":
            from ..languages.php import PhpLangTreeSitterParser

            self.language_specific_parser = PhpLangTreeSitterParser(self)
        elif self.language_name == "kotlin":
            from ..languages.kotlin import KotlinLangTreeSitterParser

            self.language_specific_parser = KotlinLangTreeSitterParser(self)
        elif self.language_name == "scala":
            from ..languages.scala import ScalaLangTreeSitterParser

            self.language_specific_parser = ScalaLangTreeSitterParser(self)
        elif self.language_name == "swift":
            from ..languages.swift import SwiftLangTreeSitterParser

            self.language_specific_parser = SwiftLangTreeSitterParser(self)
        elif self.language_name == "haskell":
            from ..languages.haskell import HaskellLangTreeSitterParser

            self.language_specific_parser = HaskellLangTreeSitterParser(self)

    def parse(self, path: Path, is_dependency: bool = False, **kwargs) -> Dict:
        """Dispatches parsing to the language-specific parser."""
        if self.language_specific_parser:
            return self.language_specific_parser.parse(path, is_dependency, **kwargs)
        else:
            raise NotImplementedError(
                f"No language-specific parser implemented for {self.language_name}"
            )


class GraphBuilder:
    """Module for building and managing the Neo4j code graph."""

    def __init__(
        self, neo4j_client: Neo4jClient, job_manager: JobManager, loop: asyncio.AbstractEventLoop
    ):
        self.neo4j_client = neo4j_client
        self.job_manager = job_manager
        self.loop = loop
        self.driver = self.neo4j_client.driver
        # Build one TreeSitterParser per unique language, then share instances
        # across extensions (e.g. .js / .jsx / .mjs all reuse the same parser).
        _lang_instances = {lang: TreeSitterParser(lang) for lang in set(EXT_TO_LANG.values())}
        self.parsers = {ext: _lang_instances[lang] for ext, lang in EXT_TO_LANG.items()}
        self.create_schema()

    # A general schema creation based on common features across languages
    def create_schema(self):
        """Create constraints and indexes in Neo4j."""
        # When adding a new node type with a unique key, add its constraint here.
        # NOTE: We use 'repo' (format: "owner/name") + relative 'path' for uniqueness
        with self.driver.session() as session:
            try:
                # Repository uses repo (owner/name) as unique key
                session.run(
                    "CREATE CONSTRAINT repository_repo IF NOT EXISTS FOR (r:Repository) REQUIRE r.repo IS UNIQUE"
                )
                # File uses repo + relative path
                session.run(
                    "CREATE CONSTRAINT file_unique IF NOT EXISTS FOR (f:File) REQUIRE (f.repo, f.path) IS UNIQUE"
                )
                # Directory uses repo + relative path
                session.run(
                    "CREATE CONSTRAINT directory_unique IF NOT EXISTS FOR (d:Directory) REQUIRE (d.repo, d.path) IS UNIQUE"
                )
                # Code elements use repo + relative path + line_number
                session.run(
                    "CREATE CONSTRAINT function_unique IF NOT EXISTS FOR (f:Function) REQUIRE (f.name, f.repo, f.path, f.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT class_unique IF NOT EXISTS FOR (c:Class) REQUIRE (c.name, c.repo, c.path, c.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT trait_unique IF NOT EXISTS FOR (t:Trait) REQUIRE (t.name, t.repo, t.path, t.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT interface_unique IF NOT EXISTS FOR (i:Interface) REQUIRE (i.name, i.repo, i.path, i.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT macro_unique IF NOT EXISTS FOR (m:Macro) REQUIRE (m.name, m.repo, m.path, m.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT variable_unique IF NOT EXISTS FOR (v:Variable) REQUIRE (v.name, v.repo, v.path, v.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT module_name IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT struct_unique IF NOT EXISTS FOR (cstruct: Struct) REQUIRE (cstruct.name, cstruct.repo, cstruct.path, cstruct.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT enum_unique IF NOT EXISTS FOR (cenum: Enum) REQUIRE (cenum.name, cenum.repo, cenum.path, cenum.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT union_unique IF NOT EXISTS FOR (cunion: Union) REQUIRE (cunion.name, cunion.repo, cunion.path, cunion.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT annotation_unique IF NOT EXISTS FOR (a:Annotation) REQUIRE (a.name, a.repo, a.path, a.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT record_unique IF NOT EXISTS FOR (r:Record) REQUIRE (r.name, r.repo, r.path, r.line_number) IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT property_unique IF NOT EXISTS FOR (p:Property) REQUIRE (p.name, p.repo, p.path, p.line_number) IS UNIQUE"
                )

                # Indexes for language attribute
                session.run("CREATE INDEX function_lang IF NOT EXISTS FOR (f:Function) ON (f.lang)")
                session.run("CREATE INDEX class_lang IF NOT EXISTS FOR (c:Class) ON (c.lang)")
                session.run(
                    "CREATE INDEX annotation_lang IF NOT EXISTS FOR (a:Annotation) ON (a.lang)"
                )
                session.run("""
                    CREATE FULLTEXT INDEX code_search_index IF NOT EXISTS
                    FOR (n:Function|Class|Variable)
                    ON EACH [n.name, n.source, n.docstring]
                """)

                info_logger("Database schema verified/created successfully")
            except Exception as e:
                warning_logger(f"Schema creation warning: {e}")

    def _pre_scan_for_imports(self, files: list[Path], repo_path: Optional[Path] = None) -> dict:
        """Dispatches pre-scan to the correct language-specific implementation.

        Args:
            files: List of file paths to scan
            repo_path: Repository root path for calculating relative paths.
                       If None, falls back to self.project_root (set by callers
                       that already know the repo root).

        Returns:
            Dictionary mapping symbol names to relative file paths
        """
        if repo_path is None:
            repo_path = getattr(self, "project_root", None)

        imports_map = {}

        # Group files by language/extension
        files_by_lang = {}
        for file in files:
            if file.suffix in self.parsers:
                lang_ext = file.suffix
                if lang_ext not in files_by_lang:
                    files_by_lang[lang_ext] = []
                files_by_lang[lang_ext].append(file)

        if ".py" in files_by_lang:
            from ..languages import python as python_lang_module

            imports_map.update(
                python_lang_module.pre_scan_python(
                    files_by_lang[".py"], self.parsers[".py"], repo_path
                )
            )
        if ".ipynb" in files_by_lang:
            from ..languages import python as python_lang_module

            imports_map.update(
                python_lang_module.pre_scan_python(
                    files_by_lang[".ipynb"], self.parsers[".ipynb"], repo_path
                )
            )
        if ".js" in files_by_lang:
            from ..languages import javascript as js_lang_module

            imports_map.update(
                js_lang_module.pre_scan_javascript(
                    files_by_lang[".js"], self.parsers[".js"], repo_path
                )
            )
        if ".jsx" in files_by_lang:
            from ..languages import javascript as js_lang_module

            imports_map.update(
                js_lang_module.pre_scan_javascript(
                    files_by_lang[".jsx"], self.parsers[".jsx"], repo_path
                )
            )
        if ".mjs" in files_by_lang:
            from ..languages import javascript as js_lang_module

            imports_map.update(
                js_lang_module.pre_scan_javascript(
                    files_by_lang[".mjs"], self.parsers[".mjs"], repo_path
                )
            )
        if ".cjs" in files_by_lang:
            from ..languages import javascript as js_lang_module

            imports_map.update(
                js_lang_module.pre_scan_javascript(
                    files_by_lang[".cjs"], self.parsers[".cjs"], repo_path
                )
            )
        if ".go" in files_by_lang:
            from ..languages import go as go_lang_module

            imports_map.update(
                go_lang_module.pre_scan_go(files_by_lang[".go"], self.parsers[".go"], repo_path)
            )
        if ".ts" in files_by_lang:
            from ..languages import typescript as ts_lang_module

            imports_map.update(
                ts_lang_module.pre_scan_typescript(
                    files_by_lang[".ts"], self.parsers[".ts"], repo_path
                )
            )
        if ".tsx" in files_by_lang:
            from ..languages import typescriptjsx as tsx_lang_module

            imports_map.update(
                tsx_lang_module.pre_scan_typescript(
                    files_by_lang[".tsx"], self.parsers[".tsx"], repo_path
                )
            )
        if ".cpp" in files_by_lang:
            from ..languages import cpp as cpp_lang_module

            imports_map.update(
                cpp_lang_module.pre_scan_cpp(files_by_lang[".cpp"], self.parsers[".cpp"], repo_path)
            )
        if ".h" in files_by_lang:
            from ..languages import cpp as cpp_lang_module

            imports_map.update(
                cpp_lang_module.pre_scan_cpp(files_by_lang[".h"], self.parsers[".h"], repo_path)
            )
        if ".hpp" in files_by_lang:
            from ..languages import cpp as cpp_lang_module

            imports_map.update(
                cpp_lang_module.pre_scan_cpp(files_by_lang[".hpp"], self.parsers[".hpp"], repo_path)
            )
        if ".rs" in files_by_lang:
            from ..languages import rust as rust_lang_module

            imports_map.update(
                rust_lang_module.pre_scan_rust(files_by_lang[".rs"], self.parsers[".rs"], repo_path)
            )
        if ".c" in files_by_lang:
            from ..languages import c as c_lang_module

            imports_map.update(
                c_lang_module.pre_scan_c(files_by_lang[".c"], self.parsers[".c"], repo_path)
            )
        elif ".java" in files_by_lang:
            from ..languages import java as java_lang_module

            imports_map.update(
                java_lang_module.pre_scan_java(
                    files_by_lang[".java"], self.parsers[".java"], repo_path
                )
            )
        elif ".rb" in files_by_lang:
            from ..languages import ruby as ruby_lang_module

            imports_map.update(
                ruby_lang_module.pre_scan_ruby(files_by_lang[".rb"], self.parsers[".rb"], repo_path)
            )
        elif ".cs" in files_by_lang:
            from ..languages import csharp as csharp_lang_module

            imports_map.update(
                csharp_lang_module.pre_scan_csharp(
                    files_by_lang[".cs"], self.parsers[".cs"], repo_path
                )
            )
        if ".kt" in files_by_lang:
            from ..languages import kotlin as kotlin_lang_module

            imports_map.update(
                kotlin_lang_module.pre_scan_kotlin(
                    files_by_lang[".kt"], self.parsers[".kt"], repo_path
                )
            )
        if ".scala" in files_by_lang:
            from ..languages import scala as scala_lang_module

            imports_map.update(
                scala_lang_module.pre_scan_scala(
                    files_by_lang[".scala"], self.parsers[".scala"], repo_path
                )
            )
        if ".sc" in files_by_lang:
            from ..languages import scala as scala_lang_module

            imports_map.update(
                scala_lang_module.pre_scan_scala(
                    files_by_lang[".sc"], self.parsers[".sc"], repo_path
                )
            )
        if ".swift" in files_by_lang:
            from ..languages import swift as swift_lang_module

            imports_map.update(
                swift_lang_module.pre_scan_swift(
                    files_by_lang[".swift"], self.parsers[".swift"], repo_path
                )
            )
        if ".php" in files_by_lang:
            from ..languages import php as php_lang_module

            imports_map.update(
                php_lang_module.pre_scan_php(files_by_lang[".php"], self.parsers[".php"], repo_path)
            )
        if ".hs" in files_by_lang:
            from ..languages import haskell as haskell_lang_module

            imports_map.update(
                haskell_lang_module.pre_scan_haskell(
                    files_by_lang[".hs"], self.parsers[".hs"], repo_path
                )
            )

        return imports_map

    async def build_project_graph(
        self,
        project_path: str,
        include_dependencies: bool = False,
        owner: str = None,
        repo_name: str = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for building project graph.

        Args:
            project_path: Path to the project root
            include_dependencies: Whether to include dependency analysis
            owner: GitHub owner (e.g., 'Pavel401'). Defaults to 'local' if not provided
            repo_name: Repository name. Defaults to directory name if not provided

        Returns:
            Dictionary with ingestion statistics
        """
        project_path_obj = Path(project_path)

        if not project_path_obj.exists():
            raise ValueError(f"Project path does not exist: {project_path}")

        if repo_name is None:
            repo_name = project_path_obj.name
        if owner is None:
            owner = "local"

        repo_identifier = f"{owner}/{repo_name}"
        print(f"Building graph for project: {repo_identifier}")

        # Add repository to graph
        self.add_repository_to_graph(
            project_path_obj, is_dependency=False, owner=owner, repo_name=repo_name
        )

        # Get all source files
        files = []
        supported_extensions = list(self.parsers.keys())

        for ext in supported_extensions:
            pattern = f"**/*{ext}"
            found_files = list(project_path_obj.glob(pattern))
            files.extend([f for f in found_files if f.is_file()])

        print(f"Found {len(files)} files to process")

        # Pre-scan for imports (now returns relative paths)
        imports_map = self._pre_scan_for_imports(files, project_path_obj)
        print(f"Pre-scan complete, found {len(imports_map)} imports")

        # Process each file
        files_processed = 0
        files_skipped = 0
        classes_found = 0
        functions_found = 0
        errors = []
        all_file_data = []

        for file_path in files:
            try:
                ext = file_path.suffix
                if ext in self.parsers:
                    file_data = self.parse_file(project_path_obj, file_path, is_dependency=False)

                    if file_data and "error" not in file_data:
                        # Add repo_identifier and repo_path so the second pass can resolve paths
                        file_data["repo_identifier"] = repo_identifier
                        file_data["repo_path"] = str(project_path_obj)
                        self.add_file_to_graph(file_data, repo_identifier, imports_map)
                        all_file_data.append(file_data)
                        files_processed += 1

                        classes_found += len(file_data.get("classes", []))
                        functions_found += len(file_data.get("functions", []))
                    else:
                        files_skipped += 1
                else:
                    files_skipped += 1

            except Exception as e:
                error_msg = f"Error processing {file_path}: {str(e)}"
                errors.append(error_msg)
                print(f"  {error_msg}")
                files_skipped += 1

        # Second pass: create cross-file relationships (CALL and INHERITS) now that
        # all nodes exist in the graph.
        print("Creating call and inheritance relationships...")
        self._create_all_inheritance_links(all_file_data, imports_map)
        self._create_all_function_calls(all_file_data, imports_map)

        print("Graph building complete!")

        return {
            "repo": repo_identifier,
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "classes_found": classes_found,
            "functions_found": functions_found,
            "imports_found": len(imports_map),
            "total_lines": 0,
            "errors": errors,
        }

    # Language-agnostic method
    def add_repository_to_graph(
        self, repo_path: Path, is_dependency: bool = False, owner: str = None, repo_name: str = None
    ):
        """
        Adds a repository node using owner/name as the unique key.

        Args:
            repo_path: Local path to the repository (used for parsing, not stored as identifier)
            is_dependency: Whether this is a dependency repository
            owner: GitHub owner (e.g., 'Pavel401'). If not provided, defaults to 'local'
            repo_name: Repository name (e.g., 'FinanceBro'). If not provided, uses directory name
        """
        if repo_name is None:
            repo_name = repo_path.name
        if owner is None:
            owner = "local"  # Default for local repos without GitHub context

        repo_identifier = f"{owner}/{repo_name}"

        with self.driver.session() as session:
            session.run(
                """
                MERGE (r:Repository {repo: $repo})
                SET r.owner = $owner, r.name = $name, r.is_dependency = $is_dependency
                """,
                repo=repo_identifier,
                owner=owner,
                name=repo_name,
                is_dependency=is_dependency,
            )

    # First pass to add file and its contents
    def add_file_to_graph(self, file_data: Dict, repo_identifier: str, imports_map: dict):
        """
        Adds a file and its contents within a single, unified session.

        Args:
            file_data: Parsed file data from tree-sitter
            repo_identifier: Repository identifier in "owner/name" format (e.g., "Pavel401/FinanceBro")
            imports_map: Map of symbol names to file paths for resolving imports
        """
        info_logger(f"Adding file to graph for repo: {repo_identifier}")

        # Get the absolute path for reading the file content
        file_path_abs = str(Path(file_data["path"]).resolve())
        file_name = Path(file_path_abs).name
        is_dependency = file_data.get("is_dependency", False)

        # Calculate relative path from repo root
        repo_path_abs = Path(file_data.get("repo_path", "")).resolve()
        try:
            relative_path = str(Path(file_path_abs).relative_to(repo_path_abs))
        except ValueError as e:
            error_logger(
                f"CRITICAL: Cannot calculate relative path for {file_path_abs} from repo {repo_path_abs}: {e}"
            )
            raise ValueError(
                f"File {file_path_abs} is not within repository {repo_path_abs}. Ingestion cannot proceed."
            )

        # Read file source code for storage
        file_source_code = None
        lines_count = 0
        try:
            with open(file_path_abs, "r", errors="replace") as f:
                file_source_code = f.read()
                lines_count = file_source_code.count("\n") + 1
        except Exception as e:
            warning_logger(f"Could not read source for {file_path_abs}: {e}")

        # Detect language from extension
        language = EXT_TO_LANG.get(Path(file_path_abs).suffix, "unknown")

        with self.driver.session() as session:
            # Create/update File node with repo + relative path as unique key
            session.run(
                """
                MERGE (f:File {repo: $repo, path: $path})
                SET f.name = $name,
                    f.is_dependency = $is_dependency,
                    f.source_code = $source_code,
                    f.language = $language,
                    f.lines_count = $lines_count
            """,
                repo=repo_identifier,
                path=relative_path,
                name=file_name,
                is_dependency=is_dependency,
                source_code=file_source_code,
                language=language,
                lines_count=lines_count,
            )

            # Build directory hierarchy with relative paths
            path_parts = Path(relative_path).parts[:-1]  # All parts except filename
            parent_path = None  # Start from repository
            parent_label = "Repository"

            for i, part in enumerate(path_parts):
                # Build relative directory path
                current_rel_path = str(Path(*path_parts[: i + 1]))

                if parent_label == "Repository":
                    session.run(
                        """
                        MATCH (p:Repository {repo: $repo})
                        MERGE (d:Directory {repo: $repo, path: $current_path})
                        SET d.name = $part
                        MERGE (p)-[:CONTAINS]->(d)
                    """,
                        repo=repo_identifier,
                        current_path=current_rel_path,
                        part=part,
                    )
                else:
                    session.run(
                        """
                        MATCH (p:Directory {repo: $repo, path: $parent_path})
                        MERGE (d:Directory {repo: $repo, path: $current_path})
                        SET d.name = $part
                        MERGE (p)-[:CONTAINS]->(d)
                    """,
                        repo=repo_identifier,
                        parent_path=parent_path,
                        current_path=current_rel_path,
                        part=part,
                    )

                parent_path = current_rel_path
                parent_label = "Directory"

            # Link file to its parent (repo or directory)
            if parent_label == "Repository":
                session.run(
                    """
                    MATCH (p:Repository {repo: $repo})
                    MATCH (f:File {repo: $repo, path: $path})
                    MERGE (p)-[:CONTAINS]->(f)
                """,
                    repo=repo_identifier,
                    path=relative_path,
                )
            else:
                session.run(
                    """
                    MATCH (p:Directory {repo: $repo, path: $parent_path})
                    MATCH (f:File {repo: $repo, path: $path})
                    MERGE (p)-[:CONTAINS]->(f)
                """,
                    repo=repo_identifier,
                    parent_path=parent_path,
                    path=relative_path,
                )

            # CONTAINS relationships for functions, classes, and variables
            # To add a new language-specific node type (e.g., 'Trait' for Rust):
            # 1. Ensure your language-specific parser returns a list under a unique key (e.g., 'traits': [...] ).
            # 2. Add a new constraint for the new label in the `create_schema` method.
            # 3. Add a new entry to the `item_mappings` list below (e.g., (file_data.get('traits', []), 'Trait') ).
            item_mappings = [
                (file_data.get("functions", []), "Function"),
                (file_data.get("classes", []), "Class"),
                (file_data.get("traits", []), "Trait"),
                (file_data.get("variables", []), "Variable"),
                (file_data.get("interfaces", []), "Interface"),
                (file_data.get("macros", []), "Macro"),
                (file_data.get("structs", []), "Struct"),
                (file_data.get("enums", []), "Enum"),
                (file_data.get("unions", []), "Union"),
                (file_data.get("records", []), "Record"),
                (file_data.get("properties", []), "Property"),
            ]
            for item_data, label in item_mappings:
                for item in item_data:
                    # Ensure cyclomatic_complexity is set for functions
                    if label == "Function" and "cyclomatic_complexity" not in item:
                        item["cyclomatic_complexity"] = 1

                    # Add repo to item props for storage
                    item_props = {**item, "repo": repo_identifier, "path": relative_path}

                    query = f"""
                        MATCH (f:File {{repo: $repo, path: $path}})
                        MERGE (n:{label} {{name: $name, repo: $repo, path: $path, line_number: $line_number}})
                        SET n += $props
                        MERGE (f)-[:CONTAINS]->(n)
                    """

                    session.run(
                        query,
                        repo=repo_identifier,
                        path=relative_path,
                        name=item["name"],
                        line_number=item["line_number"],
                        props=item_props,
                    )

                    if label == "Function":
                        for arg_name in item.get("args", []):
                            session.run(
                                """
                                MATCH (fn:Function {name: $func_name, repo: $repo, path: $path, line_number: $line_number})
                                MERGE (p:Parameter {name: $arg_name, repo: $repo, path: $path, function_line_number: $line_number})
                                MERGE (fn)-[:HAS_PARAMETER]->(p)
                            """,
                                func_name=item["name"],
                                repo=repo_identifier,
                                path=relative_path,
                                line_number=item["line_number"],
                                arg_name=arg_name,
                            )

            # --- NEW: persist Ruby Modules ---
            for m in file_data.get("modules", []):
                session.run(
                    """
                    MERGE (mod:Module {name: $name})
                    ON CREATE SET mod.lang = $lang
                    ON MATCH  SET mod.lang = coalesce(mod.lang, $lang)
                """,
                    name=m["name"],
                    lang=file_data.get("lang"),
                )

            # Create CONTAINS relationships for nested functions
            for item in file_data.get("functions", []):
                if item.get("context_type") == "function_definition":
                    session.run(
                        """
                        MATCH (outer:Function {name: $context, repo: $repo, path: $path})
                        MATCH (inner:Function {name: $name, repo: $repo, path: $path, line_number: $line_number})
                        MERGE (outer)-[:CONTAINS]->(inner)
                    """,
                        context=item["context"],
                        repo=repo_identifier,
                        path=relative_path,
                        name=item["name"],
                        line_number=item["line_number"],
                    )

            # Handle imports and create IMPORTS relationships
            for imp in file_data.get("imports", []):
                info_logger(f"Processing import: {imp}")
                lang = file_data.get("lang")
                if lang == "javascript":
                    module_name = imp.get("source")
                    if not module_name:
                        continue

                    rel_props = {"imported_name": imp.get("name", "*")}
                    if imp.get("alias"):
                        rel_props["alias"] = imp.get("alias")
                    if imp.get("line_number"):
                        rel_props["line_number"] = imp.get("line_number")

                    session.run(
                        """
                        MATCH (f:File {repo: $repo, path: $path})
                        MERGE (m:Module {name: $module_name})
                        MERGE (f)-[r:IMPORTS]->(m)
                        SET r += $props
                    """,
                        repo=repo_identifier,
                        path=relative_path,
                        module_name=module_name,
                        props=rel_props,
                    )
                else:
                    # Existing logic for Python (and other languages)
                    set_clauses = ["m.alias = $alias"]
                    if "full_import_name" in imp:
                        set_clauses.append("m.full_import_name = $full_import_name")
                    set_clause_str = ", ".join(set_clauses)

                    rel_props = {}
                    if imp.get("line_number"):
                        rel_props["line_number"] = imp.get("line_number")
                    if imp.get("alias"):
                        rel_props["alias"] = imp.get("alias")

                    session.run(
                        f"""
                        MATCH (f:File {{repo: $repo, path: $path}})
                        MERGE (m:Module {{name: $name}})
                        SET {set_clause_str}
                        MERGE (f)-[r:IMPORTS]->(m)
                        SET r += $rel_props
                    """,
                        repo=repo_identifier,
                        path=relative_path,
                        rel_props=rel_props,
                        **imp,
                    )

            # Handle CONTAINS relationship between class to their children like variables
            for func in file_data.get("functions", []):
                if func.get("class_context"):
                    session.run(
                        """
                        MATCH (c:Class {name: $class_name, repo: $repo, path: $path})
                        MATCH (fn:Function {name: $func_name, repo: $repo, path: $path, line_number: $func_line})
                        MERGE (c)-[:CONTAINS]->(fn)
                    """,
                        class_name=func["class_context"],
                        repo=repo_identifier,
                        path=relative_path,
                        func_name=func["name"],
                        func_line=func["line_number"],
                    )

            # --- NEW: Class INCLUDES Module (Ruby mixins) ---
            for inc in file_data.get("module_inclusions", []):
                session.run(
                    """
                    MATCH (c:Class {name: $class_name, repo: $repo, path: $path})
                    MERGE (m:Module {name: $module_name})
                    MERGE (c)-[:INCLUDES]->(m)
                """,
                    class_name=inc["class"],
                    repo=repo_identifier,
                    path=relative_path,
                    module_name=inc["module"],
                )

            # Class inheritance is handled in a separate pass after all files are processed.
            # Function calls are also handled in a separate pass after all files are processed.

    # Second pass to create relationships that depend on all files being present like call functions and class inheritance
    def _collect_call_params(self, file_data: Dict, imports_map: dict) -> tuple[list, list]:
        """
        Resolve call relationships for a single file and return batched parameter rows.

        Returns two lists:
        - func_rows: calls where the caller is a known Function/Class (has full context)
        - file_rows: calls where the caller context is unknown (fall back to File node)
        """
        repo_identifier = file_data.get("repo_identifier", "local/unknown")
        repo_path_abs = Path(file_data.get("repo_path", "")).resolve()
        file_path_abs = Path(file_data["path"]).resolve()

        try:
            caller_relative_path = str(file_path_abs.relative_to(repo_path_abs))
        except ValueError:
            debug_log(
                f"Skipping CALLS collection for {file_path_abs}: not within repo {repo_path_abs}"
            )
            return [], []

        local_names = {f["name"] for f in file_data.get("functions", [])} | {
            c["name"] for c in file_data.get("classes", [])
        }
        local_imports = {
            imp.get("alias") or imp["name"].split(".")[-1]: imp["name"]
            for imp in file_data.get("imports", [])
        }

        # Guard against __builtins__ being a module (e.g. in __main__)
        builtins_set: set = (
            set(__builtins__.keys()) if isinstance(__builtins__, dict) else set(dir(__builtins__))
        )

        func_rows: list = []
        file_rows: list = []

        for call in file_data.get("function_calls", []):
            called_name = call["name"]
            if called_name in builtins_set:
                continue

            full_call = call.get("full_name", called_name)
            base_obj = full_call.split(".")[0] if "." in full_call else None
            is_chained_call = full_call.count(".") > 1 if "." in full_call else False

            if is_chained_call and base_obj in ("self", "this", "super", "super()", "cls", "@"):
                lookup_name = called_name
            else:
                lookup_name = base_obj if base_obj else called_name

            resolved_path: str | None = None

            # 1. Self/local context
            if base_obj in ("self", "this", "super", "super()", "cls", "@") and not is_chained_call:
                resolved_path = caller_relative_path
            elif lookup_name in local_names:
                resolved_path = caller_relative_path

            # 2. Inferred object type
            elif call.get("inferred_obj_type"):
                obj_type = call["inferred_obj_type"]
                possible_paths = imports_map.get(obj_type, [])
                if possible_paths:
                    resolved_path = possible_paths[0]

            # 3. Imports map
            if not resolved_path:
                possible_paths = imports_map.get(lookup_name, [])
                if len(possible_paths) == 1:
                    resolved_path = possible_paths[0]
                elif len(possible_paths) > 1:
                    if lookup_name in local_imports:
                        full_import_name = local_imports[lookup_name]
                        if full_import_name in imports_map:
                            direct_paths = imports_map[full_import_name]
                            if direct_paths and len(direct_paths) == 1:
                                resolved_path = direct_paths[0]
                        if not resolved_path:
                            for path in possible_paths:
                                if full_import_name.replace(".", "/") in path:
                                    resolved_path = path
                                    break

            # 4. Fallback
            if not resolved_path:
                debug_log(
                    f"Could not resolve call {called_name} (lookup: {lookup_name}) in {caller_relative_path}"
                )
                if called_name in local_names:
                    resolved_path = caller_relative_path
                elif called_name in imports_map and imports_map[called_name]:
                    candidates = imports_map[called_name]
                    for path in candidates:
                        for imp_name in local_imports.values():
                            if imp_name.replace(".", "/") in path:
                                resolved_path = path
                                break
                        if resolved_path:
                            break
                    if not resolved_path:
                        resolved_path = candidates[0]
                else:
                    resolved_path = caller_relative_path

            base_row = {
                "repo": repo_identifier,
                "caller_path": caller_relative_path,
                "called_name": called_name,
                "called_path": resolved_path,
                "line_number": call["line_number"],
                "args": [str(a) for a in call.get("args", [])],
                "full_call_name": call.get("full_name", called_name),
            }

            caller_context = call.get("context")
            if caller_context and len(caller_context) == 3 and caller_context[0] is not None:
                caller_name, _, caller_line_number = caller_context
                func_rows.append(
                    {
                        **base_row,
                        "caller_name": caller_name,
                        "caller_line_number": caller_line_number,
                    }
                )
            else:
                file_rows.append(base_row)

        return func_rows, file_rows

    def _create_all_function_calls(self, all_file_data: list[Dict], imports_map: dict):
        """Create CALLS relationships for all functions using batched UNWIND queries."""
        all_func_rows: list = []
        all_file_rows: list = []

        for file_data in all_file_data:
            func_rows, file_rows = self._collect_call_params(file_data, imports_map)
            all_func_rows.extend(func_rows)
            all_file_rows.extend(file_rows)

        info_logger(
            f"Processing CALLS relationships: {len(all_func_rows)} function-context + {len(all_file_rows)} file-context calls"
        )

        _BATCH_SIZE = 500

        with self.driver.session() as session:
            # Batch 1: caller is a Function/Class node
            for i in range(0, len(all_func_rows), _BATCH_SIZE):
                batch = all_func_rows[i : i + _BATCH_SIZE]
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (caller) WHERE (caller:Function OR caller:Class)
                      AND caller.name = row.caller_name
                      AND caller.repo = row.repo
                      AND caller.path = row.caller_path
                      AND caller.line_number = row.caller_line_number
                    MATCH (called) WHERE (called:Function OR called:Class)
                      AND called.name = row.called_name
                      AND called.repo = row.repo
                      AND called.path = row.called_path
                    WITH caller, called, row
                    OPTIONAL MATCH (called)-[:CONTAINS]->(init:Function)
                    WHERE called:Class AND init.name IN ["__init__", "constructor"]
                    WITH caller, COALESCE(init, called) AS final_target, row
                    MERGE (caller)-[:CALLS {line_number: row.line_number, args: row.args, full_call_name: row.full_call_name}]->(final_target)
                """,
                    rows=batch,
                )

            # Batch 2: caller is a File node (no function context)
            for i in range(0, len(all_file_rows), _BATCH_SIZE):
                batch = all_file_rows[i : i + _BATCH_SIZE]
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (caller:File {repo: row.repo, path: row.caller_path})
                    MATCH (called) WHERE (called:Function OR called:Class)
                      AND called.name = row.called_name
                      AND called.repo = row.repo
                      AND called.path = row.called_path
                    WITH caller, called, row
                    OPTIONAL MATCH (called)-[:CONTAINS]->(init:Function)
                    WHERE called:Class AND init.name IN ["__init__", "constructor"]
                    WITH caller, COALESCE(init, called) AS final_target, row
                    MERGE (caller)-[:CALLS {line_number: row.line_number, args: row.args, full_call_name: row.full_call_name}]->(final_target)
                """,
                    rows=batch,
                )

    def _create_inheritance_links(self, session, file_data: Dict, imports_map: dict):
        """Create INHERITS relationships with a more robust resolution logic."""
        # Get repo identifier and relative path
        repo_identifier = file_data.get("repo_identifier", "local/unknown")
        repo_path_abs = Path(file_data.get("repo_path", "")).resolve()
        file_path_abs = Path(file_data["path"]).resolve()
        try:
            caller_relative_path = str(file_path_abs.relative_to(repo_path_abs))
        except ValueError:
            warning_logger(
                f"Skipping inheritance for {file_path_abs}: not within repo {repo_path_abs}"
            )
            return  # Skip this file's inheritance instead of using wrong path

        local_class_names = {c["name"] for c in file_data.get("classes", [])}
        local_imports = {
            imp.get("alias") or imp["name"].split(".")[-1]: imp["name"]
            for imp in file_data.get("imports", [])
        }

        for class_item in file_data.get("classes", []):
            if not class_item.get("bases"):
                continue

            for base_class_str in class_item["bases"]:
                if base_class_str == "object":
                    continue

                resolved_path = None
                target_class_name = base_class_str.split(".")[-1]

                if "." in base_class_str:
                    lookup_name = base_class_str.split(".")[0]

                    if lookup_name in local_imports:
                        full_import_name = local_imports[lookup_name]
                        possible_paths = imports_map.get(target_class_name, [])
                        for path in possible_paths:
                            if full_import_name.replace(".", "/") in path:
                                resolved_path = path
                                break
                else:
                    lookup_name = base_class_str
                    if lookup_name in local_class_names:
                        resolved_path = caller_relative_path
                    elif lookup_name in local_imports:
                        full_import_name = local_imports[lookup_name]
                        possible_paths = imports_map.get(target_class_name, [])
                        for path in possible_paths:
                            if full_import_name.replace(".", "/") in path:
                                resolved_path = path
                                break
                    elif lookup_name in imports_map:
                        possible_paths = imports_map[lookup_name]
                        if len(possible_paths) == 1:
                            resolved_path = possible_paths[0]

                if resolved_path:
                    session.run(
                        """
                        MATCH (child:Class {name: $child_name, repo: $repo, path: $path})
                        MATCH (parent:Class {name: $parent_name, repo: $repo, path: $resolved_parent_path})
                        MERGE (child)-[:INHERITS]->(parent)
                    """,
                        child_name=class_item["name"],
                        repo=repo_identifier,
                        path=caller_relative_path,
                        parent_name=target_class_name,
                        resolved_parent_path=resolved_path,
                    )

    def _create_csharp_inheritance_and_interfaces(
        self, session, file_data: Dict, imports_map: dict
    ):
        """Create INHERITS and IMPLEMENTS relationships for C# types."""
        if file_data.get("lang") != "c_sharp":
            return

        # Get repo identifier and relative path
        repo_identifier = file_data.get("repo_identifier", "local/unknown")
        repo_path_abs = Path(file_data.get("repo_path", "")).resolve()
        file_path_abs = Path(file_data["path"]).resolve()
        try:
            caller_relative_path = str(file_path_abs.relative_to(repo_path_abs))
        except ValueError:
            warning_logger(
                f"Skipping interface inheritance for {file_path_abs}: not within repo {repo_path_abs}"
            )
            return  # Skip this file's interface inheritance instead of using wrong path

        local_type_names = set()
        for type_list in ["classes", "interfaces", "structs", "records"]:
            local_type_names.update(t["name"] for t in file_data.get(type_list, []))

        for type_list_name, type_label in [
            ("classes", "Class"),
            ("structs", "Struct"),
            ("records", "Record"),
            ("interfaces", "Interface"),
        ]:
            for type_item in file_data.get(type_list_name, []):
                if not type_item.get("bases"):
                    continue

                for base_str in type_item["bases"]:
                    base_name = base_str.split("<")[0].strip()

                    is_interface = False

                    for iface in file_data.get("interfaces", []):
                        if iface["name"] == base_name:
                            is_interface = True
                            break

                    if base_name in imports_map:
                        possible_paths = imports_map[base_name]
                        if len(possible_paths) > 0:
                            possible_paths[0]

                    base_index = type_item["bases"].index(base_str)

                    if is_interface or (base_index > 0 and type_label == "Class"):
                        session.run(
                            """
                            MATCH (child {name: $child_name, repo: $repo, path: $path})
                            WHERE child:Class OR child:Struct OR child:Record
                            MATCH (iface:Interface {name: $interface_name, repo: $repo})
                            MERGE (child)-[:IMPLEMENTS]->(iface)
                        """,
                            child_name=type_item["name"],
                            repo=repo_identifier,
                            path=caller_relative_path,
                            interface_name=base_name,
                        )
                    else:
                        session.run(
                            """
                            MATCH (child {name: $child_name, repo: $repo, path: $path})
                            WHERE child:Class OR child:Record OR child:Interface
                            MATCH (parent {name: $parent_name, repo: $repo})
                            WHERE parent:Class OR parent:Record OR parent:Interface
                            MERGE (child)-[:INHERITS]->(parent)
                        """,
                            child_name=type_item["name"],
                            repo=repo_identifier,
                            path=caller_relative_path,
                            parent_name=base_name,
                        )

    def _create_all_inheritance_links(self, all_file_data: list[Dict], imports_map: dict):
        """Create INHERITS relationships for all classes after all files have been processed."""
        with self.driver.session() as session:
            for file_data in all_file_data:
                # Handle C# separately
                if file_data.get("lang") == "c_sharp":
                    self._create_csharp_inheritance_and_interfaces(session, file_data, imports_map)
                else:
                    self._create_inheritance_links(session, file_data, imports_map)

    def delete_file_from_graph(self, repo_identifier: str, relative_path: str):
        """
        Deletes a file and all its contained elements and relationships.

        Args:
            repo_identifier: Repository in "owner/name" format
            relative_path: Relative path to the file within the repo
        """
        with self.driver.session() as session:
            # Get parent directories for cleanup
            parents_res = session.run(
                """
                MATCH (f:File {repo: $repo, path: $path})<-[:CONTAINS*]-(d:Directory)
                RETURN d.path as path ORDER BY d.path DESC
            """,
                repo=repo_identifier,
                path=relative_path,
            )
            parent_paths = [record["path"] for record in parents_res]

            # Delete file and its elements
            session.run(
                """
                MATCH (f:File {repo: $repo, path: $path})
                OPTIONAL MATCH (f)-[:CONTAINS]->(element)
                DETACH DELETE f, element
            """,
                repo=repo_identifier,
                path=relative_path,
            )
            info_logger(
                f"Deleted file and its elements from graph: {repo_identifier}:{relative_path}"
            )

            # Clean up orphaned directories
            for dir_path in parent_paths:
                session.run(
                    """
                    MATCH (d:Directory {repo: $repo, path: $path})
                    WHERE NOT (d)-[:CONTAINS]->()
                    DETACH DELETE d
                """,
                    repo=repo_identifier,
                    path=dir_path,
                )

    def delete_repository_from_graph(self, repo_identifier: str) -> bool:
        """
        Deletes a repository and all its contents from the graph.

        Args:
            repo_identifier: Repository in "owner/name" format

        Returns:
            True if deleted, False if not found
        """
        with self.driver.session() as session:
            result = session.run(
                "MATCH (r:Repository {repo: $repo}) RETURN count(r) as cnt", repo=repo_identifier
            ).single()
            if not result or result["cnt"] == 0:
                warning_logger(f"Attempted to delete non-existent repository: {repo_identifier}")
                return False

            session.run(
                """
                MATCH (r:Repository {repo: $repo})
                OPTIONAL MATCH (r)-[:CONTAINS*]->(e)
                DETACH DELETE r, e
            """,
                repo=repo_identifier,
            )
            info_logger(f"Deleted repository and its contents from graph: {repo_identifier}")
            return True

    def update_file_in_graph(
        self, path: Path, repo_path: Path, imports_map: dict, repo_identifier: str = None
    ):
        """
        Updates a single file's nodes in the graph.

        Args:
            path: Absolute path to the file
            repo_path: Absolute path to the repository root
            imports_map: Map of symbol names to relative paths
            repo_identifier: Repository in "owner/name" format
        """
        if repo_identifier is None:
            repo_identifier = f"local/{repo_path.name}"

        # Calculate relative path
        try:
            relative_path = str(path.relative_to(repo_path))
        except ValueError:
            relative_path = path.name

        self.delete_file_from_graph(repo_identifier, relative_path)

        if path.exists():
            file_data = self.parse_file(repo_path, path)

            if "error" not in file_data:
                file_data["repo_identifier"] = repo_identifier
                self.add_file_to_graph(file_data, repo_identifier, imports_map)
                return file_data
            else:
                error_logger(
                    f"Skipping graph add for {relative_path} due to parsing error: {file_data['error']}"
                )
                return None
        else:
            return {"deleted": True, "repo": repo_identifier, "path": relative_path}

    def parse_file(self, repo_path: Path, path: Path, is_dependency: bool = False) -> Dict:
        """Parses a file with the appropriate language parser and extracts code elements."""
        parser = self.parsers.get(path.suffix)
        if not parser:
            warning_logger(f"No parser found for file extension {path.suffix}. Skipping {path}")
            return {"path": str(path), "error": f"No parser for {path.suffix}"}

        debug_log(f"[parse_file] Starting parsing for: {path} with {parser.language_name} parser")
        try:
            index_source = (get_config_value("INDEX_SOURCE") or "true").lower() != "false"
            if parser.language_name == "python":
                is_notebook = path.suffix == ".ipynb"
                file_data = parser.parse(
                    path, is_dependency, is_notebook=is_notebook, index_source=index_source
                )
            else:
                file_data = parser.parse(path, is_dependency, index_source=index_source)
            file_data["repo_path"] = str(repo_path)
            return file_data
        except Exception as e:
            error_logger(f"Error parsing {path} with {parser.language_name} parser: {e}")
            debug_log(f"[parse_file] Error parsing {path}: {e}")
            return {"path": str(path), "error": str(e)}

    def estimate_processing_time(self, path: Path) -> Optional[Tuple[int, float]]:
        """Estimate processing time and file count"""
        try:
            supported_extensions = self.parsers.keys()
            if path.is_file():
                if path.suffix in supported_extensions:
                    files = [path]
                else:
                    return 0, 0.0  # Not a supported file type
            else:
                all_files = path.rglob("*")
                files = [f for f in all_files if f.is_file() and f.suffix in supported_extensions]

                # Filter default ignored directories
                ignore_dirs_str = get_config_value("IGNORE_DIRS") or ""
                if ignore_dirs_str:
                    ignore_dirs = {
                        d.strip().lower() for d in ignore_dirs_str.split(",") if d.strip()
                    }
                    if ignore_dirs:
                        kept_files = []
                        for f in files:
                            try:
                                parts = set(p.lower() for p in f.relative_to(path).parent.parts)
                                if not parts.intersection(ignore_dirs):
                                    kept_files.append(f)
                            except ValueError:
                                kept_files.append(f)
                        files = kept_files

            total_files = len(files)
            estimated_time = total_files * 0.05  # tree-sitter is faster
            return total_files, estimated_time
        except Exception as e:
            error_logger(f"Could not estimate processing time for {path}: {e}")
            return None

    async def build_graph_from_path_async(
        self,
        path: Path,
        is_dependency: bool = False,
        job_id: str = None,
        owner: str = None,
        repo_name: str = None,
    ):
        """
        Builds graph from a directory or file path.

        Args:
            path: Path to the directory or file
            is_dependency: Whether this is a dependency
            job_id: Optional job ID for progress tracking
            owner: GitHub owner (defaults to 'local')
            repo_name: Repository name (defaults to directory name)
        """
        try:
            if job_id:
                self.job_manager.update_job(job_id, status=JobStatus.RUNNING)

            if repo_name is None:
                repo_name = path.name
            if owner is None:
                owner = "local"

            repo_identifier = f"{owner}/{repo_name}"

            self.add_repository_to_graph(path, is_dependency, owner=owner, repo_name=repo_name)

            # Search for .cgcignore upwards
            cgcignore_path = None
            ignore_root = path.resolve()

            # Start search from path (or parent if path is file)
            curr = path.resolve()
            if not curr.is_dir():
                curr = curr.parent

            # Walk up looking for .cgcignore
            while True:
                candidate = curr / ".cgcignore"
                if candidate.exists():
                    cgcignore_path = candidate
                    ignore_root = curr
                    debug_log(f"Found .cgcignore at {ignore_root}")
                    break
                if curr.parent == curr:  # Root hit
                    break
                curr = curr.parent

            if cgcignore_path:
                with open(cgcignore_path) as f:
                    ignore_patterns = f.read().splitlines()
                spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)
            else:
                spec = None

            supported_extensions = self.parsers.keys()
            all_files = path.rglob("*") if path.is_dir() else [path]
            files = [f for f in all_files if f.is_file() and f.suffix in supported_extensions]

            # Filter default ignored directories
            ignore_dirs_str = get_config_value("IGNORE_DIRS") or ""
            if ignore_dirs_str and path.is_dir():
                ignore_dirs = {d.strip().lower() for d in ignore_dirs_str.split(",") if d.strip()}
                if ignore_dirs:
                    kept_files = []
                    for f in files:
                        try:
                            # Check if any parent directory in the relative path is in ignore list
                            parts = set(p.lower() for p in f.relative_to(path).parent.parts)
                            if not parts.intersection(ignore_dirs):
                                kept_files.append(f)
                            else:
                                # debug_log(f"Skipping default ignored file: {f}")
                                pass
                        except ValueError:
                            kept_files.append(f)
                    files = kept_files

            if spec:
                filtered_files = []
                for f in files:
                    try:
                        # Match relative to the directory containing .cgcignore
                        rel_path = f.relative_to(ignore_root)
                        if not spec.match_file(str(rel_path)):
                            filtered_files.append(f)
                        else:
                            debug_log(f"Ignored file based on .cgcignore: {rel_path}")
                    except ValueError:
                        # Should not happen if ignore_root is a parent, but safety fallback
                        filtered_files.append(f)
                files = filtered_files
            if job_id:
                self.job_manager.update_job(job_id, total_files=len(files))

            debug_log("Starting pre-scan to build imports map...")
            repo_path_resolved = path.resolve() if path.is_dir() else path.parent.resolve()
            imports_map = self._pre_scan_for_imports(files, repo_path_resolved)
            debug_log(f"Pre-scan complete. Found {len(imports_map)} definitions.")

            all_file_data = []

            processed_count = 0
            for file in files:
                if file.is_file():
                    if job_id:
                        self.job_manager.update_job(job_id, current_file=str(file))
                    repo_path = path.resolve() if path.is_dir() else file.parent.resolve()
                    file_data = self.parse_file(repo_path, file, is_dependency)
                    if "error" not in file_data:
                        # Add repo_identifier for relationship creation in second pass
                        file_data["repo_identifier"] = repo_identifier
                        self.add_file_to_graph(file_data, repo_identifier, imports_map)
                        all_file_data.append(file_data)
                    processed_count += 1
                    if job_id:
                        self.job_manager.update_job(job_id, processed_files=processed_count)
                    await asyncio.sleep(0.01)

            self._create_all_inheritance_links(all_file_data, imports_map)
            self._create_all_function_calls(all_file_data, imports_map)

            if job_id:
                self.job_manager.update_job(
                    job_id, status=JobStatus.COMPLETED, end_time=datetime.now()
                )
        except Exception as e:
            error_message = str(e)
            error_logger(f"Failed to build graph for path {path}: {error_message}")
            if job_id:
                """checking if the repo got deleted """
                if (
                    "no such file found" in error_message
                    or "deleted" in error_message
                    or "not found" in error_message
                ):
                    status = JobStatus.CANCELLED

                else:
                    status = JobStatus.FAILED

                self.job_manager.update_job(
                    job_id, status=status, end_time=datetime.now(), errors=[str(e)]
                )
