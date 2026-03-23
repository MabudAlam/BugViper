import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

logger = logging.getLogger(__name__)


class Neo4jClient:
    """
    Neo4j database client with connection management.

    Supports both local Neo4j instances and Neo4j AuraDB cloud.

    Example:
        client = Neo4jClient(
            uri="neo4j+s://xxx.databases.neo4j.io",
            user="neo4j",
            password="your-password"
        )
        records, summary, keys = client.run_query("MATCH (n) RETURN n LIMIT 10")
        client.close()
    """

    def __init__(self, uri: str, user: str, password: str, database: Optional[str] = None):
        """
        Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI (e.g., neo4j+s://xxx.databases.neo4j.io)
            user: Database username
            password: Database password
            database: Database name. Pass None or "neo4j" for AuraDB default database.
                      Note: AuraDB requires database=None to use its default database.
        """
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        # For AuraDB, database should be None to use the default database
        # The "neo4j" name is treated as None for AuraDB compatibility
        self.database = database if database and database != "neo4j" else None
        self.connected = False

        # Verify connectivity
        try:
            self.driver.verify_connectivity()
            logger.info("Connected to Neo4j database")
            self.connected = True
        except Exception as e:
            logger.warning(
                "Connection failed: %s. If using AuraDB free tier, check if your "
                "instance is paused at https://console.neo4j.io",
                e,
            )
            logger.info("API will run in mock mode without database")
            self.connected = False
            # Don't raise error to allow API to start in mock mode

    def close(self) -> None:
        """Close the database connection."""
        if self.driver:
            self.driver.close()

    def run_query(
        self, query: str, parameters: Optional[Dict[str, Any]] = None, max_retries: int = 3
    ) -> Tuple[List[Any], Any, List[str]]:
        """
        Execute a Cypher query with explicit session and transaction management.
        Includes retry logic for transient errors.

        Args:
            query: Cypher query string
            parameters: Query parameters
            max_retries: Maximum number of retry attempts for transient errors

        Returns:
            Tuple of (records, summary, keys)
        """
        if not self.connected:
            logger.warning("Database not connected, returning empty results")
            return [], None, []

        # Use explicit session and transaction for proper commit handling
        # This is critical for ensuring data persists, especially in async contexts
        def _execute_query(tx):
            result = tx.run(query, parameters or {})
            records = list(result)
            summary = result.consume()
            keys = result.keys()
            return records, summary, keys

        last_error = None
        for attempt in range(max_retries):
            try:
                with self.driver.session(database=self.database) as session:
                    return session.execute_write(_execute_query)
            except (ServiceUnavailable, SessionExpired, TransientError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    logger.warning(
                        f"Transient error on attempt {attempt + 1}/{max_retries}: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Max retries ({max_retries}) exceeded for query")
                    raise
            except Exception as e:
                logger.error(f"Non-retryable error executing query: {e}")
                raise

        if last_error:
            raise last_error

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.close()


def get_neo4j_client() -> "Neo4jClient":
    """Build a Neo4jClient from environment variables.

    Reads NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, and NEO4J_DATABASE.
    Raises ValueError if required variables are missing.
    """
    uri = os.getenv("NEO4J_URI", "")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    if not uri:
        raise ValueError("NEO4J_URI environment variable is required")
    if not user:
        raise ValueError("NEO4J_USERNAME environment variable is required")
    if not password:
        raise ValueError("NEO4J_PASSWORD environment variable is required")

    return Neo4jClient(uri=uri, user=user, password=password, database=database)
