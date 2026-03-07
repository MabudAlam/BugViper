
import logging
from fastapi import APIRouter, HTTPException, Query, Depends, Path
from typing import List, Dict, Any, Optional

from db.client import Neo4jClient
from db.code_serarch_layer import CodeSearchService
from db.schema import CodeGraphSchema
from api.dependencies import get_neo4j_client, get_current_user
from api.services.firebase_service import firebase_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _cleanup_firestore_repo(uid: str, owner: str, repo_name: str) -> None:
    """
    Delete the Firestore repo metadata document.  Non-fatal — logs on failure.
    """
    try:
        firebase_service.delete_repo_metadata(uid, owner, repo_name)
    except Exception as exc:
        logger.warning(
            "Failed to delete Firestore repo metadata (uid=%s owner=%s repo=%s): %s",
            uid, owner, repo_name, exc,
            exc_info=True,
        )


def get_query_service(db: Neo4jClient = Depends(get_neo4j_client)) -> CodeSearchService:
    """Dependency to get query service."""
    return CodeSearchService(db)


def get_schema_service(db: Neo4jClient = Depends(get_neo4j_client)) -> CodeGraphSchema:
    """Dependency to get schema service."""
    return CodeGraphSchema(db)


@router.get("/")
async def list_repositories(
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    List all repositories ingested by the current user (read from Firestore).
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Authenticated user has no UID")

    try:
        repositories = firebase_service.list_repos(uid)
        return {
            "repositories": repositories,
            "total": len(repositories),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list repositories: {str(e)}")


@router.get("/getAllRepositories")
async def get_all_repositories(
    userName: str = Query(..., description="Username to filter repositories (legacy parameter)"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get all repositories (legacy endpoint for frontend compatibility).
    The userName parameter is included for API compatibility but filters all repos.
    """
    try:
        repositories = query_service.list_repositories()
        
        # Filter by username if provided and repositories have owner field
        if userName:
            filtered_repos = [
                repo for repo in repositories 
                if repo.get('owner') and repo['owner'].lower() == userName.lower()
            ]
            repositories = filtered_repos
        
        return {
            "repositories": repositories,
            "total": len(repositories),
            "filtered_by_user": userName if userName else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repositories: {str(e)}")


@router.get("/{username}/{repo_name}")
async def get_repository(
    username: str = Path(..., description="Repository owner/username"),
    repo_name: str = Path(..., description="Repository name"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository details by username and repository name.
    """
    try:
        repo_id = f"{username}/{repo_name}"
        
        # Get repository overview
        overview = query_service.get_repo_overview(repo_id)
        if not overview:
            raise HTTPException(status_code=404, detail="Repository not found")
        
        # Get additional metadata
        repositories = query_service.list_repositories()
        repo_metadata = next((r for r in repositories if r['id'] == repo_id), None)
        
        return {
            **overview,
            "metadata": repo_metadata,
            "repository_id": repo_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository: {str(e)}")


@router.get("/{repo_id}")
async def get_repository_by_id(
    repo_id: str = Path(..., description="Repository ID"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository details by repository ID.
    """
    try:
        overview = query_service.get_repo_overview(repo_id)
        if not overview:
            raise HTTPException(status_code=404, detail="Repository not found")
        
        # Get additional metadata
        repositories = query_service.list_repositories()
        repo_metadata = next((r for r in repositories if r['id'] == repo_id), None)
        
        return {
            **overview,
            "metadata": repo_metadata,
            "repository_id": repo_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository: {str(e)}")


@router.delete("/{username}/{repo_name}")
async def delete_repository_by_name(
    username: str = Path(..., description="Repository owner/username"),
    repo_name: str = Path(..., description="Repository name"),
    query_service: CodeSearchService = Depends(get_query_service),
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Delete a repository by username and repo name from Neo4j and Firestore.
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Authenticated user has no UID")

    repo_id = f"{username}/{repo_name}"
    try:
        deleted = query_service.delete_repository(repo_id)

        # Always clean up Firestore regardless of whether Neo4j had the repo
        _cleanup_firestore_repo(uid, username, repo_name)

        if deleted:
            return {"message": f"Repository {repo_id} deleted successfully", "deleted_repository_id": repo_id}

        raise HTTPException(status_code=404, detail="Repository not found in graph database")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete repository: {str(e)}")


@router.delete("/{repo_id}")
async def delete_repository(
    repo_id: str = Path(..., description="Repository ID to delete (owner/repo format)"),
    query_service: CodeSearchService = Depends(get_query_service),
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Delete a repository and all its associated data from Neo4j and Firestore.
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Authenticated user has no UID")

    if "/" not in repo_id:
        logger.warning(
            "delete_repository called with malformed repo_id %r (expected owner/repo format)",
            repo_id,
        )
        raise HTTPException(
            status_code=400,
            detail=f"repo_id must be in owner/repo format, got: {repo_id!r}",
        )

    try:
        deleted = query_service.delete_repository(repo_id)

        # Parse owner/repo from repo_id and clean up Firestore
        owner, repo_name = repo_id.split("/", 1)
        _cleanup_firestore_repo(uid, owner, repo_name)

        if deleted:
            return {"message": f"Repository {repo_id} deleted successfully", "deleted_repository_id": repo_id}

        raise HTTPException(status_code=404, detail="Repository not found in graph database")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete repository: {str(e)}")


@router.get("/{username}/{repo_name}/stats")
async def get_repository_stats(
    username: str = Path(..., description="Repository owner/username"),
    repo_name: str = Path(..., description="Repository name"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository statistics by username and repo name.
    """
    try:
        repo_id = f"{username}/{repo_name}"
        stats = query_service.get_repository_stats(repo_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Repository not found")
        
        return {
            "repository_id": repo_id,
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository stats: {str(e)}")


@router.get("/{repo_id}/stats")
async def get_repository_stats_by_id(
    repo_id: str = Path(..., description="Repository ID"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository statistics by repository ID.
    """
    try:
        stats = query_service.get_repository_stats(repo_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Repository not found")
        
        return {
            "repository_id": repo_id,
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository stats: {str(e)}")


@router.get("/{username}/{repo_name}/files")
async def list_repository_files_by_name(
    username: str = Path(..., description="Repository owner/username"),
    repo_name: str = Path(..., description="Repository name"),
    path: str = Query(None, description="Optional path filter"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    List files in a repository by username and repo name.
    """
    try:
        repo_id = f"{username}/{repo_name}"
        files = query_service.get_repository_files(repo_id)
        
        # Filter by path if provided
        if path:
            filtered_files = [
                file_info for file_info in files 
                if file_info.get('path', '').startswith(path)
            ]
            files = filtered_files
        
        return {
            "repository_id": repo_id,
            "files": files,
            "total": len(files),
            "path_filter": path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list repository files: {str(e)}")


@router.get("/{repo_id}/files")
async def list_repository_files_by_id(
    repo_id: str = Path(..., description="Repository ID"),
    path: str = Query(None, description="Optional path filter"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    List files in a repository by repository ID.
    """
    try:
        files = query_service.get_repository_files(repo_id)
        
        # Filter by path if provided
        if path:
            filtered_files = [
                file_info for file_info in files 
                if file_info.get('path', '').startswith(path)
            ]
            files = filtered_files
        
        return {
            "repository_id": repo_id,
            "files": files,
            "total": len(files),
            "path_filter": path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list repository files: {str(e)}")


@router.get("/{username}/{repo_name}/files/content")
async def get_file_content_by_name(
    username: str = Path(..., description="Repository owner/username"),
    repo_name: str = Path(..., description="Repository name"),
    path: str = Query(..., description="File path to get content for"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get file content by username, repo name, and file path.
    """
    try:
        repo_id = f"{username}/{repo_name}"
        
        # Find the file by path
        files = query_service.get_repository_files(repo_id)
        target_file = next((f for f in files if f['path'] == path), None)
        
        if not target_file:
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        
        # Reconstruct file content
        content = query_service.reconstruct_file(target_file['id'])
        
        if content is None:
            raise HTTPException(status_code=404, detail=f"File content not available: {path}")
        
        return {
            "repository_id": repo_id,
            "file_path": path,
            "file_id": target_file['id'],
            "content": content,
            "metadata": target_file
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get file content: {str(e)}")


@router.get("/{repo_id}/files/{file_path:path}/reconstruct")
async def reconstruct_file_content(
    repo_id: str = Path(..., description="Repository ID"),
    file_path: str = Path(..., description="File path to reconstruct"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Reconstruct file content by repository ID and file path.
    """
    try:
        # Find the file by path
        files = query_service.get_repository_files(repo_id)
        target_file = next((f for f in files if f['path'] == file_path), None)
        
        if not target_file:
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
        
        # Reconstruct file content
        content = query_service.reconstruct_file(target_file['id'])
        
        if content is None:
            raise HTTPException(status_code=404, detail=f"File content not available: {file_path}")
        
        return {
            "repository_id": repo_id,
            "file_path": file_path,
            "file_id": target_file['id'],
            "content": content,
            "metadata": target_file,
            "reconstructed": True
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reconstruct file: {str(e)}")


@router.post("/{repo_id}/update")
async def update_repository(
    repo_id: str = Path(..., description="Repository ID to update"),
    schema_service: CodeGraphSchema = Depends(get_schema_service)
) -> Dict[str, Any]:
    """
    Update a repository by re-ingesting it.
    """
    try:
        # This would need to trigger a re-ingestion process
        # For now, return a message indicating the functionality needs to be implemented
        return {
            "message": f"Repository update functionality for {repo_id} needs to be implemented",
            "repository_id": repo_id,
            "note": "This endpoint should trigger re-ingestion of the repository"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update repository: {str(e)}")


@router.get("/{repo_id}/verify")
async def verify_repository_reconstruction(
    repo_id: str = Path(..., description="Repository ID to verify"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Verify that all files in a repository can be reconstructed.
    """
    try:
        verification = query_service.verify_repository_reconstruction(repo_id)
        return verification
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify repository: {str(e)}")


@router.get("/{repo_id}/dependencies")
async def get_repository_dependencies(
    repo_id: str = Path(..., description="Repository ID"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository dependencies.
    """
    try:
        dependencies = query_service.get_repo_dependencies(repo_id)
        return {
            "repository_id": repo_id,
            "dependencies": dependencies,
            "total": len(dependencies)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository dependencies: {str(e)}")


@router.get("/{repo_id}/config-files")
async def get_repository_config_files(
    repo_id: str = Path(..., description="Repository ID"),
    query_service: CodeSearchService = Depends(get_query_service)
) -> Dict[str, Any]:
    """
    Get repository configuration files.
    """
    try:
        config_files = query_service.get_repo_config_files(repo_id)
        return {
            "repository_id": repo_id,
            "config_files": config_files,
            "total": len(config_files)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get repository config files: {str(e)}")