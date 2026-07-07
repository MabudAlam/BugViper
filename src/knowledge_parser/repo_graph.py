"""Repo graph storage via Firebase Storage + Firestore index."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from common.firebase_init import get_storage_bucket, get_storage_client

logger = logging.getLogger(__name__)

_COLLECTION = "repo_graphs"


def _doc_id(owner: str, repo: str) -> str:
    return f"{owner}___{repo}"


def _doc_path(owner: str, repo: str) -> str:
    return f"{_COLLECTION}/{_doc_id(owner, repo)}"


def _storage_path(owner: str, repo: str) -> str:
    return f"{owner}/{repo}/graph.json"


def get_graph_meta(owner: str, repo: str) -> dict[str, Any] | None:
    """Return Firestore metadata for a repo's graph. Returns None if not found."""
    from common.firebase_service import firebase_service

    doc = firebase_service._db.document(_doc_path(owner, repo)).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def download_graph(owner: str, repo: str) -> dict[str, Any] | None:
    """Download graph JSON from Firebase Storage. Returns None if not found."""
    try:
        bucket = get_storage_client()
        blob_name = _storage_path(owner, repo)
        blob = bucket.blob(blob_name)
        if not blob.exists():
            return None
        json_str = blob.download_as_text()
        return json.loads(json_str)
    except Exception as exc:
        logger.warning("Failed to download graph for %s/%s: %s", owner, repo, exc)
        return None


def upload_graph(
    owner: str,
    repo: str,
    graph_json: dict[str, Any],
    sha: str,
    file_count: int = 0,
    func_count: int = 0,
) -> bool:
    """Upload graph JSON to Firebase Storage and update Firestore index."""
    try:
        from common.firebase_service import firebase_service

        bucket = get_storage_client()
        blob_name = _storage_path(owner, repo)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(graph_json, default=str), content_type="application/json"
        )

        meta_ref = firebase_service._db.document(_doc_path(owner, repo))
        meta_ref.set(
            {
                "storage_path": f"gs://{get_storage_bucket()}/{blob_name}",
                "sha": sha,
                "file_count": file_count,
                "func_count": func_count,
                "status": "ready",
                "updated_at": datetime.now(timezone.utc),
            },
            merge=True,
        )
        logger.info("Uploaded graph for %s/%s (sha=%s, files=%d)", owner, repo, sha[:7], file_count)
        return True
    except Exception as exc:
        logger.error("Failed to upload graph for %s/%s: %s", owner, repo, exc)
        return False


def update_graph_status(owner: str, repo: str, status: str) -> None:
    """Update the status field in Firestore (e.g. 'building', 'ready', 'missing')."""
    try:
        from common.firebase_service import firebase_service

        meta_ref = firebase_service._db.document(_doc_path(owner, repo))
        meta_ref.set({"status": status, "updated_at": datetime.now(timezone.utc)}, merge=True)
    except Exception as exc:
        logger.warning("Failed to update graph status for %s/%s: %s", owner, repo, exc)


def _pr_storage_path(owner: str, repo: str, pr_number: int, sha: str, filename: str) -> str:
    return f"prs/{owner}/{repo}/{pr_number}/{sha}/{filename}"


def upload_pr_call_graph(
    owner: str,
    repo: str,
    pr_number: int,
    sha: str,
    call_graph_json: str,
    blast_radius_md: str,
    diff_text: str,
) -> dict[str, str]:
    """Upload call_graph.json, blast_radius.md, and diff.patch to Firebase Storage.

    Returns dict of filename -> storage_path for each uploaded file.
    """
    try:
        from common.firebase_service import firebase_service

        bucket = get_storage_client()
        uploaded: dict[str, str] = {}
        base_path = _pr_storage_path(owner, repo, pr_number, sha, "")

        files = {
            "call_graph.json": (call_graph_json, "application/json"),
            "blast_radius.md": (blast_radius_md, "text/markdown"),
            "diff.patch": (diff_text, "text/plain"),
        }

        for filename, (content, content_type) in files.items():
            blob_name = f"{base_path}{filename}"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(content, content_type=content_type)
            uploaded[filename] = f"gs://{get_storage_bucket()}/{blob_name}"
            logger.info("Uploaded %s for %s/%s PR#%s", filename, owner, repo, pr_number)

        meta_ref = firebase_service._db.document(_doc_path(owner, repo))
        meta_ref.set(
            {
                "pr_call_graph": {
                    str(pr_number): {
                        "sha": sha,
                        "storage_paths": uploaded,
                        "updated_at": datetime.now(timezone.utc),
                    }
                }
            },
            merge=True,
        )

        return uploaded
    except Exception as exc:
        logger.error(
            "Failed to upload PR call graph for %s/%s PR#%s: %s",
            owner,
            repo,
            pr_number,
            exc,
        )
        return {}
