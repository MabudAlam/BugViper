"""BugViperFirebaseService - Firebase Admin SDK + Firestore user operations."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from common.firebase_init import _initialize_firebase
from common.firebase_models import FirebaseUserData, FirebaseUserProfile, PRMetadata


def _to_dict(data: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Serialize a Pydantic model (or plain dict) to a Firestore-ready dict."""
    if isinstance(data, BaseModel):
        return data.model_dump(by_alias=True, exclude_none=True)
    return data


logger = logging.getLogger(__name__)


class BugViperFirebaseService:
    """Singleton service for Firestore user operations."""

    _instance: Optional["BugViperFirebaseService"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not BugViperFirebaseService._initialized:
            self._db = _initialize_firebase()
            BugViperFirebaseService._initialized = True

    @property
    def db(self):
        return self._db

    # ── User CRUD ─────────────────────────────────────────────────────────

    def create_or_update_user(
        self,
        uid: str,
        github_access_token: str,
        github_profile: dict,
        firebase_claims: dict,
    ) -> FirebaseUserProfile:
        """
        Create or update a user document in Firestore.

        Args:
            uid: Firebase user ID.
            github_access_token: GitHub OAuth token (stored for later API use).
            github_profile: Dict from GitHub /user API (may be empty on failure).
            firebase_claims: Decoded Firebase ID token claims (fallback values).

        Returns the public user profile (no access token).
        """
        now = datetime.now(timezone.utc).isoformat()

        email = github_profile.get("email") or firebase_claims.get("email")
        display_name = github_profile.get("name") or firebase_claims.get("name")
        github_username = github_profile.get("login")
        photo_url = github_profile.get("avatar_url") or firebase_claims.get("picture")

        doc_ref = self._db.collection("users").document(uid)
        doc = doc_ref.get()

        user_doc = FirebaseUserData(
            uid=uid,
            email=email,
            display_name=display_name,
            github_username=github_username,
            github_access_token=github_access_token,
            photo_url=photo_url,
            last_login=now,
        )

        if doc.exists:
            doc_ref.update(_to_dict(user_doc))
            created_at = doc.to_dict().get("createdAt")
        else:
            full_doc = {**_to_dict(user_doc), "createdAt": now}
            doc_ref.set(full_doc)
            created_at = now

        return FirebaseUserProfile(
            uid=uid,
            email=email,
            display_name=display_name,
            github_username=github_username,
            photo_url=photo_url,
            created_at=created_at,
        )

    def ensure_user(self, uid: str, firebase_claims: dict) -> FirebaseUserProfile:
        """
        Ensure user doc exists for returning sessions (no GitHub token needed).
        Creates a minimal doc from Firebase token claims if missing.

        Returns the public user profile.
        """
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = self._db.collection("users").document(uid)
        doc = doc_ref.get()

        if doc.exists:
            doc_ref.update({"lastLogin": now})
            data = doc.to_dict()
            return FirebaseUserProfile(
                uid=uid,
                email=data.get("email"),
                display_name=data.get("displayName"),
                github_username=data.get("githubUsername"),
                photo_url=data.get("photoURL"),
                created_at=data.get("createdAt"),
            )

        # First time — create from Firebase token claims
        new_user = FirebaseUserData(
            uid=uid,
            email=firebase_claims.get("email"),
            display_name=firebase_claims.get("name"),
            photo_url=firebase_claims.get("picture"),
            created_at=now,
            last_login=now,
        )
        doc_ref.set(_to_dict(new_user))

        return FirebaseUserProfile(
            uid=uid,
            email=new_user.email,
            display_name=new_user.display_name,
            photo_url=new_user.photo_url,
            created_at=now,
        )

    def get_user(self, uid: str) -> Optional[FirebaseUserProfile]:
        """
        Fetch user profile from Firestore by UID.
        Returns None if user doc does not exist.
        """
        doc = self._db.collection("users").document(uid).get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        return FirebaseUserProfile(
            uid=uid,
            email=data.get("email"),
            display_name=data.get("displayName"),
            github_username=data.get("githubUsername"),
            photo_url=data.get("photoURL"),
            created_at=data.get("createdAt"),
        )

    def get_github_token(self, uid: str) -> Optional[str]:
        """
        Retrieve the stored GitHub access token for a user.
        Returns None if user doc doesn't exist or has no token.
        """
        doc = self._db.collection("users").document(uid).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("githubAccessToken")

    def checkIfRepoIndexedOrNot(self, uid: str, owner: str, repo: str) -> bool:
        """
        Check if a repo has been indexed for a user by looking for the metadata doc.

        Returns True if the repo is fully ingested, False otherwise.
        """
        repo_key = f"{owner}_{repo}"
        doc = (
            self._db.collection("users").document(uid).collection("repos").document(repo_key).get()
        )

        if not doc.exists:
            return False

        return doc.to_dict().get("ingestionStatus") == "ingested"

    # ── Repo metadata ─────────────────────────────────────────────────────

    def upsert_repo_metadata(
        self,
        uid: str,
        owner: str,
        repo: str,
        data: BaseModel | dict[str, Any],
    ) -> None:
        """
        Create or update the repo metadata document.

        Path: users/{uid}/repos/{owner}_{repo}

        Merges `data` into the document — safe to call multiple times
        (e.g. once at job dispatch with status=pending, again at completion
        with ingestion stats).

        Accepts a Pydantic model (RepoMetadata, RepoIngestionUpdate, etc.)
        or a plain dict for partial updates.
        """
        repo_key = f"{owner}_{repo}"
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = self._db.collection("users").document(uid).collection("repos").document(repo_key)
        doc = doc_ref.get()
        payload = {**_to_dict(data), "updatedAt": now}
        if doc.exists:
            doc_ref.update(payload)
        else:
            payload["createdAt"] = now
            doc_ref.set(payload)
        logger.info(f"Upserted repo metadata for {owner}/{repo} (uid={uid})")

    def get_repo_metadata(self, uid: str, owner: str, repo: str) -> Optional[dict]:
        """Fetch the repo metadata document. Returns None if not found."""
        repo_key = f"{owner}_{repo}"
        doc = (
            self._db.collection("users").document(uid).collection("repos").document(repo_key).get()
        )
        return doc.to_dict() if doc.exists else None

    def delete_repo_metadata(self, uid: str, owner: str, repo: str) -> None:
        """Delete the repo metadata document and all subcollections (prs, reviews)."""
        repo_key = f"{owner}_{repo}"
        repo_ref = self._db.collection("users").document(uid).collection("repos").document(repo_key)
        # Delete prs subcollection and their reviews
        for pr_doc in repo_ref.collection("prs").stream():
            for review_doc in pr_doc.reference.collection("reviews").stream():
                review_doc.reference.delete()
            pr_doc.reference.delete()
        repo_ref.delete()
        logger.info(f"Deleted repo metadata for {owner}/{repo} (uid={uid})")

    def list_repos(self, uid: str) -> list[dict]:
        """List all ingested repos for a user."""
        docs = self._db.collection("users").document(uid).collection("repos").stream()
        return [doc.to_dict() for doc in docs]

    def find_project_owner_id(self, github_username: str) -> Optional[str]:
        """Return the Firebase UID for a given GitHub username, or None if not found."""
        docs = (
            self._db.collection("users")
            .where("githubUsername", "==", github_username)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.id

        return None

    def upsert_pr_metadata(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        pr_data: PRMetadata,
    ) -> None:
        """
        Create or update the PR metadata document.

        Path: users/{uid}/repos/{owner}_{repo}/prs/{pr_number}

        Accepts a PRMetadata model instance.
        """
        repo_key = f"{owner}_{repo}"
        now = datetime.now(timezone.utc).isoformat()

        pr_data.updated_at = now

        doc_ref = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
        )
        doc = doc_ref.get()
        if doc.exists:
            payload = pr_data.model_dump(by_alias=True, exclude_none=True)
            # Avoid overwriting creation date and tracked metrics on an update
            payload.pop("createdAt", None)
            payload.pop("reviewCount", None)
            payload.pop("openIssueCount", None)
            doc_ref.update(payload)
        else:
            pr_data.created_at = now
            payload = pr_data.model_dump(by_alias=True, exclude_none=True)
            doc_ref.set(payload)

    def get_pr_metadata(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> Optional[dict]:
        """Fetch the PR metadata document. Returns None if not found."""
        repo_key = f"{owner}_{repo}"
        doc = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
            .get()
        )
        return doc.to_dict() if doc.exists else None

    # ── Review runs ────────────────────────────────────────────────────────

    def save_review_run(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        run_data: BaseModel | dict[str, Any],
    ) -> str:
        """
        Save a review run document and update the PR's tallies.

        Path: users/{uid}/repos/{owner}_{repo}/prs/{pr_number}/reviews/run_{n}

        Accepts a ReviewRunData model or a plain dict.
        Returns the run document ID (e.g. "run_2").
        """
        if not isinstance(pr_number, int) or pr_number <= 0:
            raise ValueError(
                f"save_review_run: pr_number must be a positive int, got {pr_number!r}"
            )

        repo_key = f"{owner}_{repo}"
        now = datetime.now(timezone.utc).isoformat()

        pr_ref = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
        )

        # Determine next run number
        existing = list(pr_ref.collection("reviews").stream())
        run_number = len(existing) + 1
        run_id = f"run_{run_number}"

        run_dict = _to_dict(run_data)
        run_ref = pr_ref.collection("reviews").document(run_id)
        run_ref.set({**run_dict, "runNumber": run_number, "createdAt": now})

        # Update PR-level tallies
        open_count = len([i for i in run_dict.get("issues", []) if i.get("status") != "fixed"])
        pr_doc = pr_ref.get()
        if pr_doc.exists:
            pr_ref.update(
                {
                    "reviewCount": run_number,
                    "openIssueCount": open_count,
                    "lastReviewedAt": now,
                }
            )

        logger.info(f"Saved review run {run_id} for {owner}/{repo}#{pr_number}")
        return run_id

    # ── Customer support ──────────────────────────────────────────────────

    def save_customer_query(
        self,
        name: str,
        email: str,
        subject: str,
        category: str,
        message: str,
        priority: str = "medium",
    ) -> str:
        """
        Save a customer support query to the top-level ``customer_queries`` collection.

        Returns the auto-generated Firestore document ID.
        """
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = self._db.collection("customer_queries").document()
        doc_ref.set(
            {
                "name": name,
                "email": email,
                "subject": subject,
                "category": category,
                "message": message,
                "priority": priority,
                "status": "open",
                "createdAt": now,
                "updatedAt": now,
            }
        )
        logger.info("Saved customer query %s from %s", doc_ref.id, email)
        return doc_ref.id

    def get_latest_review_run(
        self, uid: str, owner: str, repo: str, pr_number: int
    ) -> Optional[dict]:
        """
        Fetch the most recent review run document for this specific PR.

        Scoping is enforced at two levels:
        1. Firestore path: .../prs/{pr_number}/reviews  (path-level isolation)
        2. Field check: returned doc must have prNumber == pr_number  (defensive guard)

        Returns None if no previous run exists for this PR.
        """
        if not isinstance(pr_number, int):
            logger.error("get_latest_review_run: pr_number must be an int, got %r", pr_number)
            return None

        repo_key = f"{owner}_{repo}"
        runs_ref = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
            .collection("reviews")
        )
        docs = list(runs_ref.order_by("runNumber", direction="DESCENDING").limit(1).stream())
        if not docs:
            return None

        run = docs[0].to_dict()

        # Defensive check: verify the doc belongs to this PR (guards against path bugs)
        stored_pr = run.get("prNumber")
        if stored_pr is not None and stored_pr != pr_number:
            logger.error(
                "Review run mismatch: expected prNumber=%s, got %s — ignoring stale data",
                pr_number,
                stored_pr,
            )
            return None

        return run


# Module-level convenience instance (triggers Firebase init on import)
firebase_service = BugViperFirebaseService()
