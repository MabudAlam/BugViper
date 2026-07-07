"""Repository management API — backed by Firestore, not Neo4j."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_current_uid
from common.firebase_service import firebase_service

logger = logging.getLogger(__name__)
router = APIRouter()


class RepoSummary(BaseModel):
    owner: str
    repoName: str
    fullName: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    private: bool
    defaultBranch: str
    topics: list[str]
    filesProcessed: int | None
    filesSkipped: int | None
    classesFound: int | None
    functionsFound: int | None
    importsFound: int | None
    totalLines: int | None
    ingestedAt: str | None
    openIssueCount: int
    totalIssuesRaised: int
    reviewCount: int


class PRReviewSummary(BaseModel):
    owner: str
    repo: str
    prNumber: int
    repoId: str
    reviewStatus: str | None
    reviewCount: int
    openIssueCount: int
    totalIssuesRaised: int
    totalPositives: int
    lastReviewType: str | None
    lastReviewedAt: str | None
    lastReviewedSha: str | None
    createdAt: str | None


class ReviewRunSummary(BaseModel):
    runNumber: int
    issuesCount: int
    positivesCount: int
    walkthroughCount: int
    summary: str
    filesChanged: list[str]
    reviewType: str
    startedAt: str | None
    endedAt: str | None
    durationSeconds: float | None


class ReviewRunDetail(BaseModel):
    runNumber: int
    issues: list[dict]
    positiveFindings: list[str]
    summary: str
    filesChanged: list[str]
    reviewType: str
    issuesCount: int
    positivesCount: int
    walkthroughCount: int
    headSha: str | None = None
    baseSha: str | None = None
    startedAt: str | None = None
    endedAt: str | None = None
    durationSeconds: float | None = None
    createdAt: str | None = None
    githubCommentIds: list[dict] = []


def _repo_doc_to_summary(doc_id: str, data: dict[str, Any]) -> RepoSummary:
    parts = doc_id.split("_", 1)
    owner = parts[0] if len(parts) > 0 else doc_id
    repo_name = parts[1] if len(parts) > 1 else doc_id
    return RepoSummary(
        owner=owner,
        repoName=repo_name,
        fullName=data.get("fullName", f"{owner}/{repo_name}"),
        description=data.get("description"),
        language=data.get("language"),
        stars=data.get("stars", 0),
        forks=data.get("forks", 0),
        private=data.get("private", False),
        defaultBranch=data.get("defaultBranch", "main"),
        topics=data.get("topics", []),
        filesProcessed=data.get("filesProcessed"),
        filesSkipped=data.get("filesSkipped"),
        classesFound=data.get("classesFound"),
        functionsFound=data.get("functionsFound"),
        importsFound=data.get("importsFound"),
        totalLines=data.get("totalLines"),
        ingestedAt=data.get("ingestedAt"),
        openIssueCount=0,
        totalIssuesRaised=0,
        reviewCount=0,
    )


def _pr_doc_to_summary(data: dict[str, Any]) -> PRReviewSummary:
    return PRReviewSummary(
        owner=data.get("owner", ""),
        repo=data.get("repo", ""),
        prNumber=data.get("prNumber", 0),
        repoId=data.get("repoId", ""),
        reviewStatus=data.get("reviewStatus"),
        reviewCount=data.get("reviewCount", 0),
        openIssueCount=data.get("openIssueCount", 0),
        totalIssuesRaised=data.get("totalIssuesRaised", 0),
        totalPositives=data.get("totalPositives", 0),
        lastReviewType=data.get("lastReviewType"),
        lastReviewedAt=data.get("lastReviewedAt"),
        lastReviewedSha=data.get("lastReviewedSha"),
        createdAt=data.get("createdAt"),
    )


def _review_doc_to_summary(data: dict[str, Any], run_number: str) -> ReviewRunSummary:
    return ReviewRunSummary(
        runNumber=int(run_number.rsplit("_", 1)[-1]) if "_" in run_number else int(run_number),
        issuesCount=data.get("issuesCount") or data.get("issues_count", 0),
        positivesCount=data.get("positivesCount") or data.get("positives_count", 0),
        walkthroughCount=data.get("walkthroughCount") or data.get("walkthrough_count", 0),
        summary=data.get("summary", ""),
        filesChanged=data.get("filesChanged") or data.get("files_changed", []),
        reviewType=data.get("reviewType") or data.get("review_type", "incremental_review"),
        startedAt=data.get("startedAt") or data.get("started_at"),
        endedAt=data.get("endedAt") or data.get("ended_at"),
        durationSeconds=data.get("durationSeconds") or data.get("duration_seconds"),
    )


def _review_doc_to_detail(data: dict[str, Any], run_id: str) -> ReviewRunDetail:
    return ReviewRunDetail(
        runNumber=int(run_id.rsplit("_", 1)[-1]) if "_" in run_id else int(run_id),
        issues=data.get("issues", []),
        positiveFindings=data.get("positiveFindings") or data.get("positive_findings") or [],
        summary=data.get("summary", ""),
        filesChanged=data.get("filesChanged") or data.get("files_changed", []),
        reviewType=data.get("reviewType") or data.get("review_type", "incremental_review"),
        issuesCount=data.get("issuesCount") or data.get("issues_count", 0),
        positivesCount=data.get("positivesCount") or data.get("positives_count", 0),
        walkthroughCount=data.get("walkthroughCount") or data.get("walkthrough_count", 0),
        headSha=data.get("headSha") or data.get("head_sha"),
        baseSha=data.get("baseSha") or data.get("base_sha"),
        startedAt=data.get("startedAt") or data.get("started_at"),
        endedAt=data.get("endedAt") or data.get("ended_at"),
        durationSeconds=data.get("durationSeconds") or data.get("duration_seconds"),
        createdAt=data.get("createdAt") or data.get("created_at"),
        githubCommentIds=data.get("githubCommentIds") or data.get("github_comment_ids", []),
    )


@router.get("/", response_model=list[RepoSummary])
async def list_repos(uid: str = Depends(get_current_uid)) -> list[RepoSummary]:
    """List all repositories indexed for the authenticated user."""
    db = firebase_service.db
    try:
        docs = db.collection("users").document(uid).collection("repos").stream()
        results = []
        for d in docs:
            results.append(_repo_doc_to_summary(d.id, d.to_dict()))
        return results
    except Exception as exc:
        logger.exception("Failed to list repos for uid=%s: %s", uid, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch repositories")


@router.get("/{owner}/{repo}/prs", response_model=list[PRReviewSummary])
async def list_prs(
    owner: str, repo: str, uid: str = Depends(get_current_uid)
) -> list[PRReviewSummary]:
    """List all PRs reviewed for a repository."""
    db = firebase_service.db
    repo_key = f"{owner}_{repo}"
    try:
        docs = (
            db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .order_by("createdAt", direction="DESCENDING")
            .stream()
        )
        return [_pr_doc_to_summary(d.to_dict()) for d in docs]
    except Exception as exc:
        logger.exception("Failed to list PRs for %s/%s: %s", owner, repo, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch PRs")


@router.get("/{owner}/{repo}/prs/{pr_number}/reviews", response_model=list[ReviewRunSummary])
async def list_pr_reviews(
    owner: str, repo: str, pr_number: int,
    limit: int = Query(20, ge=1, le=50),
    uid: str = Depends(get_current_uid)
) -> list[ReviewRunSummary]:
    """List recent review runs for a specific PR."""
    db = firebase_service.db
    repo_key = f"{owner}_{repo}"
    try:
        docs = (
            db.collection("users")
            .document(uid)
            .collection("repos")
            .document(repo_key)
            .collection("prs")
            .document(str(pr_number))
            .collection("reviews")
            .order_by("runNumber", direction="DESCENDING")
            .limit(limit)
            .stream()
        )
        return [_review_doc_to_summary(d.to_dict(), d.id) for d in docs]
    except Exception as exc:
        logger.exception("Failed to list reviews for %s/%s#%s: %s", owner, repo, pr_number, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch reviews")


@router.get("/{owner}/{repo}/prs/{pr_number}/reviews/{run_number}", response_model=ReviewRunDetail)
async def get_pr_review_run(
    owner: str, repo: str, pr_number: int, run_number: int,
    uid: str = Depends(get_current_uid)
) -> ReviewRunDetail:
    """Fetch a specific review run with full details."""
    run_data = firebase_service.get_review_run(uid, owner, repo, pr_number, run_number)
    if not run_data:
        raise HTTPException(status_code=404, detail="Review run not found")
    run_id = f"run_{run_number}"
    return _review_doc_to_detail(run_data, run_id)


class DashboardStats(BaseModel):
    total_repos: int
    total_prs: int
    total_reviews: int
    total_issues_raised: int
    total_issues_resolved: int
    total_positives: int


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(uid: str = Depends(get_current_uid)) -> DashboardStats:
    """Aggregate stats across all repos and PRs for the dashboard."""
    db = firebase_service.db

    try:
        repos_ref = db.collection("users").document(uid).collection("repos")
        repo_docs = list(repos_ref.stream())
    except Exception as exc:
        logger.exception("Failed to fetch repos for dashboard: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load dashboard data")

    total_repos = len(repo_docs)
    total_prs = 0
    total_reviews = 0
    total_issues_raised = 0
    total_issues_resolved = 0
    total_positives = 0

    for repo_doc in repo_docs:
        try:
            prs_ref = repo_doc.reference.collection("prs")
            for pr_doc in prs_ref.stream():
                pr_data = pr_doc.to_dict()
                total_prs += 1
                total_issues_raised += pr_data.get("totalIssuesRaised", 0)
                total_positives += pr_data.get("totalPositives", 0)
                reviews_ref = pr_doc.reference.collection("reviews")
                for review_doc in reviews_ref.stream():
                    review_data = review_doc.to_dict()
                    total_reviews += 1
                    total_issues_resolved += review_data.get("issuesCount", 0)
        except Exception as exc:
            logger.warning("Failed to traverse PRs for repo %s: %s", repo_doc.id, exc)
            continue

    return DashboardStats(
        total_repos=total_repos,
        total_prs=total_prs,
        total_reviews=total_reviews,
        total_issues_raised=total_issues_raised,
        total_issues_resolved=total_issues_resolved,
        total_positives=total_positives,
    )
