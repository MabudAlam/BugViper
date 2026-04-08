import asyncio
import os
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.embedder import embed_nodes_in_neo4j
from common.github_client import GitHubAuthError, GitHubClient
from common.job_models import JobStatus
from db import CodeGraphSchema, GraphIngestionService, Neo4jClient

from .jobs import JobManager
from .tree_sitter_router import GraphBuilder


@dataclass
class IngestionStats:
    """Statistics for ingestion operations."""

    files_processed: int = 0
    files_skipped: int = 0
    classes_found: int = 0
    functions_found: int = 0
    imports_found: int = 0
    total_lines: int = 0
    errors: List[str] = None
    embedding_status: str = "skipped"  # "skipped" | "completed" | "failed"
    nodes_embedded: int = 0
    embedding_error: Optional[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class AdvancedIngestionEngine:
    """
    Advanced ingestion engine that combines the multi-language
    capabilities with Neo4j storage and GitHub App authentication.
    """

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j_client = neo4j_client
        self.job_manager = JobManager()
        self.graph_builder = GraphBuilder(
            self.neo4j_client, self.job_manager, asyncio.get_event_loop()
        )
        self.schema = CodeGraphSchema(neo4j_client)
        self.ingestion_service = GraphIngestionService(neo4j_client)

        # Initialize GitHub client if credentials are available
        try:
            self.github_client = GitHubClient()
        except ValueError as e:
            print(f"Warning: GitHub App credentials not configured: {e}")
            self.github_client = None

    def setup(self):
        """Initialize database schema."""
        print("Setting up database schema...")
        self.schema.create_constraints_and_indexes()
        # Also setup the advanced graph schema
        self.graph_builder.create_schema()
        print("✓ Schema ready")

    def get_stats(self) -> Dict[str, Any]:
        """Get ingestion statistics."""
        return {"jobs": list(self.job_manager.jobs.values())}

    async def ingest_github_repository(
        self, owner: str, repo_name: str, branch: Optional[str] = None, clear_existing: bool = False
    ) -> IngestionStats:
        """
        Ingest a GitHub repository (public or private) using GitHub App authentication.

        Args:
            owner: GitHub repository owner/organization
            repo_name: Repository name
            branch: Branch to clone (optional, defaults to default branch)
            clear_existing: Whether to clear existing data before ingestion

        Returns:
            IngestionStats with ingestion results
        """
        if not self.github_client:
            raise ValueError(
                "GitHub App credentials not configured. Cannot access private repositories."
            )

        repo_id = f"{owner}/{repo_name}"
        print(f"\n{'=' * 60}")
        print("GITHUB REPOSITORY INGESTION")
        print(f"{'=' * 60}")
        print(f"Repository: {repo_id}")

        # Check if we have access to the repository
        print("Checking repository access...")
        has_access = await self.github_client.check_repository_access(owner, repo_name)
        if not has_access:
            raise GitHubAuthError(f"No access to {repo_id}. Ensure GitHub App is installed.")

        # Get repository info
        repo_info = await self.github_client.get_repository_info(owner, repo_name)
        print(
            f"Access confirmed - Repository: {repo_info['full_name']} ({'private' if repo_info['private'] else 'public'})"
        )

        # Clear existing data if requested
        if clear_existing:
            print(f"\n⚠️  Clearing existing data for {repo_id}...")
            self.schema.clear_repository(repo_id)
            print("✓ Existing data cleared")

        # Clone the repository
        print("\nCloning repository...")
        try:
            clone_path = await self.github_client.clone_repository(owner, repo_name, branch=branch)
            print(f"✓ Repository cloned to: {clone_path}")

            # Create job for tracking
            job_id = self.job_manager.create_job(
                "github_repository_ingestion",
                owner=owner,
                repo_name=repo_name,
                repo_url=f"https://github.com/{owner}/{repo_name}",
                clone_path=str(clone_path),
            )

            try:
                self.job_manager.update_job_status(job_id, JobStatus.RUNNING)

                repo_id = f"{owner}/{repo_name}"
                print(f"Repository ID: {repo_id}")

                # Use the advanced graph builder to analyze the repository
                # (build_project_graph creates the Repository node via add_repository_to_graph)
                print("\nRunning advanced multi-language analysis...")

                # Build the graph using the advanced system
                result = await self.graph_builder.build_project_graph(
                    str(clone_path), include_dependencies=False, owner=owner, repo_name=repo_name
                )

                # Create user node and link to repository after graph is built
                self.ingestion_service.create_user(owner)
                self._link_user_to_repository(
                    owner, repo_id, f"https://github.com/{owner}/{repo_name}", str(clone_path)
                )

                # Generate and store embeddings for semantic search via OpenRouter.
                # Reads source_code from Neo4j nodes — does NOT need the cloned files.
                # Safe to skip if OPENROUTER_API_KEY is not set.
                embedding_status = "skipped"
                nodes_embedded = 0
                embedding_error_msg: Optional[str] = None

                if os.getenv("OPENROUTER_API_KEY"):
                    print("\nGenerating embeddings for semantic search...")
                    try:
                        embed_stats = embed_nodes_in_neo4j(self.neo4j_client)
                        nodes_embedded = sum(embed_stats.values())
                        embedding_status = "completed"
                        for label, count in embed_stats.items():
                            if count:
                                print(f"  ✓ Embedded {count} {label} nodes")
                        print(f"  ✓ Total: {nodes_embedded} nodes embedded")
                    except Exception as embed_err:
                        embedding_status = "failed"
                        embedding_error_msg = str(embed_err)
                        print(f"  ⚠ Embedding failed: {embed_err}")
                        print(f"  ℹ Retry via: POST /api/v1/ingest/{owner}/{repo_name}/embed")
                else:
                    print("\n⚠ OPENROUTER_API_KEY not set — skipping semantic embeddings")

                # Clean up cloned repository
                print("\nCleaning up cloned repository...")
                if clone_path.exists():
                    shutil.rmtree(clone_path)
                    print("✓ Cleanup completed")

                # Update job status
                self.job_manager.update_job_status(job_id, JobStatus.COMPLETED)

                # Create stats
                stats = IngestionStats(
                    files_processed=result.get("files_processed", 0),
                    files_skipped=result.get("files_skipped", 0),
                    classes_found=result.get("classes_found", 0),
                    functions_found=result.get("functions_found", 0),
                    imports_found=result.get("imports_found", 0),
                    total_lines=result.get("total_lines", 0),
                    errors=result.get("errors", []),
                    embedding_status=embedding_status,
                    nodes_embedded=nodes_embedded,
                    embedding_error=embedding_error_msg,
                )

                print("\n✅ GitHub repository ingestion completed!")
                print(f"Files processed: {stats.files_processed}")
                print(f"Classes found: {stats.classes_found}")
                print(f"Functions found: {stats.functions_found}")

                return stats

            except Exception:
                # Clean up on error
                if clone_path.exists():
                    shutil.rmtree(clone_path)
                self.job_manager.update_job_status(job_id, JobStatus.FAILED)
                raise

        except GitHubAuthError:
            raise
        except Exception as e:
            print(f"\n❌ GitHub ingestion failed: {str(e)}")
            return IngestionStats(errors=[str(e)])

    def _link_user_to_repository(
        self, username: str, repo_id: str, url: str = None, local_path: str = None
    ):
        """Link a User node to an existing Repository node created by the graph builder."""
        with self.neo4j_client.driver.session() as session:
            session.run(
                """
                MATCH (u:User {username: $username})
                MATCH (r:Repository {repo: $repo_id})
                SET r.url = $url, r.path = $local_path
                MERGE (u)-[:OWNS]->(r)
            """,
                username=username,
                repo_id=repo_id,
                url=url,
                local_path=local_path,
            )

    def close(self):
        """Close database connections."""
        self.neo4j_client.close()
