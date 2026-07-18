"""BugViperFirebaseService - Firebase Admin SDK + Firestore user operations."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from api.models.config import ModelConfig
from common.firebase_init import _initialize_firebase
from common.firebase_models import (
    FirebaseUserData,
    FirebaseUserProfile,
    PRAnalyticsEntry,
    PendingInstallation,
    PRMetadata,
    PrReviewStatus,
    RepoAnalytics,
    ReviewRunData,
    RunAnalytics,
    ToolsConfig,
)


def _to_dict(data: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Serialize a Pydantic model (or plain dict) to a Firestore-ready dict."""
    if isinstance(data, BaseModel):
        return data.model_dump(by_alias=True, exclude_none=True)
    return data


logger = logging.getLogger(__name__)


class BugViperFirebaseService:
    """Service for Firestore user operations."""

    __slots__ = ("_db",)

    def __init__(self, db):
        self._db = db

    @property
    def db(self):
        return self._db

    # ── Firestore ref helpers ──────────────────────────────────────────────

    @staticmethod
    def _repo_key(owner: str, repo: str) -> str:
        return f"{owner}_{repo}"

    def _pr_ref(self, uid: str, owner: str, repo: str, pr_number: int):
        repo_key = self._repo_key(owner, repo)
        return (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
        )

    def _analytics_ref(self, uid: str, owner: str, repo: str):
        repo_key = self._repo_key(owner, repo)
        return (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("analytics")
            .document("summary")
        )

    # ── Analytics ──────────────────────────────────────────────────────────

    @staticmethod
    def _count_resolved(issues: list[dict]) -> int:
        return sum(1 for i in issues if i.get("status") in ("fixed", "resolved"))

    def _recompute_pr_analytics(self, uid: str, owner: str, repo: str, pr_number: int) -> None:
        """Read all runs for a PR and update the repo analytics doc."""
        pr_ref = self._pr_ref(uid, owner, repo, pr_number)
        analytics_ref = self._analytics_ref(uid, owner, repo)

        runs = list(pr_ref.collection("reviews").stream())

        pr_runs = []
        for run_doc in runs:
            run = run_doc.to_dict()
            issues = run.get("issues", [])
            pr_runs.append({
                "run_number": run.get("runNumber", 0),
                "issues": len(issues),
                "resolved": self._count_resolved(issues),
            })

        total_issues = sum(r["issues"] for r in pr_runs)
        total_resolved = sum(r["resolved"] for r in pr_runs)
        positives = sum(
            r.to_dict().get("positivesCount", 0) for r in runs
        )

        now = datetime.now(timezone.utc).isoformat()
        pr_doc = pr_ref.get()
        pr_data = pr_doc.to_dict() if pr_doc.exists else {}
        latest_run = runs[-1].to_dict() if runs else {}

        pr_entry = _to_dict(PRAnalyticsEntry(
            pr_number=pr_number,
            owner=pr_data.get("owner", owner),
            repo=pr_data.get("repo", repo),
            repo_id=pr_data.get("repoId", ""),
            created_at=pr_data.get("createdAt") or latest_run.get("createdAt"),
            last_reviewed_at=latest_run.get("endedAt") or pr_data.get("lastReviewedAt"),
            last_review_type=pr_data.get("lastReviewType"),
            last_reviewed_sha=pr_data.get("lastReviewedSha"),
            review_status=pr_data.get("reviewStatus") or latest_run.get("reviewStatusOverride"),
            runs=[RunAnalytics(**r) for r in pr_runs],
            total_issues=total_issues,
            total_resolved=total_resolved,
            positives=positives,
            merged_at=pr_data.get("mergedAt"),
            closed_at=pr_data.get("closedAt"),
        ))

        existing = analytics_ref.get()
        analytics = existing.to_dict() if existing.exists else {
            "owner": owner,
            "repoName": repo,
            "totalPrs": 0,
            "totalReviews": 0,
            "totalIssuesGenerated": 0,
            "totalIssuesResolved": 0,
            "totalPositives": 0,
            "prs": {},
            "updatedAt": now,
        }

        prs = analytics.get("prs", {})
        prs[str(pr_number)] = pr_entry
        analytics["prs"] = prs
        analytics["totalPrs"] = len(prs)

        total_reviews = 0
        total_issues_generated = 0
        total_issues_resolved = 0
        total_positives = 0
        for p in prs.values():
            total_reviews += len(p.get("runs", []))
            total_issues_generated += p.get("totalIssues", 0)
            total_issues_resolved += p.get("totalResolved", 0)
            total_positives += p.get("positives", 0)

        analytics["totalReviews"] = total_reviews
        analytics["totalIssuesGenerated"] = total_issues_generated
        analytics["totalIssuesResolved"] = total_issues_resolved
        analytics["totalPositives"] = total_positives

        # ── Derived stats ────────────────────────────────────────────────
        created_dates = []
        merge_times = []
        for p in prs.values():
            if p.get("createdAt"):
                try:
                    created_dates.append(datetime.fromisoformat(p["createdAt"]))
                except Exception:
                    pass
            # Merge time: use real mergedAt if available, else approximate
            # Skip PRs that were closed but not merged (closedAt without mergedAt)
            end_time = p.get("mergedAt")
            if not end_time and not p.get("closedAt"):
                end_time = p.get("lastReviewedAt")
            if p.get("createdAt") and end_time:
                try:
                    delta = (
                        datetime.fromisoformat(end_time)
                        - datetime.fromisoformat(p["createdAt"])
                    )
                    merge_times.append(delta.total_seconds() / 3600)
                except Exception:
                    pass

        if created_dates:
            span_days = (datetime.now(timezone.utc) - min(created_dates)).total_seconds() / 86400
            total = len(created_dates)
            if span_days < 7:
                analytics["prsPerWeek"] = float(total)
            else:
                analytics["prsPerWeek"] = round(total / span_days * 7, 1)
        else:
            analytics["prsPerWeek"] = 0.0

        analytics["addressedRate"] = round(
            total_issues_resolved / max(total_issues_generated, 1), 3
        )
        analytics["avgMergeTimeHours"] = round(
            sum(merge_times) / max(len(merge_times), 1), 1
        ) if merge_times else 0.0

        # ── Daily breakdown: aggregate issues caught/resolved by day ──────
        daily: dict[str, dict[str, int]] = {}
        for p in prs.values():
            pr_num = p.get("prNumber") if isinstance(p, dict) else getattr(p, "pr_number", None)
            if not pr_num:
                continue
            pr_ref_for_run = self._pr_ref(uid, owner, repo, pr_num)
            pr_created = (p.get("createdAt") or "")[:10] if isinstance(p, dict) else ""
            for run in p.get("runs", []):
                run_num = run.get("runNumber", 0) if isinstance(run, dict) else run.run_number
                run_ref = pr_ref_for_run.collection("reviews").document(f"run_{run_num}").get()
                if not run_ref.exists:
                    continue
                run_data = run_ref.to_dict()
                date_key = (
                    run_data.get("startedAt")
                    or run_data.get("endedAt")
                    or run_data.get("createdAt")
                    or pr_created
                )[:10]
                if not date_key:
                    continue
                if date_key not in daily:
                    daily[date_key] = {"caught": 0, "resolved": 0, "reviews": 0}
                daily[date_key]["caught"] += run_data.get("issuesCount", 0) if isinstance(run_data, dict) else 0
                daily[date_key]["resolved"] += self._count_resolved(run_data.get("issues", []))
                daily[date_key]["reviews"] += 1
        analytics["dailyBreakdown"] = [
            {"date": d, **v} for d, v in sorted(daily.items())
        ]

        analytics["updatedAt"] = now

        analytics_ref.set(analytics)

    def get_repo_analytics(self, uid: str, owner: str, repo: str) -> RepoAnalytics | None:
        """Return the analytics doc for a repo, or None if it doesn't exist."""
        doc = self._analytics_ref(uid, owner, repo).get()
        if not doc.exists:
            return None
        raw = doc.to_dict()
        prs = {}
        for k, v in raw.get("prs", {}).items():
            runs = [RunAnalytics.model_validate(r) for r in v.get("runs", [])]
            prs[k] = PRAnalyticsEntry.model_validate({**v, "runs": runs})
        return RepoAnalytics.model_validate({**raw, "prs": prs})

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

        # Link any pending GitHub App installation
        if github_username:
            try:
                self._link_pending_installation(uid, github_username)
            except Exception as exc:
                logger.error(
                    "Failed to link pending installation for uid=%s github_username=%s: %s",
                    uid, github_username, exc,
                )

        data = doc_ref.get().to_dict()
        return FirebaseUserProfile(
            uid=uid,
            email=email,
            display_name=display_name,
            github_username=github_username,
            photo_url=photo_url,
            created_at=created_at,
            github_installation_id=data.get("githubInstallationId"),
            account_type=data.get("accountType"),
            repository_selection=data.get("repositorySelection"),
        )

    def _link_pending_installation(self, uid: str, github_username: str) -> bool:
        """Link a pending installation to the user if one exists. Returns True if linked."""
        pending = self.get_pending_installation(github_username)
        if not pending:
            return False
        self.link_installation_to_user(
            uid=uid,
            installation_id=pending["githubInstallationId"],
            account_id=pending["githubAccountId"],
            account_type=pending["accountType"],
            repository_selection=pending.get("repositorySelection"),
        )
        self.delete_pending_installation(github_username)
        return True

    def ensure_user(self, uid: str, firebase_claims: dict) -> FirebaseUserProfile:
        """
        Ensure user doc exists for returning sessions (no GitHub token needed).
        Creates a minimal doc from Firebase token claims if missing.

        Also checks for pending GitHub App installations and links them
        automatically.

        Returns the public user profile.
        """
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = self._db.collection("users").document(uid)
        doc = doc_ref.get()

        if doc.exists:
            doc_ref.update({"lastLogin": now})

            # Try to link pending installation for existing users
            github_username = doc.to_dict().get("githubUsername")
            if github_username:
                self._link_pending_installation(uid, github_username)

            data = doc_ref.get().to_dict()
            return FirebaseUserProfile(
                uid=uid,
                email=data.get("email"),
                display_name=data.get("displayName"),
                github_username=data.get("githubUsername"),
                photo_url=data.get("photoURL"),
                created_at=data.get("createdAt"),
                github_installation_id=data.get("githubInstallationId"),
                account_type=data.get("accountType"),
                repository_selection=data.get("repositorySelection"),
            )

        # First time — create from Firebase token claims
        new_user = FirebaseUserData(
            uid=uid,
            email=firebase_claims.get("email"),
            display_name=firebase_claims.get("name"),
            github_username=firebase_claims.get("nickname"),
            photo_url=firebase_claims.get("picture"),
            created_at=now,
            last_login=now,
        )
        doc_ref.set(_to_dict(new_user))

        profile = FirebaseUserProfile(
            uid=uid,
            email=new_user.email,
            display_name=new_user.display_name,
            github_username=new_user.github_username,
            photo_url=new_user.photo_url,
            created_at=now,
        )

        # Check for pending GitHub App installation
        github_username = new_user.github_username or firebase_claims.get("nickname")
        if github_username:
            if self._link_pending_installation(uid, github_username):
                data = doc_ref.get().to_dict()
                profile = FirebaseUserProfile(
                    uid=uid,
                    email=data.get("email"),
                    display_name=data.get("displayName"),
                    github_username=data.get("githubUsername"),
                    photo_url=data.get("photoURL"),
                    created_at=data.get("createdAt"),
                    github_installation_id=data.get("githubInstallationId"),
                    account_type=data.get("accountType"),
                    repository_selection=data.get("repositorySelection"),
                )

        return profile

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

    # ── GitHub App Installation ──────────────────────────────────────────

    def store_pending_installation(
        self,
        github_username: str,
        installation_id: int,
        account_id: int,
        account_type: str,
        repository_selection: Optional[str] = None,
    ) -> None:
        """Store a pending installation (user hasn't signed up yet)."""
        import uuid
        from datetime import datetime, timedelta, timezone

        ts = datetime.now(timezone.utc)
        data = PendingInstallation(
            github_username=github_username,
            github_installation_id=installation_id,
            github_account_id=account_id,
            account_type=account_type,
            repository_selection=repository_selection,
            created_at=ts.isoformat(),
            expires_at=(ts + timedelta(days=30)).isoformat(),
        )
        self._db.collection("pending_installations").document(github_username).set(
            _to_dict(data)
        )
        logger.info("Stored pending installation for github_username=%s", github_username)

    def get_pending_installation(self, github_username: str) -> Optional[dict]:
        """Get pending installation doc for a github username, or None."""
        doc = self._db.collection("pending_installations").document(github_username).get()
        return doc.to_dict() if doc.exists else None

    def delete_pending_installation(self, github_username: str) -> None:
        """Delete pending installation doc after linking."""
        self._db.collection("pending_installations").document(github_username).delete()
        logger.info("Deleted pending installation for github_username=%s", github_username)

    def link_installation_to_user(
        self,
        uid: str,
        installation_id: int,
        account_id: int,
        account_type: str,
        repository_selection: Optional[str] = None,
    ) -> None:
        """Link a GitHub installation to an existing user document."""
        update = {
            "githubInstallationId": installation_id,
            "githubAccountId": account_id,
            "accountType": account_type,
            "repositorySelection": repository_selection,
        }
        self._db.collection("users").document(uid).update(update)
        logger.info("Linked installation %s to uid=%s", installation_id, uid)

    def get_user_installation(self, uid: str) -> Optional[int]:
        """Return installation_id for a user, or None."""
        doc = self._db.collection("users").document(uid).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("githubInstallationId")

    def get_user_github_username(self, uid: str) -> Optional[str]:
        """Return github username for a user, or None."""
        doc = self._db.collection("users").document(uid).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("githubUsername")

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

    def mark_review_running(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        review_type: str,
    ) -> None:
        """
        Mark a PR as currently being reviewed.

        Creates the PR doc if it doesn't exist. Overwrites RUNNING status
        even if a previous review crashed — safe for recovery.
        """
        repo_key = f"{owner}_{repo}"
        now = datetime.now(timezone.utc).isoformat()

        existing = self.get_pr_metadata(uid, owner, repo, pr_number)
        repo_id = existing.get("repoId", "") if existing else ""

        pr_ref = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
        )
        doc = pr_ref.get()

        payload = {
            "owner": owner,
            "repo": repo,
            "prNumber": pr_number,
            "repoId": repo_id,
            "reviewStatus": PrReviewStatus.RUNNING.value,
            "lastReviewType": review_type,
            "updatedAt": now,
            "failedReasons": [],
        }
        if not doc.exists:
            payload["createdAt"] = now
            pr_ref.set(payload)
        else:
            pr_ref.update(payload)

        logger.info(
            "Marked review running: %s/%s#%s (uid=%s)",
            owner,
            repo,
            pr_number,
            uid,
        )

    def mark_review_completed(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        review_type: str,
        issues_count: int,
        positives_count: int,
        walkthrough_count: int,
        open_issue_count: int,
        run_data: ReviewRunData | dict[str, Any],
        head_sha: str = "",
        base_sha: str = "",
    ) -> str:
        """
        Save the completed review run and update PR-level tallies.

        Returns the run document ID (e.g. "run_2").
        """
        now = datetime.now(timezone.utc).isoformat()

        pr_ref = self._pr_ref(uid, owner, repo, pr_number)

        existing_runs = list(pr_ref.collection("reviews").stream())
        run_number = len(existing_runs) + 1
        run_id = f"run_{run_number}"

        run_dict = _to_dict(run_data)
        run_ref = pr_ref.collection("reviews").document(run_id)
        run_ref.set({**run_dict, "runNumber": run_number, "createdAt": now})

        pr_ref.update(
            {
                "reviewStatus": PrReviewStatus.COMPLETED.value,
                "reviewCount": run_number,
                "openIssueCount": open_issue_count,
                "totalIssuesRaised": (
                    (
                        existing_runs[0].to_dict().get("issuesCount", 0) * (run_number - 1)
                        + issues_count
                    )
                    if run_number > 1
                    else issues_count
                ),
                "totalPositives": (
                    (
                        existing_runs[0].to_dict().get("positivesCount", 0) * (run_number - 1)
                        + positives_count
                    )
                    if run_number > 1
                    else positives_count
                ),
                "lastReviewType": review_type,
                "lastReviewedSha": base_sha if review_type == "full_review" else head_sha,
                "lastReviewBaseSha": base_sha,
                "lastReviewedAt": now,
                "updatedAt": now,
            }
        )

        self._recompute_pr_analytics(uid, owner, repo, pr_number)

        logger.info(
            "Marked review completed: %s/%s#%s run=%s issues=%d",
            owner,
            repo,
            pr_number,
            run_id,
            issues_count,
        )
        return run_id

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

        now = datetime.now(timezone.utc).isoformat()

        pr_ref = self._pr_ref(uid, owner, repo, pr_number)

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
        self._recompute_pr_analytics(uid, owner, repo, pr_number)
        return run_id

    def get_all_review_runs(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch all review runs for a PR, newest first."""
        runs_ref = self._pr_ref(uid, owner, repo, pr_number).collection("reviews")
        docs = list(runs_ref.order_by("runNumber", direction="DESCENDING").stream())
        return [d.to_dict() for d in docs if d.exists]

    def mark_review_comments_resolved(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        resolved_entries: list[dict[str, Any]],
    ) -> int:
        """Mark resolved GitHub comment entries and matching issues in review runs."""
        if not resolved_entries:
            return 0

        resolved_by_comment_id = {
            str(entry.get("comment_id")): entry
            for entry in resolved_entries
            if entry.get("comment_id") is not None
        }
        if not resolved_by_comment_id:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        pr_ref = self._pr_ref(uid, owner, repo, pr_number)
        review_docs = list(pr_ref.collection("reviews").stream())

        updated_comments = 0
        newest_run: dict[str, Any] | None = None
        newest_run_number = -1

        for review_doc in review_docs:
            if not review_doc.exists:
                continue

            run = review_doc.to_dict()
            run_number = int(run.get("runNumber", 0) or 0)
            comment_entries = run.get("githubCommentIds", [])
            issues = run.get("issues", [])
            changed = False

            resolved_issue_keys: set[tuple[str, str]] = set()
            for comment_entry in comment_entries:
                comment_id = comment_entry.get("comment_id")
                resolved_entry = resolved_by_comment_id.get(str(comment_id))
                if not resolved_entry:
                    continue

                if comment_entry.get("status") != "resolved":
                    updated_comments += 1
                comment_entry["status"] = "resolved"
                comment_entry["resolvedAt"] = now
                comment_entry["githubResolved"] = True
                changed = True

                file_name = comment_entry.get("file") or resolved_entry.get("file")
                title = comment_entry.get("title") or resolved_entry.get("title")
                if file_name and title:
                    resolved_issue_keys.add((str(file_name), str(title)))

            if resolved_issue_keys:
                for issue in issues:
                    issue_key = (str(issue.get("file", "")), str(issue.get("title", "")))
                    if issue_key in resolved_issue_keys and issue.get("status") != "fixed":
                        issue["status"] = "resolved"
                        issue["resolvedAt"] = now
                        changed = True

            if changed:
                review_doc.reference.update(
                    {
                        "githubCommentIds": comment_entries,
                        "issues": issues,
                        "updatedAt": now,
                    }
                )
                run["githubCommentIds"] = comment_entries
                run["issues"] = issues

            if run_number > newest_run_number:
                newest_run_number = run_number
                newest_run = run

        if newest_run is not None:
            open_issue_count = len(
                [
                    issue
                    for issue in newest_run.get("issues", [])
                    if issue.get("status") not in ("fixed", "resolved")
                ]
            )
            pr_ref.update({"openIssueCount": open_issue_count, "updatedAt": now})

        self._recompute_pr_analytics(uid, owner, repo, pr_number)

        logger.info(
            "Marked %d review comments resolved for %s/%s#%s",
            updated_comments,
            owner,
            repo,
            pr_number,
        )
        return updated_comments

    def get_last_review_run(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict | None:
        """
        Get the most recent review run for a PR.

        Path: users/{uid}/repos/{owner}_{repo}/prs/{pr_number}/reviews/run_{n}

        Returns:
            The last review run dict with issues, or None if no reviews exist.
        """
        pr_ref = self._pr_ref(uid, owner, repo, pr_number)

        docs = list(pr_ref.collection("reviews").order_by("runNumber", direction="DESCENDING").limit(1).stream())
        if not docs:
            return None
        return docs[0].to_dict()

    def update_previous_run_issues(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        validated_issues: list[dict[str, Any]],
    ) -> None:
        """Update issues in the previous run with their validation status.

        Marks issues as 'fixed' in the previous run when they are no longer present
        in the current code.

        Args:
            uid: User ID
            owner: Repository owner
            repo: Repository name
            pr_number: PR number
            validated_issues: List of validated issues with status field
        """
        if not validated_issues:
            return

        last_run = self.get_last_review_run(uid, owner, repo, pr_number)
        if not last_run or not last_run.get("issues"):
            return

        run_number = last_run.get("runNumber", 1)
        run_id = f"run_{run_number}"

        run_ref = self._pr_ref(uid, owner, repo, pr_number).collection("reviews").document(run_id)

        old_issues = last_run.get("issues", [])
        issue_map = {(i.get("file"), i.get("line_start"), i.get("title")): i for i in old_issues}

        updated = False
        for validated in validated_issues:
            if validated.get("status") != "fixed":
                continue

            key = (
                validated.get("file"),
                validated.get("line_start"),
                validated.get("title"),
            )
            if key in issue_map:
                issue_map[key]["status"] = "fixed"
                issue_map[key]["fixedAt"] = datetime.now(timezone.utc).isoformat()
                issue_map[key]["fixedInRun"] = run_number + 1
                updated = True
                logger.info(f"Marked issue as fixed: {validated.get('title')}")

        if updated:
            run_ref.update({"issues": list(issue_map.values())})
            fixed_count = sum(1 for i in validated_issues if i.get("status") == "fixed")
            logger.info(f"Updated {fixed_count} fixed issues in previous run")

        self._recompute_pr_analytics(uid, owner, repo, pr_number)

    def get_review_run(
        self,
        uid: str,
        owner: str,
        repo: str,
        pr_number: int,
        run_number: int,
    ) -> Optional[dict]:
        """Fetch a specific review run by run number.

        Returns None if the run doesn't exist.
        """
        repo_key = f"{owner}_{repo}"
        run_id = f"run_{run_number}"
        doc = (
            self._db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
            .collection("reviews")
            .document(run_id)
            .get()
        )
        return doc.to_dict() if doc.exists else None

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

    # ── Tools config ───────────────────────────────────────────────────────

    def get_tools_config(self, uid: str) -> ToolsConfig:
        """Read the user's linter tools config, returning defaults if not set."""
        doc = self._db.collection("users").document(uid).collection("config").document("tools").get()
        if not doc.exists:
            return ToolsConfig()
        return ToolsConfig.model_validate(doc.to_dict())

    def save_tools_config(self, uid: str, tools_config: ToolsConfig) -> None:
        """Save the user's linter tools config."""
        self._db.collection("users").document(uid).collection("config").document("tools").set(
            _to_dict(tools_config),
        )

    def setModelConfig(self, uid: str, model_config: ModelConfig) -> None:
        """
        Save the user's model configuration to Firestore.
        """

        if not isinstance(model_config, ModelConfig):
            raise ValueError("model_config must be an instance of ModelConfig")

        repo_ref = self._db.collection("users").document(uid)

        if not repo_ref.get().exists:
            raise ValueError(f"User with uid {uid} does not exist in Firestore")
        
        else:
            repo_ref.update({"modelConfig": _to_dict(model_config)})
            logger.info(f"Saved model configuration for user {uid}")

_firebase_db = _initialize_firebase()
firebase_service = BugViperFirebaseService(_firebase_db)
