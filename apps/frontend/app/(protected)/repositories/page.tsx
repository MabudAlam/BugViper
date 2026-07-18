"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getRepoOverview,
  listPRReviews,
  getPRReviewRun,
  getRepoAnalytics,
  getInstallationStatus,
  type DashboardStats,
  type PRReviewSummary,
  type ReviewRunSummary,
  type ReviewRunDetail,
  type ReviewIssue,
  type RepoSummary,
  type RepoAnalyticsDetail,
  type InstallationStatus,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

function fmt(n: number | string) {
  if (typeof n === "string") return n;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toString();
}

function formatDT(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function FixRateBadge({ raised, resolved, size = "md" }: { raised: number; resolved: number; size?: "sm" | "md" }) {
  const pct = raised > 0 ? Math.round((resolved / raised) * 100) : 0;
  const r = size === "sm" ? 10 : 14;
  const view = size === "sm" ? 28 : 36;
  const circumference = 2 * Math.PI * r;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className={`relative shrink-0 ${size === "sm" ? "w-8 h-8" : "w-14 h-14"}`}>
      <svg className="w-full h-full -rotate-90" viewBox={`0 0 ${view} ${view}`}>
        <circle cx={view / 2} cy={view / 2} r={r} fill="none" stroke="currentColor" strokeWidth="3" className="text-border" />
        <circle
          cx={view / 2} cy={view / 2} r={r} fill="none" stroke="currentColor" strokeWidth="3" className="text-main"
          strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className={`font-bold text-main ${size === "sm" ? "text-[9px]" : "text-sm"}`}>{pct}%</span>
      </div>
    </div>
  );
}

function ReviewStatusBadge({ status }: { status: string | null | undefined }) {
  if (status === "running" || status === "pending" || status === "dispatched") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-sm font-base bg-chart-2/10 text-chart-2 border-2 border-chart-2/30">
        <svg className="w-2 h-2 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
        </svg>
        Running
      </span>
    );
  }
  if (status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-sm font-base bg-chart-1/10 text-chart-1 border-2 border-chart-1/30">
        <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        Done
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-sm font-base bg-destructive/10 text-destructive border-2 border-destructive/20">
        Failed
      </span>
    );
  }
  return null;
}

function IssueRow({ issue, resolvedAt }: { issue: ReviewIssue; resolvedAt?: string | null }) {
  const severity = issue.severity ?? "info";
  const isResolved = issue.status === "fixed" || issue.status === "resolved";

  return (
    <div className="px-5 py-4 space-y-3 hover:bg-accent/50 transition-colors">
      <div className="flex items-start gap-3">
        <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${
          severity === "critical" || severity === "high" ? "bg-destructive" :
          severity === "medium" ? "bg-chart-2" : "bg-chart-1"
        }`} />
        <div className="flex-1 min-w-0 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-foreground text-sm">{issue.title}</span>
            <span className={`text-[11px] px-2 py-0.5 rounded-full border-2 font-base ${
              severity === "critical" ? "bg-destructive/10 text-destructive border-destructive/30" :
              severity === "high" ? "bg-chart-2/10 text-chart-2 border-chart-2/30" :
              severity === "medium" ? "bg-chart-2/10 text-chart-2 border-chart-2/30" :
              "bg-chart-1/10 text-chart-1 border-chart-1/30"
            }`}>{severity}</span>
            {issue.issue_type && (
              <span className="text-[11px] px-2 py-0.5 rounded-base border-2 border-border text-muted-foreground uppercase tracking-wider font-base">{issue.issue_type.replace(/_/g, " ")}</span>
            )}
            {issue.category && (
              <span className="text-[11px] px-2 py-0.5 rounded-base border-2 border-border text-muted-foreground font-base">{issue.category}</span>
            )}
            {issue.confidence !== undefined && (
              <span className="text-[11px] text-muted-foreground font-base">
                Confidence: <span className="font-semibold text-foreground">{issue.confidence}/10</span>
              </span>
            )}
            {isResolved && resolvedAt && (
              <span className="text-[11px] text-muted-foreground font-base">
                Resolved: <span className="font-semibold text-foreground">{formatDT(resolvedAt)}</span>
              </span>
            )}
          </div>
          {issue.file && (
            <p className="text-xs text-muted-foreground font-mono">
              {issue.file}{issue.line_start ? `:${issue.line_start}` : ""}{issue.line_end && issue.line_end !== issue.line_start ? `-${issue.line_end}` : ""}
            </p>
          )}
        </div>
        {isResolved ? (
          <div className="shrink-0 w-7 h-7 rounded-full bg-main flex items-center justify-center" title={`Resolved ${resolvedAt ? formatDT(resolvedAt) : ""}`}>
            <svg className="w-3.5 h-3.5 text-main-foreground" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
        ) : (
          <div className="shrink-0 w-7 h-7 rounded-full bg-main flex items-center justify-center" title="Pending">
            <svg className="w-3.5 h-3.5 text-main-foreground" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 4h12M6 4a2 2 0 00-2 2 3 3 0 003 3 5 5 0 015 5 5 5 0 015-5 3 3 0 003-3 2 2 0 00-2-2m0 0h-2m-8 0H8m4 10a5 5 0 01-5 5 3 3 0 00-3 3v1h16v-1a3 3 0 00-3-3 5 5 0 01-5-5z" />
            </svg>
          </div>
        )}
      </div>

      {issue.description && (
        <p className="text-sm text-foreground leading-relaxed pl-8">{issue.description}</p>
      )}

      {issue.code_snippet && (
        <div className="pl-8">
          <pre className="text-xs font-mono text-foreground bg-background border-2 border-border rounded-base p-3 overflow-x-auto leading-relaxed whitespace-pre-wrap">{issue.code_snippet}</pre>
        </div>
      )}

      {issue.impact && (
        <div className="pl-8 space-y-1">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Impact</span>
          <p className="text-sm text-muted-foreground leading-relaxed">{issue.impact}</p>
        </div>
      )}

      {issue.suggestion && (
        <div className="pl-8 space-y-1">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Suggestion</span>
          <p className="text-sm text-main leading-relaxed">{issue.suggestion}</p>
        </div>
      )}
    </div>
  );
}

function RunDetailView({ run, onBack }: { run: ReviewRunDetail; onBack: () => void }) {
  const closedIssues = run.issues.filter((i) => i.status === "fixed" || i.status === "resolved").length;

  const resolvedLookup = new Map<string, string>();
  for (const c of run.githubCommentIds) {
    const key = `${c.file ?? ""}|${c.title ?? ""}`;
    if (c.resolvedAt) {
      resolvedLookup.set(key, c.resolvedAt);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="w-8 h-8 rounded-base border-2 border-border flex items-center justify-center hover:bg-main hover:text-main-foreground hover:border-main transition-all duration-150 shrink-0"
          aria-label="Back"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 12H5m7 7l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h3 className="text-lg font-bold text-foreground">Run #{run.runNumber}</h3>
          <p className="text-sm text-muted-foreground">
            {run.reviewType.replace("_", " ")} · {run.startedAt ? timeAgo(run.startedAt) : "—"}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4 text-sm bg-background border-2 border-border rounded-base px-5 py-3 flex-wrap">
        <span className="text-muted-foreground">
          Total issues: <span className="font-semibold text-foreground">{run.issues.length}</span>
        </span>
        <span className="w-px h-4 bg-border" />
        <span className="text-muted-foreground">
          Resolved: <span className="font-semibold text-main">{closedIssues}</span>
        </span>
        <span className="w-px h-4 bg-border" />
        <span className="text-muted-foreground">
          Duration: <span className="font-semibold text-foreground">{run.durationSeconds ? `${Math.round(run.durationSeconds)}s` : "—"}</span>
        </span>
        <span className="w-px h-4 bg-border" />
        <span className="text-muted-foreground">
          Opened: <span className="font-semibold text-foreground">{run.startedAt ? formatDT(run.startedAt) : "—"}</span>
        </span>
      </div>

      {run.summary && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Walkthrough</h4>
          <div className="bg-background border-2 border-border rounded-base px-5 py-4">
            <p className="text-sm text-muted-foreground leading-relaxed">{run.summary}</p>
          </div>
        </div>
      )}

      {run.positiveFindings.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Positive Findings</h4>
          <div className="space-y-2">
            {run.positiveFindings.map((p, i) => (
              <div key={i} className="bg-background border-2 border-border rounded-base px-5 py-3">
                <div className="flex items-start gap-3">
                  <span className="text-sm text-main mt-0.5 shrink-0">✦</span>
                  <p className="text-sm text-foreground leading-relaxed">{p}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {run.issues.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Issues</h4>
          <div className="bg-background border-2 border-border rounded-base divide-y divide-border">
            {run.issues.map((issue, i) => {
              const resolvedAt = resolvedLookup.get(`${issue.file ?? ""}|${issue.title ?? ""}`);
              return <IssueRow key={i} issue={issue} resolvedAt={resolvedAt} />;
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function PRCard({ pr, expanded: forceExpanded, reviews: preloadedReviews, onSelectRun }: {
  pr: PRReviewSummary;
  expanded?: boolean;
  reviews?: ReviewRunSummary[] | null;
  onSelectRun: (pr: PRReviewSummary, runNumber: number) => void;
}) {
  const [reviews, setReviews] = useState<ReviewRunSummary[] | null>(preloadedReviews ?? null);
  const [expanded, setExpanded] = useState(forceExpanded ?? false);

  useEffect(() => {
    if (forceExpanded && preloadedReviews && reviews === null) {
      setReviews(preloadedReviews);
    }
  }, [forceExpanded, preloadedReviews, reviews]);

  const loadReviews = useCallback(() => {
    setExpanded(true);
    if (reviews !== null) return;
    listPRReviews(pr.owner, pr.repo, pr.prNumber)
      .then((data) => setReviews(data))
      .catch(() => setReviews([]));
  }, [pr.owner, pr.repo, pr.prNumber, reviews]);

  return (
    <div className="bg-secondary-background border-2 border-border rounded-base overflow-hidden">
      <button
        onClick={loadReviews}
        className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left hover:bg-accent transition-colors"
      >
        <div className="flex items-center gap-2.5 min-w-0 flex-wrap">
          <span className="text-base font-bold text-foreground">#{pr.prNumber}</span>
          <ReviewStatusBadge status={pr.reviewStatus} />
          {pr.lastReviewType && (
            <span className="text-[11px] px-2 py-0.5 rounded-base border-2 border-border text-muted-foreground uppercase tracking-wider font-base">
              {pr.lastReviewType.replace("_", " ")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-5 text-sm shrink-0">
          <span className="text-muted-foreground"><span className="font-semibold text-foreground">{pr.totalIssuesRaised}</span> issues</span>
          <span className="text-muted-foreground"><span className="font-semibold text-main">{pr.totalIssuesRaised - pr.openIssueCount}</span> fixed</span>
          <span className="text-muted-foreground">{pr.reviewCount} review{pr.reviewCount !== 1 ? "s" : ""}</span>
          <span className="text-muted-foreground">{timeAgo(pr.lastReviewedAt)}</span>
          <svg className={`w-4 h-4 text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </button>

      {expanded && reviews !== null && reviews.length > 0 && (
        <div className="border-t-2 border-border space-y-2 p-4 bg-background/40">
          {reviews.map((run) => (
            <button
              key={run.runNumber}
              onClick={() => onSelectRun(pr, run.runNumber)}
              className="w-full flex items-center justify-between gap-4 px-4 py-3 text-left bg-background border-2 border-border rounded-base hover:bg-accent hover:border-main transition-all duration-150 text-sm"
            >
              <div className="flex items-center gap-3 min-w-0 flex-wrap">
                <span className="font-bold text-main">Run #{run.runNumber}</span>
                <span className="text-[11px] px-2 py-0.5 rounded-base border-2 border-border text-muted-foreground uppercase tracking-wider font-base">{run.reviewType.replace("_", " ")}</span>
                <span className="text-sm text-muted-foreground">{run.startedAt ? timeAgo(run.startedAt) : "—"}</span>
              </div>
              <div className="flex items-center gap-4 text-sm shrink-0">
                <span className="text-muted-foreground"><span className="font-semibold text-foreground">{run.issuesCount}</span> issues</span>
                {run.durationSeconds && <span className="text-muted-foreground font-mono">{Math.round(run.durationSeconds)}s</span>}
                <svg className="w-3.5 h-3.5 text-muted-foreground" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </button>
          ))}
        </div>
      )}
      {expanded && reviews !== null && reviews.length === 0 && (
        <div className="border-t-2 border-border px-5 py-4 text-sm text-muted-foreground text-center">
          No review runs yet.
        </div>
      )}
    </div>
  );
}

function RepoDetailContent({ repo, onClose }: { repo: RepoSummary; onClose: () => void }) {
  const [prs, setPrs] = useState<PRReviewSummary[] | null>(null);
  const [latestPrReviews, setLatestPrReviews] = useState<ReviewRunSummary[] | null>(null);
  const [selectedRun, setSelectedRun] = useState<ReviewRunDetail | null>(null);
  const [loadingRun, setLoadingRun] = useState(false);
  const [dailyData, setDailyData] = useState<{ date: string; caught: number; resolved: number }[]>([]);
  const [analytics, setAnalytics] = useState<RepoAnalyticsDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRepoAnalytics(repo.owner, repo.repoName)
      .then((data) => {
        if (cancelled) return;
        setAnalytics(data);
        const mapped: PRReviewSummary[] = data.prs
          .sort((a, b) => b.prNumber - a.prNumber)
          .map((p) => ({
            owner: p.owner,
            repo: p.repo,
            prNumber: p.prNumber,
            repoId: p.repoId,
            reviewStatus: p.reviewStatus,
            reviewCount: p.reviewCount,
            openIssueCount: p.openIssueCount,
            totalIssuesRaised: p.totalIssuesRaised,
            totalPositives: p.totalPositives,
            lastReviewType: p.lastReviewType,
            lastReviewedAt: p.lastReviewedAt,
            lastReviewedSha: p.lastReviewedSha,
            createdAt: p.createdAt,
          }));
        setPrs(mapped);
        setDailyData((data as any).dailyBreakdown ?? []);
      })
      .catch(() => { if (!cancelled) setPrs([]); });
    return () => { cancelled = true; };
  }, [repo.owner, repo.repoName]);

  useEffect(() => {
    if (!prs || prs.length === 0) return;
    let cancelled = false;
    const latestPr = prs[0];
    listPRReviews(latestPr.owner, latestPr.repo, latestPr.prNumber)
      .then((reviews) => { if (!cancelled) setLatestPrReviews(reviews); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [prs]);

  const handleSelectRun = useCallback(async (pr: PRReviewSummary, runNumber: number) => {
    setLoadingRun(true);
    try {
      const detail = await getPRReviewRun(pr.owner, pr.repo, pr.prNumber, runNumber);
      setSelectedRun(detail);
    } catch {
      // ignore
    } finally {
      setLoadingRun(false);
    }
  }, []);

  const totalIssues = (prs ?? []).reduce((sum, p) => sum + p.totalIssuesRaised, 0);
  const totalResolved = (prs ?? []).reduce((sum, p) => sum + (p.totalIssuesRaised - p.openIssueCount), 0);

  if (selectedRun) {
    return <RunDetailView run={selectedRun} onBack={() => setSelectedRun(null)} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-xl font-bold text-foreground">{repo.fullName}</span>
          {repo.private && <Badge variant="neutral" className="shrink-0">Private</Badge>}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {repo.language && (
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: LANG_COLOURS[repo.language] ?? "#8b949e" }} />
              {repo.language}
            </span>
          )}
          {repo.stars > 0 && <span className="text-sm text-muted-foreground">★ {repo.stars}</span>}
        </div>
      </div>

      <div className="flex items-center gap-4 text-sm bg-background border-2 border-border rounded-base px-5 py-3 flex-wrap">
        <span className="text-muted-foreground"><span className="font-semibold text-foreground">{totalIssues}</span> issues</span>
        <span className="w-px h-4 bg-border hidden sm:block" />
        <span className="text-muted-foreground"><span className="font-semibold text-main">{totalResolved}</span> resolved</span>
        <span className="w-px h-4 bg-border hidden sm:block" />
        <span className="text-muted-foreground"><span className="font-semibold text-foreground">{prs?.length ?? 0}</span> PRs</span>
        <span className="w-px h-4 bg-border hidden sm:block" />
        <FixRateBadge raised={totalIssues} resolved={totalResolved} size="sm" />
      </div>

      <AnalyticsCharts prs={prs ?? []} dailyData={dailyData} analytics={analytics} />

      <div className="space-y-3">
        {prs === null ? (
          <div className="space-y-3">
            <Skeleton className="h-16 w-full rounded-base" />
            <Skeleton className="h-16 w-full rounded-base" />
          </div>
        ) : prs.length > 0 ? (
          prs.map((pr, i) => (
            <PRCard
              key={pr.prNumber}
              pr={pr}
              expanded={i === 0}
              reviews={i === 0 ? latestPrReviews : undefined}
              onSelectRun={handleSelectRun}
            />
          ))
        ) : (
          <div className="text-sm text-muted-foreground text-center py-10">
            No PRs reviewed yet.
          </div>
        )}
      </div>

      {loadingRun && (
        <div className="absolute inset-0 bg-background/60 flex items-center justify-center z-10">
          <div className="w-6 h-6 border-2 border-main border-t-transparent rounded-full animate-spin" />
        </div>
      )}
    </div>
  );
}

function RepoSheet({ repo, open, onClose }: { repo: RepoSummary | null; open: boolean; onClose: () => void }) {
  return (
    <div className={`fixed inset-0 z-50 ${open ? "" : "pointer-events-none"}`}>
      <div
        className={`absolute inset-0 bg-overlay transition-opacity duration-300 ${open ? "opacity-100" : "opacity-0"}`}
        onClick={onClose}
      />
      <div
        className={`absolute top-0 right-0 h-full bg-secondary-background border-l-2 border-border shadow-shadow transition-transform duration-300 ease-out flex flex-col ${open ? "translate-x-0" : "translate-x-full"} w-full sm:w-[85%]`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b-2 border-border shrink-0">
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-base border-2 border-border flex items-center justify-center hover:bg-main hover:text-main-foreground hover:border-main transition-all duration-150"
              aria-label="Close"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <span className="text-sm font-semibold text-foreground">Repository Details</span>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {repo && <RepoDetailContent repo={repo} onClose={onClose} />}
        </div>
      </div>
    </div>
  );
}

function RepoCard({ repo, idx, onSelect }: { repo: RepoSummary; idx: number; onSelect: () => void }) {
  const totalIssues = repo.totalIssuesRaised;
  const totalResolved = totalIssues - repo.openIssueCount;
  const totalReviews = repo.reviewCount;
  const fixPct = totalIssues > 0 ? Math.round((totalResolved / totalIssues) * 100) : 0;

  return (
    <div className="animate-fade-in-up" style={{ animationDelay: `${100 + idx * 80}ms` }}>
      <Card className="bg-secondary-background shadow-shadow overflow-hidden cursor-pointer hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_0px_var(--main)] transition-all duration-200">
        <button onClick={onSelect} className="w-full text-left h-full">
          <div className="px-5 py-4 flex flex-col h-full gap-2.5">
            <div className="flex items-center gap-2 min-w-0">
              <svg className="w-5 h-5 shrink-0 text-main" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              <span className="font-bold text-base text-foreground truncate min-w-0">{repo.fullName}</span>
            </div>

            <div className="flex items-center gap-4 text-sm flex-wrap">
              <span className="text-muted-foreground">
                <span className="font-semibold text-foreground">{totalIssues}</span> issue{totalIssues !== 1 ? "s" : ""}
              </span>
              <span className="text-muted-foreground">
                <span className="font-semibold text-foreground">{totalReviews}</span> review{totalReviews !== 1 ? "s" : ""}
              </span>
              <span className={fixPct >= 80 ? "text-green-600 font-semibold" : "text-muted-foreground"}>
                {fixPct}% fixed
              </span>
            </div>

            <div className="flex items-center gap-3 text-xs text-muted-foreground justify-end mt-auto">
              {repo.language && (
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: LANG_COLOURS[repo.language] ?? "#8b949e" }} />
                  {repo.language}
                </span>
              )}
              {repo.filesProcessed != null && (
                <span>{repo.filesProcessed} file{repo.filesProcessed !== 1 ? "s" : ""}</span>
              )}
            </div>
          </div>
        </button>
      </Card>
    </div>
  );
}

function BarChart({ data, labelKey, valueKey, color, label, valueLabel }: {
  data: { date: string; [key: string]: any }[];
  labelKey: string;
  valueKey: string;
  color: string;
  label: string;
  valueLabel: string;
}) {
  const max = Math.max(...data.map((d) => d[valueKey] ?? 0), 1);
  return (
    <div className="bg-background border-2 border-border rounded-base p-4">
      <h4 className="text-sm font-semibold text-foreground mb-3">{label}</h4>
      <div className="flex items-end gap-2 h-24">
        {data.map((d) => {
          const val = d[valueKey] ?? 0;
          const h = (val / max) * 96;
          return (
            <div key={d[labelKey]} className="flex-1 flex flex-col items-center gap-1 min-w-0">
              <div className="w-full flex items-end justify-center" style={{ height: 96 }}>
                <div
                  className="w-full rounded-sm transition-all duration-200"
                  style={{ height: `${Math.max(h, val > 0 ? 4 : 0)}px`, backgroundColor: color }}
                  title={`${val} ${valueLabel}`}
                />
              </div>
              <span className="text-[10px] text-muted-foreground">{d[labelKey].slice(5)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StackedBarChart({ data, label, bar1, bar2, color1, color2, label1, label2 }: {
  data: { date: string; [key: string]: any }[];
  label: string;
  bar1: string;
  bar2: string;
  color1: string;
  color2: string;
  label1: string;
  label2: string;
}) {
  const max = Math.max(...data.map((d) => (d[bar1] ?? 0) + (d[bar2] ?? 0)), 1);
  return (
    <div className="bg-background border-2 border-border rounded-base p-4">
      <h4 className="text-sm font-semibold text-foreground mb-3">{label}</h4>
      <div className="flex items-end gap-2 h-24">
        {data.map((d) => {
          const v1 = d[bar1] ?? 0;
          const v2 = d[bar2] ?? 0;
          return (
            <div key={d.date} className="flex-1 flex flex-col items-center gap-1 min-w-0">
              <div className="relative w-full flex flex-col items-center justify-end" style={{ height: 96 }}>
                <div
                  className="w-full rounded-t-sm transition-all duration-200"
                  style={{ height: `${(v1 / max) * 96}px`, backgroundColor: color1 }}
                  title={`${v1} ${label1}`}
                />
                <div
                  className="w-full rounded-b-sm transition-all duration-200"
                  style={{ height: `${(v2 / max) * 96}px`, backgroundColor: color2 }}
                  title={`${v2} ${label2}`}
                />
              </div>
              <span className="text-[10px] text-muted-foreground">{d.date.slice(5)}</span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ backgroundColor: color1 }} /> {label1}</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ backgroundColor: color2 }} /> {label2}</span>
      </div>
    </div>
  );
}

function AnalyticsCharts({ prs, dailyData, analytics }: {
  prs: PRReviewSummary[];
  dailyData: { date: string; caught: number; resolved: number }[];
  analytics: RepoAnalyticsDetail | null;
}) {

  const totalIssues = prs.reduce((s, p) => s + p.totalIssuesRaised, 0);
  const totalResolved = prs.reduce((s, p) => s + (p.totalIssuesRaised - p.openIssueCount), 0);
  const addressedRate = totalIssues > 0 ? Math.round((totalResolved / totalIssues) * 100) : 0;
  const avgMergeTime = analytics?.avgMergeTimeHours ?? 0;
  const prsPerWeek = analytics?.prsPerWeek ?? 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-background border-2 border-border rounded-base p-4 flex flex-col gap-3">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">Addressed Rate</span>
          <div className="flex items-center gap-4">
            <span className="text-3xl font-bold text-main">{addressedRate}%</span>
            <div className="flex-1 h-3 bg-border rounded-full overflow-hidden">
              <div className="h-full bg-main rounded-full transition-all" style={{ width: `${addressedRate}%` }} />
            </div>
          </div>
        </div>

        <div className="bg-background border-2 border-border rounded-base p-4 flex flex-col gap-3">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">Avg Time to Merge</span>
          <div className="flex items-center gap-2">
            <span className="text-3xl font-bold text-foreground">{avgMergeTime > 0 ? (
              avgMergeTime < 1 / 60 ? `${Math.round(avgMergeTime * 3600)}s` :
              avgMergeTime < 1 ? `${Math.round(avgMergeTime * 60)}m` :
              `${avgMergeTime.toFixed(1)}h`
            ) : "—"}</span>
            {avgMergeTime > 0 && <span className="text-xs text-muted-foreground">from PR creation</span>}
          </div>
        </div>

        <div className="bg-background border-2 border-border rounded-base p-4 flex flex-col gap-3">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">PRs / Week</span>
          <span className="text-3xl font-bold text-foreground">{prsPerWeek > 0 ? prsPerWeek : "—"}</span>
        </div>

        <div className="bg-background border-2 border-border rounded-base p-4 flex flex-col gap-3">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">Total Reviews</span>
          <span className="text-3xl font-bold text-foreground">{analytics?.totalReviews ?? prs.reduce((s, p) => s + p.reviewCount, 0)}</span>
        </div>
      </div>

      {dailyData.length > 0 && (
        <>
          <StackedBarChart
            data={dailyData}
            label="Bugs Caught / Resolved per Day"
            bar1="caught"
            bar2="resolved"
            color1="var(--main)"
            color2="var(--border)"
            label1="Caught"
            label2="Resolved"
          />

          <BarChart
            data={dailyData}
            labelKey="date"
            valueKey="caught"
            color="var(--main)"
            label="Bugs Caught per Day"
            valueLabel="bugs"
          />
        </>
      )}
    </div>
  );
}

const LANG_COLOURS: Record<string, string> = {
  Python: "#3572A5",
  JavaScript: "#f1e05a",
  TypeScript: "#3178c6",
  Go: "#00ADD8",
  Rust: "#dea584",
  Java: "#b07219",
  Ruby: "#701516",
  C: "#555555",
  Cpp: "#f34b7d",
  CSharp: "#4F5D95",
  Kotlin: "#A97BFF",
  Scala: "#c22d40",
  Swift: "#F05138",
  PHP: "#4F5D95",
  Haskell: "#5e5086",
};

export default function RepositoriesPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [repos, setRepos] = useState<RepoSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [sheetRepo, setSheetRepo] = useState<RepoSummary | null>(null);
  const [installation, setInstallation] = useState<InstallationStatus | null>(null);

  useEffect(() => {
    Promise.all([getRepoOverview(), getInstallationStatus()])
      .then(([overview, inst]) => {
        setStats(overview.stats);
        setRepos(overview.repos);
        setInstallation(inst);
      })
      .catch(() => {
        setStats(null);
        setRepos([]);
        setInstallation(null);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      <div>
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground">Repositories</h2>
            {installation?.linked && installation.settingsUrl ? (
              <Button variant="default" size="sm" asChild>
                <a href={installation.settingsUrl} target="_blank" rel="noopener noreferrer">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                  Add Repo
                </a>
              </Button>
            ) : (
              <Button variant="default" size="sm" asChild>
                <a
                  href={process.env.NEXT_PUBLIC_GITHUB_APP_INSTALL_URL || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                  Install App
                </a>
              </Button>
            )}
          </div>

          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <Skeleton key={i} className="h-28 w-full rounded-base" />
              ))}
            </div>
          ) : repos.length === 0 ? (
            <Card className="bg-secondary-background shadow-shadow py-16">
              <CardContent className="flex flex-col items-center justify-center gap-4 text-center">
                <div className="w-12 h-12 rounded-base bg-main/10 border-2 border-main/30 flex items-center justify-center">
                  <svg className="w-6 h-6 text-main" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
                  </svg>
                </div>
                <div>
                  <p className="font-medium text-foreground">No repositories indexed</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    Add a repository to start tracking code reviews
                  </p>
                </div>
                {installation?.linked && installation.settingsUrl ? (
                  <Button variant="default" size="sm" asChild>
                    <a href={installation.settingsUrl} target="_blank" rel="noopener noreferrer">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                      </svg>
                      Add Repository
                    </a>
                  </Button>
                ) : (
                  <Button variant="default" size="sm" asChild>
                    <a
                      href={process.env.NEXT_PUBLIC_GITHUB_APP_INSTALL_URL || "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                      </svg>
                      Install App
                    </a>
                  </Button>
                )}
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {repos.map((repo, i) => (
                <RepoCard
                  key={`${repo.owner}/${repo.repoName}`}
                  repo={repo}
                  idx={i}
                  onSelect={() => setSheetRepo(repo)}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <RepoSheet repo={sheetRepo} open={sheetRepo !== null} onClose={() => setSheetRepo(null)} />
    </div>
  );
}
