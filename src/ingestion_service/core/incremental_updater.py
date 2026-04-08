import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.debug_log import error_logger, info_logger, warning_logger
from common.github_client import GitHubClient
from common.languages import SUPPORTED_EXTENSIONS
from db import Neo4jClient
from ingestion_service.core.jobs import JobManager
from ingestion_service.core.tree_sitter_router import GraphBuilder, MARKDOWN_EXTENSIONS


@dataclass
class IncrementalUpdateStats:
    """Statistics for incremental graph update."""

    files_added: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    files_renamed: int = 0
    relationships_rebuilt: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class IncrementalGraphUpdater:
    """
    Handles incremental graph updates for code pushes (PR merges or direct pushes).

    Instead of rebuilding the entire graph, this class:
    1. Identifies changed files from the PR/push
    2. Deletes stale nodes for removed/modified files
    3. Adds/updates nodes for new/modified files
    4. Rebuilds relationships (CALLS, INHERITS) for affected files
    """

    def __init__(self, neo4j_client: Neo4jClient, github_client: GitHubClient):
        self.neo4j_client = neo4j_client
        self.github_client = github_client
        self.driver = neo4j_client.driver

    def _is_supported_file(self, filename: str) -> bool:
        """Check if file extension is supported for parsing."""
        return Path(filename).suffix in SUPPORTED_EXTENSIONS

    async def _sync_repository(self, owner: str, repo: str, repo_path: str) -> Tuple[bool, str]:
        """
        Sync the local repository with remote (git pull or re-clone if needed).

        Returns:
            Tuple of (success: bool, repo_path: str)
        """
        repo_path_obj = Path(repo_path)

        def _do_sync() -> Tuple[bool, str]:
            # Check if directory exists and is a git repo
            if repo_path_obj.exists() and (repo_path_obj / ".git").exists():
                info_logger(f"[SYNC] Repository exists at {repo_path}, pulling latest changes...")
                try:
                    # Fetch and reset to origin/HEAD to handle any local changes
                    result = subprocess.run(
                        ["git", "fetch", "origin"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode != 0:
                        warning_logger(f"[SYNC] git fetch failed: {result.stderr}")

                    # Get the default branch
                    result = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        default_branch = result.stdout.strip().replace("origin/", "")
                    else:
                        default_branch = "main"  # fallback

                    # Reset to match remote
                    result = subprocess.run(
                        ["git", "reset", "--hard", f"origin/{default_branch}"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result.returncode != 0:
                        warning_logger(f"[SYNC] git reset failed: {result.stderr}")
                        # Try just pulling
                        result = subprocess.run(
                            ["git", "pull", "--force"],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )

                    if result.returncode == 0:
                        info_logger(f"[SYNC] Successfully synced repository at {repo_path}")
                        return True, repo_path
                    else:
                        warning_logger(f"[SYNC] Pull failed: {result.stderr}, will re-clone")
                except subprocess.TimeoutExpired:
                    warning_logger("[SYNC] Git operation timed out, will re-clone")
                except Exception as e:
                    warning_logger(f"[SYNC] Git sync failed: {e}, will re-clone")

            # Directory doesn't exist or git operations failed - re-clone
            info_logger(f"[SYNC] Re-cloning repository {owner}/{repo}...")

            # Create temp directory for clone
            temp_base = tempfile.gettempdir()
            clone_path = os.path.join(temp_base, owner, repo)

            # Remove existing directory if it exists but is corrupted
            if os.path.exists(clone_path):
                import shutil

                shutil.rmtree(clone_path, ignore_errors=True)

            # Ensure parent directory exists
            os.makedirs(os.path.dirname(clone_path), exist_ok=True)

            try:
                # Clone the repository
                clone_url = f"https://github.com/{owner}/{repo}.git"
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, clone_path],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if result.returncode == 0:
                    info_logger(f"[SYNC] Successfully cloned repository to {clone_path}")
                    return True, clone_path
                else:
                    error_logger(f"[SYNC] Clone failed: {result.stderr}")
                    return False, repo_path

            except subprocess.TimeoutExpired:
                error_logger(f"[SYNC] Clone timed out for {owner}/{repo}")
                return False, repo_path
            except Exception as e:
                error_logger(f"[SYNC] Clone failed: {e}")
                return False, repo_path

        return await asyncio.to_thread(_do_sync)

    def _repository_exists(self, owner: str, repo: str) -> bool:
        """Check if a repository exists in the graph database."""
        repo_identifier = f"{owner}/{repo}"
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (r:Repository {repo: $repo})
                RETURN count(r) as cnt
            """,
                repo=repo_identifier,
            ).single()
            exists = result and result["cnt"] > 0
            info_logger(f"Repository {repo_identifier} exists in graph: {exists}")
            return exists

    async def _get_file_content_from_github(
        self, owner: str, repo: str, filepath: str, ref: str = "HEAD"
    ) -> Optional[str]:
        """Fetch file content from GitHub."""
        try:
            return await self.github_client.get_file_content(owner, repo, filepath, ref=ref)
        except Exception as e:
            warning_logger(f"Could not fetch {filepath}: {e}")
            return None

    def _delete_file_nodes_and_relationships(
        self, repo_identifier: str, relative_path: str
    ) -> None:
        """
        Delete a file and all its contained elements from the graph.
        Also cleans up orphaned directories.

        Args:
            repo_identifier: Repository in "owner/name" format
            relative_path: Relative path to the file within the repo
        """
        with self.driver.session() as session:
            info_logger(f"Deleting file from graph: {repo_identifier}:{relative_path}")

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

            # Delete all elements contained by the file
            session.run(
                """
                MATCH (f:File {repo: $repo, path: $path})
                OPTIONAL MATCH (f)-[:CONTAINS*]->(element)
                DETACH DELETE element
            """,
                repo=repo_identifier,
                path=relative_path,
            )

            # Delete the file node itself
            session.run(
                """
                MATCH (f:File {repo: $repo, path: $path})
                DETACH DELETE f
            """,
                repo=repo_identifier,
                path=relative_path,
            )

            info_logger(f"Deleted file and elements: {repo_identifier}:{relative_path}")

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

    def _delete_incoming_calls_to_file(self, repo_identifier: str, relative_path: str) -> int:
        """
        Delete CALLS relationships from other files pointing to elements in this file.

        Args:
            repo_identifier: Repository in "owner/name" format
            relative_path: Relative path to the file

        Returns:
            Count of deleted relationships
        """
        with self.driver.session() as session:
            info_logger(f"Deleting incoming CALLS to: {repo_identifier}:{relative_path}")

            result = session.run(
                """
                MATCH (caller)-[r:CALLS]->(callee)
                WHERE callee.repo = $repo AND callee.path = $path
                  AND caller.path <> $path
                DELETE r
                RETURN count(r) as deleted_count
            """,
                repo=repo_identifier,
                path=relative_path,
            )
            record = result.single()
            deleted_count = record["deleted_count"] if record else 0

            info_logger(f"Deleted {deleted_count} incoming CALLS relationships")
            return deleted_count

    def _get_files_that_call_into(self, repo_identifier: str, relative_path: str) -> List[str]:
        """Get list of relative paths that have CALLS relationships into the given file."""
        with self.driver.session() as session:
            info_logger(f"Finding callers into: {repo_identifier}:{relative_path}")

            result = session.run(
                """
                MATCH (caller)-[:CALLS]->(callee)
                WHERE callee.repo = $repo AND callee.path = $path
                  AND caller.path <> $path
                RETURN DISTINCT caller.path as caller_path
            """,
                repo=repo_identifier,
                path=relative_path,
            )
            callers = [record["caller_path"] for record in result]

            info_logger(f"Found {len(callers)} files that call into this file")
            return callers

    def _get_files_that_inherit_from(self, repo_identifier: str, relative_path: str) -> List[str]:
        """Get list of relative paths that have INHERITS relationships from the given file."""
        with self.driver.session() as session:
            info_logger(f"Finding inheritors from: {repo_identifier}:{relative_path}")

            result = session.run(
                """
                MATCH (child)-[:INHERITS]->(parent)
                WHERE parent.repo = $repo AND parent.path = $path
                  AND child.path <> $path
                RETURN DISTINCT child.path as child_path
            """,
                repo=repo_identifier,
                path=relative_path,
            )
            inheritors = [record["child_path"] for record in result]

            info_logger(f"Found {len(inheritors)} files that inherit from this file")
            return inheritors

    def _rebuild_imports_map_for_files(
        self, graph_builder: GraphBuilder, file_paths: List[Path]
    ) -> Dict:
        """Rebuild the imports map for a subset of files."""
        info_logger(f"Pre-scanning {len(file_paths)} files for exports")
        result = graph_builder._pre_scan_for_imports(file_paths)
        info_logger(f"Found {len(result)} symbols in new files")
        return result

    def _get_existing_imports_map(self, repo_identifier: str) -> Dict:
        """
        Build imports_map from existing graph data.
        Uses repo + relative path for uniqueness.
        """
        imports_map = {}
        with self.driver.session() as session:
            info_logger(f"Building imports_map from graph for repo: {repo_identifier}")

            result = session.run(
                """
                MATCH (n)
                WHERE (n:Function OR n:Class OR n:Trait OR n:Interface OR n:Struct)
                AND n.repo = $repo
                RETURN n.name as name, n.path as path
            """,
                repo=repo_identifier,
            )

            for record in result:
                name = record["name"]
                path = record["path"]
                if name not in imports_map:
                    imports_map[name] = []
                if path not in imports_map[name]:
                    imports_map[name].append(path)

            info_logger(f"Built imports_map with {len(imports_map)} symbols from existing graph")

        return imports_map

    async def update_graph_incrementally(
        self,
        owner: str,
        repo: str,
        changed_files: List[Dict[str, Any]],
        repo_local_path: Optional[str] = None,
    ) -> IncrementalUpdateStats:
        """
        Incrementally update the graph based on changed files.

        Args:
            owner: GitHub repository owner
            repo: GitHub repository name
            changed_files: List of file changes from PR/push with keys:
                - filename: relative path
                - status: 'added', 'modified', 'removed', 'renamed'
                - previous_filename: (for renamed) old filename
            repo_local_path: Optional local path if repo is already cloned

        Returns:
            IncrementalUpdateStats with update statistics
        """
        repo_identifier = f"{owner}/{repo}"

        info_logger("=" * 60)
        info_logger("========== INCREMENTAL GRAPH UPDATE STARTED ==========")
        info_logger(f"Repository: {repo_identifier}")
        info_logger(f"Changed files count: {len(changed_files)}")
        info_logger("=" * 60)

        stats = IncrementalUpdateStats()

        # Check if repository exists in graph
        info_logger("PHASE 0: Checking if repository exists in graph...")
        if not self._repository_exists(owner, repo):
            stats.errors.append(f"Repository {repo_identifier} not found in graph")
            info_logger("ERROR: Repository not found in graph!")
            return stats

        # Sync repository locally
        info_logger("PHASE 0.5: Syncing repository with remote (git pull/re-clone)...")
        temp_base = tempfile.gettempdir()
        default_local_path = os.path.join(temp_base, owner, repo)
        sync_success, local_path = await self._sync_repository(
            owner, repo, repo_local_path or default_local_path
        )

        if not sync_success:
            stats.errors.append(f"Failed to sync repository {repo_identifier}")
            info_logger("ERROR: Repository sync failed!")
            return stats

        info_logger(f"Repository synced at: {local_path}")
        local_path_obj = Path(local_path)

        # Create graph builder for parsing
        loop = asyncio.get_event_loop()
        job_manager = JobManager()
        graph_builder = GraphBuilder(self.neo4j_client, job_manager, loop)
        graph_builder.project_root = local_path_obj

        # ========== PHASE 1: CATEGORIZE FILES ==========
        info_logger("PHASE 1: Categorizing changed files by status...")
        files_to_delete: List[str] = []  # relative paths
        files_to_add: List[Dict] = []
        files_to_modify: List[Dict] = []
        affected_relative_paths: set = set()

        for file_change in changed_files:
            filename = file_change.get("filename")  # This is already a relative path
            status = file_change.get("status")

            info_logger(f"  Processing: {filename} (status={status})")

            if not self._is_supported_file(filename):
                info_logger("  -> SKIPPED (unsupported extension)")
                continue

            affected_relative_paths.add(filename)

            if status == "removed":
                files_to_delete.append(filename)
                stats.files_deleted += 1
                info_logger("  -> TO_DELETE")

            elif status == "added":
                files_to_add.append({"relative_path": filename})
                stats.files_added += 1
                info_logger("  -> TO_ADD")

            elif status == "modified":
                files_to_modify.append({"relative_path": filename})
                stats.files_modified += 1
                info_logger("  -> TO_MODIFY")

            elif status == "renamed":
                previous_filename = file_change.get("previous_filename")
                if previous_filename:
                    files_to_delete.append(previous_filename)
                    affected_relative_paths.add(previous_filename)
                    info_logger(f"  -> RENAMED from: {previous_filename}")

                files_to_add.append({"relative_path": filename})
                stats.files_renamed += 1

        info_logger(
            f"PHASE 1 SUMMARY: delete={len(files_to_delete)}, "
            f"add={len(files_to_add)}, modify={len(files_to_modify)}"
        )

        # ========== PHASE 2: FIND DEPENDENT FILES ==========
        info_logger("PHASE 2: Finding dependent files...")
        dependent_files: set = set()
        for relative_path in affected_relative_paths:
            callers = self._get_files_that_call_into(repo_identifier, relative_path)
            dependent_files.update(callers)

            inheritors = self._get_files_that_inherit_from(repo_identifier, relative_path)
            dependent_files.update(inheritors)

        info_logger(f"PHASE 2 SUMMARY: Found {len(dependent_files)} dependent files")

        # ========== PHASE 3: DELETE STALE NODES ==========
        info_logger("PHASE 3: Deleting stale nodes...")
        paths_to_delete = files_to_delete + [f["relative_path"] for f in files_to_modify]

        for relative_path in paths_to_delete:
            try:
                deleted_calls = self._delete_incoming_calls_to_file(repo_identifier, relative_path)
                stats.relationships_rebuilt += deleted_calls
                self._delete_file_nodes_and_relationships(repo_identifier, relative_path)
            except Exception as e:
                error_msg = f"Error deleting {relative_path}: {str(e)}"
                error_logger(error_msg)
                stats.errors.append(error_msg)

        # ========== PHASE 4: BUILD IMPORTS MAP ==========
        info_logger("PHASE 4: Building imports_map from existing graph...")
        existing_imports_map = self._get_existing_imports_map(repo_identifier)

        # ========== PHASE 5: PRE-SCAN NEW FILES ==========
        info_logger("PHASE 5: Pre-scanning new/modified files...")
        all_new_file_paths: List[Path] = []
        for file_info in files_to_add + files_to_modify:
            abs_path = local_path_obj / file_info["relative_path"]
            if abs_path.exists():
                all_new_file_paths.append(abs_path)

        if all_new_file_paths:
            new_imports_map = self._rebuild_imports_map_for_files(graph_builder, all_new_file_paths)
            for name, paths in new_imports_map.items():
                if name not in existing_imports_map:
                    existing_imports_map[name] = []
                existing_imports_map[name].extend(paths)

        # ========== PHASE 6: PARSE AND ADD NEW FILES ==========
        info_logger("PHASE 6: Parsing and adding files to graph...")
        new_file_data_list: List[Dict] = []

        files_to_process = files_to_add + files_to_modify
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = int(os.getenv("INGESTION_PARALLEL_WORKERS", min(8, os.cpu_count() or 4)))

        def parse_single_file_incremental(file_info):
            try:
                relative_path = file_info["relative_path"]
                abs_path = local_path_obj / relative_path
                if not abs_path.exists():
                    return ("not_found", file_info, None)
                if abs_path.suffix.lower() in MARKDOWN_EXTENSIONS:
                    return ("markdown", file_info, abs_path)
                file_data = graph_builder.parse_file(local_path_obj, abs_path)
                if "error" not in file_data:
                    file_data["repo_identifier"] = repo_identifier
                    return ("success", file_info, file_data)
                return ("parse_error", file_info, file_data.get("error"))
            except Exception as e:
                return ("exception", file_info, str(e))

        if len(files_to_process) > 1 and max_workers > 1:
            info_logger(f"  Processing {len(files_to_process)} files with {max_workers} workers...")
            parsed_results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(parse_single_file_incremental, f): f for f in files_to_process
                }
                for future in as_completed(futures):
                    parsed_results.append(future.result())

            for status, file_info, data in parsed_results:
                if status == "success":
                    try:
                        graph_builder.add_file_to_graph(data, repo_identifier, existing_imports_map)
                        new_file_data_list.append(data)
                    except Exception as e:
                        error_msg = f"Error adding {file_info['relative_path']} to graph: {str(e)}"
                        error_logger(error_msg)
                        stats.errors.append(error_msg)
                elif status == "markdown":
                    try:
                        graph_builder.add_markdown_file_to_graph(
                            data, repo_identifier, local_path_obj
                        )
                    except Exception as e:
                        error_msg = f"Error adding markdown {file_info['relative_path']}: {str(e)}"
                        error_logger(error_msg)
                        stats.errors.append(error_msg)
                elif status == "parse_error":
                    stats.errors.append(f"Parse error for {file_info['relative_path']}: {data}")
                elif status == "not_found":
                    warning_logger(f"  -> File not found locally: {file_info['relative_path']}")
                elif status == "exception":
                    error_msg = f"Error processing {file_info['relative_path']}: {data}"
                    error_logger(error_msg)
                    stats.errors.append(error_msg)
        else:
            for file_info in files_to_process:
                try:
                    relative_path = file_info["relative_path"]
                    abs_path = local_path_obj / relative_path
                    info_logger(f"  Processing: {relative_path}")

                    if not abs_path.exists():
                        warning_logger("  -> File not found locally!")
                        continue

                    if abs_path.suffix.lower() in MARKDOWN_EXTENSIONS:
                        graph_builder.add_markdown_file_to_graph(
                            abs_path, repo_identifier, local_path_obj
                        )
                        info_logger("  -> Added markdown to graph")
                        continue

                    file_data = graph_builder.parse_file(local_path_obj, abs_path)

                    if "error" not in file_data:
                        file_data["repo_identifier"] = repo_identifier
                        graph_builder.add_file_to_graph(
                            file_data, repo_identifier, existing_imports_map
                        )
                        new_file_data_list.append(file_data)
                        info_logger("  -> Added to graph")
                    else:
                        stats.errors.append(
                            f"Parse error for {relative_path}: {file_data['error']}"
                        )

                except Exception as e:
                    error_msg = f"Error processing {file_info['relative_path']}: {str(e)}"
                    error_logger(error_msg)
                    stats.errors.append(error_msg)

        # ========== PHASE 7: REBUILD RELATIONSHIPS ==========
        info_logger("PHASE 7: Rebuilding relationships...")
        if new_file_data_list:
            try:
                graph_builder._create_all_inheritance_links(
                    new_file_data_list, existing_imports_map
                )
                graph_builder._create_all_function_calls(new_file_data_list, existing_imports_map)
                info_logger(f"  -> Rebuilt relationships for {len(new_file_data_list)} files")
            except Exception as e:
                error_msg = f"Error rebuilding relationships: {str(e)}"
                error_logger(error_msg)
                stats.errors.append(error_msg)

        # ========== PHASE 8: REBUILD DEPENDENT FILES ==========
        info_logger("PHASE 8: Rebuilding dependent files...")
        dependent_file_data_list: List[Dict] = []
        for dep_relative_path in dependent_files:
            if dep_relative_path in affected_relative_paths:
                continue

            dep_abs_path = local_path_obj / dep_relative_path
            if not dep_abs_path.exists():
                continue

            try:
                file_data = graph_builder.parse_file(local_path_obj, dep_abs_path)
                if "error" not in file_data:
                    file_data["repo_identifier"] = repo_identifier
                    dependent_file_data_list.append(file_data)
            except Exception as e:
                warning_logger(f"  Could not re-parse {dep_relative_path}: {e}")

        if dependent_file_data_list:
            try:
                graph_builder._create_all_function_calls(
                    dependent_file_data_list, existing_imports_map
                )
            except Exception as e:
                warning_logger(f"  Error rebuilding dependent files: {e}")

        # ========== PHASE 9: EMBED NEW/MODIFIED NODES ==========
        info_logger("PHASE 9: Generating embeddings for new/modified nodes...")
        if os.getenv("OPENROUTER_API_KEY") and (files_to_add or files_to_modify):
            try:
                from common.embedder import embed_nodes_in_neo4j

                embed_stats = embed_nodes_in_neo4j(self.neo4j_client)
                for label, count in embed_stats.items():
                    if count:
                        info_logger(f"  ✓ Embedded {count} {label} nodes")
            except Exception as embed_err:
                info_logger(f"  ⚠ Embedding skipped: {embed_err}")
        else:
            info_logger("  Skipped (no OPENROUTER_API_KEY or no changed files)")

        # ========== FINAL SUMMARY ==========
        info_logger("=" * 60)
        info_logger("========== INCREMENTAL UPDATE COMPLETE ==========")
        info_logger(
            f"  Added: {stats.files_added}, Modified: {stats.files_modified}, "
            f"Deleted: {stats.files_deleted}"
        )
        info_logger(f"  Errors: {len(stats.errors)}")
        info_logger("=" * 60)

        return stats


async def ingest_merged_pr(
    owner: str,
    repo: str,
    pr_number: int,
    neo4j_client: Neo4jClient,
    repo_local_path: Optional[str] = None,
) -> IncrementalUpdateStats:
    """
    Handle a code push (PR merge or direct push) by incrementally updating the graph.

    Args:
        owner: GitHub repository owner
        repo: GitHub repository name
        pr_number: Pull request number
        neo4j_client: Neo4j client instance
        repo_local_path: Optional local path if repo is cloned

    Returns:
        IncrementalUpdateStats with update results
    """
    info_logger("=" * 70)
    info_logger(">>>>>>>>>> ingest_merged_pr STARTED <<<<<<<<<<")
    info_logger(f"Input: owner='{owner}', repo='{repo}', pr_number={pr_number}")
    info_logger(f"repo_local_path={repo_local_path}")
    info_logger("=" * 70)

    gh = GitHubClient()

    info_logger(f"Calling GitHub API: gh.get_pr_files('{owner}', '{repo}', {pr_number})")
    pr_files_payload = await gh.get_pr_files(owner, repo, pr_number)

    info_logger(f"GitHub API returned {len(pr_files_payload)} changed files:")
    for idx, file in enumerate(pr_files_payload):
        filename = file.get("filename")
        status = file.get("status")
        additions = file.get("additions", 0)
        deletions = file.get("deletions", 0)
        info_logger(f"  [{idx + 1}] {filename}")
        info_logger(f"      status={status}, +{additions}/-{deletions} lines")

    # Perform incremental update
    info_logger("Creating IncrementalGraphUpdater and starting update...")
    updater = IncrementalGraphUpdater(neo4j_client, gh)
    stats = await updater.update_graph_incrementally(
        owner=owner, repo=repo, changed_files=pr_files_payload, repo_local_path=repo_local_path
    )

    info_logger("=" * 70)
    info_logger(">>>>>>>>>> ingest_merged_pr COMPLETED <<<<<<<<<<")
    info_logger(
        f"Final stats: added={stats.files_added}, modified={stats.files_modified}, "
        f"deleted={stats.files_deleted}, renamed={stats.files_renamed}, "
        f"errors={len(stats.errors)}"
    )
    info_logger("=" * 70)

    return stats


async def ingest_direct_push(
    owner: str,
    repo: str,
    before_sha: str,
    after_sha: str,
    neo4j_client: Neo4jClient,
    repo_local_path: Optional[str] = None,
) -> IncrementalUpdateStats:
    """
    Handle a direct push (not via PR) by comparing commits.

    Args:
        owner: GitHub repository owner
        repo: GitHub repository name
        before_sha: SHA before the push
        after_sha: SHA after the push
        neo4j_client: Neo4j client instance
        repo_local_path: Optional local path if repo is cloned

    Returns:
        IncrementalUpdateStats with update results
    """
    info_logger("=" * 70)
    info_logger(">>>>>>>>>> ingest_direct_push STARTED <<<<<<<<<<")
    info_logger(f"Input: owner='{owner}', repo='{repo}'")
    info_logger(f"Commit range: {before_sha[:7]}..{after_sha[:7]}")
    info_logger(f"repo_local_path={repo_local_path}")
    info_logger("=" * 70)

    gh = GitHubClient()

    # ==========
    # API CALL: GitHub API - Compare two commits
    # ENDPOINT: GET /repos/{owner}/{repo}/compare/{before_sha}...{after_sha}
    # RETURNS: Comparison object with list of changed files
    # =================================
    info_logger(f"Calling GitHub API: repository.compare('{before_sha[:7]}', '{after_sha[:7]}')")

    # Use GitHub's Compare API: GET /repos/{owner}/{repo}/compare/{before}...{after}
    token = await gh._get_token(owner, repo)
    r = await gh._http.get(
        f"/repos/{owner}/{repo}/compare/{before_sha}...{after_sha}",
        headers=gh._auth_headers(token),
    )
    r.raise_for_status()
    compare_data = r.json()

    changed_files = [
        {
            "filename": f["filename"],
            "status": f["status"],
            "previous_filename": f.get("previous_filename"),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in compare_data.get("files", [])
    ]

    info_logger(f"GitHub API returned {len(changed_files)} changed files:")
    for idx, file in enumerate(changed_files):
        filename = file.get("filename")
        status = file.get("status")
        prev = file.get("previous_filename")
        additions = file.get("additions", 0)
        deletions = file.get("deletions", 0)
        info_logger(f"  [{idx + 1}] {filename}")
        info_logger(f"      status={status}, +{additions}/-{deletions} lines")
        if prev:
            info_logger(f"      previous_filename={prev}")

    # Perform incremental update
    info_logger("Creating IncrementalGraphUpdater and starting update...")
    updater = IncrementalGraphUpdater(neo4j_client, gh)
    stats = await updater.update_graph_incrementally(
        owner=owner, repo=repo, changed_files=changed_files, repo_local_path=repo_local_path
    )

    info_logger("=" * 70)
    info_logger(">>>>>>>>>> ingest_direct_push COMPLETED <<<<<<<<<<")
    info_logger(
        f"Final stats: added={stats.files_added}, modified={stats.files_modified}, "
        f"deleted={stats.files_deleted}, renamed={stats.files_renamed}, "
        f"errors={len(stats.errors)}"
    )
    info_logger("=" * 70)

    return stats
