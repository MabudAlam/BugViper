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


class PRDetailsFromAnalytics(BaseModel):
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
    runs: list[dict]

class RepoAnalyticsDetail(BaseModel):
    owner: str
    repoName: str
    totalPrs: int
    totalReviews: int
    totalIssuesGenerated: int
    totalIssuesResolved: int
    totalPositives: int
    prs: list[PRDetailsFromAnalytics]

class DashboardStats(BaseModel):
    total_repos: int
    total_prs: int
    total_reviews: int
    total_issues_raised: int
    total_issues_resolved: int
    total_positives: int

class RepoOverviewResponse(BaseModel):
    stats: DashboardStats
    repos: list[RepoSummary]


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
async def list_repos(
    limit: int = Query(50, ge=1, le=200),
    uid: str = Depends(get_current_uid)
) -> list[RepoSummary]:
    """List repositories indexed for the authenticated user (includes analytics data)."""
    db = firebase_service.db
    try:
        docs = db.collection("users").document(uid).collection("repos").limit(limit).stream()
        results = []
        for d in docs:
            summary = _repo_doc_to_summary(d.id, d.to_dict())
            analytics_doc = d.reference.collection("analytics").document("summary").get()
            if analytics_doc.exists:
                a = analytics_doc.to_dict()
                summary.openIssueCount = a.get("totalIssuesGenerated", 0) - a.get("totalIssuesResolved", 0)
                summary.totalIssuesRaised = a.get("totalIssuesGenerated", 0)
                summary.reviewCount = a.get("totalReviews", 0)
            results.append(summary)
        return results
    except Exception as exc:
        logger.exception("Failed to list repos for uid=%s: %s", uid, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch repositories")


@router.get("/{owner}/{repo}/prs", response_model=list[PRReviewSummary])
async def list_prs(
    owner: str, repo: str,
    limit: int = Query(50, ge=1, le=200),
    uid: str = Depends(get_current_uid)
) -> list[PRReviewSummary]:
    """List PRs reviewed for a repository."""
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
            .limit(limit)
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


@router.get("/overview", response_model=RepoOverviewResponse)
async def repo_overview(
    limit: int = Query(50, ge=1, le=200),
    uid: str = Depends(get_current_uid)
) -> RepoOverviewResponse:
    """Combined repos + stats in one call (replaces separate list + stats calls)."""
    db = firebase_service.db
    try:
        repos_ref = db.collection("users").document(uid).collection("repos")
        repo_docs = list(repos_ref.stream())
    except Exception as exc:
        logger.exception("Failed to fetch repos for overview: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load data")

    total_repos = len(repo_docs)
    total_prs = 0
    total_reviews = 0
    total_issues_raised = 0
    total_issues_resolved = 0
    total_positives = 0
    results: list[RepoSummary] = []

    for i, repo_doc in enumerate(repo_docs):
        if i >= limit:
            break
        summary = _repo_doc_to_summary(repo_doc.id, repo_doc.to_dict())
        analytics_doc = repo_doc.reference.collection("analytics").document("summary").get()
        if analytics_doc.exists:
            a = analytics_doc.to_dict()
            summary.openIssueCount = a.get("totalIssuesGenerated", 0) - a.get("totalIssuesResolved", 0)
            summary.totalIssuesRaised = a.get("totalIssuesGenerated", 0)
            summary.reviewCount = a.get("totalReviews", 0)
            total_prs += a.get("totalPrs", 0)
            total_reviews += a.get("totalReviews", 0)
            total_issues_raised += a.get("totalIssuesGenerated", 0)
            total_issues_resolved += a.get("totalIssuesResolved", 0)
            total_positives += a.get("totalPositives", 0)
        results.append(summary)

    return RepoOverviewResponse(
        stats=DashboardStats(
            total_repos=total_repos,
            total_prs=total_prs,
            total_reviews=total_reviews,
            total_issues_raised=total_issues_raised,
            total_issues_resolved=total_issues_resolved,
            total_positives=total_positives,
        ),
        repos=results,
    )


@router.get("/{owner}/{repo}/analytics", response_model=RepoAnalyticsDetail)
async def get_repo_analytics(
    owner: str, repo: str, uid: str = Depends(get_current_uid)
) -> RepoAnalyticsDetail:
    """Return analytics for a repo (per-PR breakdown with run counts)."""
    analytics = firebase_service.get_repo_analytics(uid, owner, repo)
    if analytics is None:
        return RepoAnalyticsDetail(
            owner=owner,
            repoName=repo,
            totalPrs=0, totalReviews=0,
            totalIssuesGenerated=0, totalIssuesResolved=0,
            totalPositives=0, prs=[],
        )

    prs_list: list[PRDetailsFromAnalytics] = []
    for pr_num_str, p in analytics.prs.items():
        review_count = len(p.runs)
        open_issue_count = p.total_issues - p.total_resolved
        prs_list.append(PRDetailsFromAnalytics(
            owner=p.owner or owner,
            repo=p.repo or repo,
            prNumber=p.pr_number,
            repoId=p.repo_id,
            reviewStatus=p.review_status,
            reviewCount=review_count,
            openIssueCount=open_issue_count,
            totalIssuesRaised=p.total_issues,
            totalPositives=p.positives,
            lastReviewType=p.last_review_type,
            lastReviewedAt=p.last_reviewed_at,
            lastReviewedSha=p.last_reviewed_sha,
            createdAt=p.created_at,
            runs=[r.model_dump(by_alias=True) for r in p.runs],
        ))

    return RepoAnalyticsDetail(
        owner=analytics.owner,
        repoName=analytics.repo_name,
        totalPrs=analytics.total_prs,
        totalReviews=analytics.total_reviews,
        totalIssuesGenerated=analytics.total_issues_generated,
        totalIssuesResolved=analytics.total_issues_resolved,
        totalPositives=analytics.total_positives,
        prs=prs_list,
    )


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(uid: str = Depends(get_current_uid)) -> DashboardStats:
    """Aggregate stats across all repos from analytics docs (1 read per repo)."""
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
        analytics_doc = repo_doc.reference.collection("analytics").document("summary").get()
        if not analytics_doc.exists:
            continue
        a = analytics_doc.to_dict()
        total_prs += a.get("totalPrs", 0)
        total_reviews += a.get("totalReviews", 0)
        total_issues_raised += a.get("totalIssuesGenerated", 0)
        total_issues_resolved += a.get("totalIssuesResolved", 0)
        total_positives += a.get("totalPositives", 0)

    return DashboardStats(
        total_repos=total_repos,
        total_prs=total_prs,
        total_reviews=total_reviews,
        total_issues_raised=total_issues_raised,
        total_issues_resolved=total_issues_resolved,
        total_positives=total_positives,
    )
