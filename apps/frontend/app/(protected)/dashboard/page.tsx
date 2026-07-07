"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getDashboardStats,
  listRepoPRs,
  listPRReviews,
  getPRReviewRun,
  listRepositories,
  getInstallationStatus,
  type DashboardStats,
  type PRReviewSummary,
  type ReviewRunSummary,
  type ReviewRunDetail,
  type ReviewIssue,
  type RepoSummary,
  type InstallationStatus,
} from "@/lib/api";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

function fmt(n: number) {
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

function StatCard({ label, value, icon, delay }: { label: string; value: string | number; icon: React.ReactNode; delay: number }) {
  return (
    <div
      className="animate-fade-in-up bg-secondary-background border-2 border-border shadow-shadow rounded-base p-5 flex items-center gap-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_0px_var(--main)]"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="w-10 h-10 rounded-base flex items-center justify-center shrink-0 border-2 bg-main/10 border-main/30">
        <div className="text-main">{icon}</div>
      </div>
      <div className="min-w-0">
        <p className="text-2xl font-bold tabular-nums leading-none text-main">{value}</p>
        <p className="text-xs text-muted-foreground mt-1.5 truncate">{label}</p>
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

  useEffect(() => {
    let cancelled = false;
    listRepoPRs(repo.owner, repo.repoName)
      .then((data) => { if (!cancelled) setPrs(data); })
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
  const [prs, setPrs] = useState<PRReviewSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRepoPRs(repo.owner, repo.repoName)
      .then((data) => { if (!cancelled) setPrs(data); })
      .catch(() => { if (!cancelled) setPrs([]); });
    return () => { cancelled = true; };
  }, [repo.owner, repo.repoName]);

  const prList = prs ?? [];
  const totalIssues = prList.reduce((sum, p) => sum + p.totalIssuesRaised, 0);
  const totalResolved = prList.reduce((sum, p) => sum + (p.totalIssuesRaised - p.openIssueCount), 0);
  const totalReviews = prList.reduce((sum, p) => sum + p.reviewCount, 0);

  return (
    <div className="animate-fade-in-up" style={{ animationDelay: `${100 + idx * 80}ms` }}>
      <Card className="bg-secondary-background shadow-shadow overflow-hidden cursor-pointer hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_0px_var(--main)] transition-all duration-200">
        <button onClick={onSelect} className="w-full text-left">
          <div className="flex items-center justify-between gap-3 px-5 py-3.5">
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-bold text-base text-foreground">{repo.fullName}</span>
              {repo.private && <Badge variant="neutral" className="shrink-0">Private</Badge>}
            </div>
            {repo.language && (
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground shrink-0">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: LANG_COLOURS[repo.language] ?? "#8b949e" }} />
                {repo.language}
              </span>
            )}
          </div>

          <div className="flex items-center gap-4 px-5 py-3 border-t-2 border-border text-sm flex-wrap">
            <span className="text-muted-foreground">
              <span className="font-semibold text-foreground">{totalIssues}</span> issues
            </span>
            <span className="w-px h-4 bg-border hidden sm:block" />
            <span className="text-muted-foreground">
              <span className="font-semibold text-foreground">{totalReviews}</span> review{totalReviews !== 1 ? "s" : ""}
            </span>
            <span className="w-px h-4 bg-border hidden sm:block" />
            <FixRateBadge raised={totalIssues} resolved={totalResolved} />
          </div>
        </button>
      </Card>
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

const STATS_CONFIG: { label: string; icon: string; getValue: (s: DashboardStats) => number }[] = [
  { label: "Repositories", icon: "repo", getValue: (s) => s.total_repos },
  { label: "PRs Reviewed", icon: "pr", getValue: (s) => s.total_prs },
  { label: "Reviews Run", icon: "review", getValue: (s) => s.total_reviews },
  { label: "Issues Raised", icon: "issue", getValue: (s) => s.total_issues_raised },
  { label: "Issues Resolved", icon: "resolved", getValue: (s) => s.total_issues_resolved },
];

function StatsIcon({ icon }: { icon: string }) {
  const cls = "w-4 h-4";
  switch (icon) {
    case "repo":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
        </svg>
      );
    case "pr":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      );
    case "review":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      );
    case "issue":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      );
    case "resolved":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      );
    default:
      return null;
  }
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [repos, setRepos] = useState<RepoSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [sheetRepo, setSheetRepo] = useState<RepoSummary | null>(null);
  const [installation, setInstallation] = useState<InstallationStatus | null>(null);

  useEffect(() => {
    Promise.all([getDashboardStats(), listRepositories(), getInstallationStatus()])
      .then(([s, r, inst]) => {
        setStats(s);
        setRepos(r);
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
      {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-24 rounded-base" />
            ))}
          </div>
        ) : stats ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
            {STATS_CONFIG.map((cfg, i) => (
              <StatCard
                key={cfg.label}
                label={cfg.label}
                value={fmt(cfg.getValue(stats))}
                icon={<StatsIcon icon={cfg.icon} />}
                delay={100 + i * 60}
              />
            ))}
          </div>
        ) : null}

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
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
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
            <div className="space-y-4">
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
