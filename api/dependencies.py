from fastapi import Request
from db import Neo4jClient, get_neo4j_client as _build_neo4j_client


def get_current_user(request: Request) -> dict:
    """Return the Firebase user decoded by FirebaseAuthMiddleware."""
    return request.state.user


def get_neo4j_client() -> Neo4jClient:
    """Get Neo4j database client from environment variables."""
    return _build_neo4j_client()
