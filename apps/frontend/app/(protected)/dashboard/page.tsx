"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BugViperFullLogo } from "@/components/logo";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  getDashboardStats,
  listRepositories,
  listRepoPRs,
  type DashboardStats,
  type RepoSummary,
  type PRReviewSummary,
} from "@/lib/api";

function fmt(n: number) {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toString();
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

function ReviewStatusBadge({ status }: { status: string | null | undefined }) {
  if (status === "running" || status === "pending" || status === "dispatched") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-700 border border-amber-500/30">
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
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-700 border border-emerald-500/30">
        <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        Done
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-destructive/10 text-destructive border border-destructive/20">
        Failed
      </span>
    );
  }
  return null;
}

function StatCard({
  label,
  value,
  icon,
  color,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <Card className="flex-1 min-w-0">
      <CardContent className="px-5 py-5 flex items-center gap-4">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
          style={{ backgroundColor: `${color}18`, border: `1px solid ${color}30` }}
        >
          <div style={{ color }}>{icon}</div>
        </div>
        <div className="min-w-0">
          <p className="text-2xl font-bold tabular-nums" style={{ color }}>
            {value}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function PRRow({ pr }: { pr: PRReviewSummary }) {
  const resolved = pr.totalIssuesRaised - pr.openIssueCount;
  const issuePct =
    pr.totalIssuesRaised > 0
      ? Math.round((resolved / pr.totalIssuesRaised) * 100)
      : 0;

  return (
    <div className="flex items-center gap-3 py-3 px-4 hover:bg-accent/50 rounded-lg transition-colors group">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <a
            href={`https://github.com/${pr.owner}/${pr.repo}/pull/${pr.prNumber}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-primary hover:underline"
          >
            #{pr.prNumber}
          </a>
          <ReviewStatusBadge status={pr.reviewStatus} />
          {pr.lastReviewType && (
            <Badge variant="outline" className="text-xs">
              {pr.lastReviewType.replace("_", " ")}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-4 mt-1.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {timeAgo(pr.lastReviewedAt)}
          </span>
          <span>{pr.reviewCount} review{pr.reviewCount !== 1 ? "s" : ""}</span>
        </div>
      </div>

      <div className="flex items-center gap-5 text-xs shrink-0">
        <div className="text-center min-w-[3rem]">
          <p className="font-semibold text-foreground">{pr.totalIssuesRaised}</p>
          <p className="text-muted-foreground">raised</p>
        </div>
        <div className="text-center min-w-[3rem]">
          <p className="font-semibold text-emerald-600">{resolved}</p>
          <p className="text-muted-foreground">resolved</p>
        </div>
        <div className="text-center min-w-[3rem]">
          <p className="font-semibold text-orange-500">{pr.totalPositives}</p>
          <p className="text-muted-foreground">positives</p>
        </div>
        {pr.totalIssuesRaised > 0 && (
          <div className="w-20">
            <div className="flex justify-between text-xs mb-1">
              <span className="text-muted-foreground">fix rate</span>
              <span className="font-medium" style={{ color: "var(--primary)" }}>
                {issuePct}%
              </span>
            </div>
            <div className="h-1.5 bg-secondary rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${issuePct}%`,
                  backgroundColor: issuePct > 70 ? "#16a34a" : issuePct > 40 ? "#d97706" : "#dc2626",
                }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function RepoCard({ repo }: { repo: RepoSummary }) {
  const [prs, setPrs] = useState<PRReviewSummary[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listRepoPRs(repo.owner, repo.repoName)
      .then((data) => { if (!cancelled) setPrs(data); })
      .catch(() => { if (!cancelled) setPrs([]); });
    return () => { cancelled = true; };
  }, [repo.owner, repo.repoName]);

  const loaded = prs !== null;
  const totalIssues = (prs ?? []).reduce((sum, p) => sum + p.totalIssuesRaised, 0);
  const totalResolved = (prs ?? []).reduce((sum, p) => sum + (p.totalIssuesRaised - p.openIssueCount), 0);
  const totalPositives = (prs ?? []).reduce((sum, p) => sum + p.totalPositives, 0);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="px-5 py-4 pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <a
                href={`https://github.com/${repo.fullName}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-semibold text-sm hover:text-primary transition-colors truncate"
              >
                {repo.fullName}
              </a>
              {repo.private && (
                <Badge variant="outline" className="text-xs shrink-0">Private</Badge>
              )}
              <Badge
                variant={repo.ingestionStatus === "completed" ? "default" : "secondary"}
                className="text-xs shrink-0"
              >
                {repo.ingestionStatus === "completed" ? "Indexed" : repo.ingestionStatus}
              </Badge>
            </div>
            {repo.description && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2 leading-relaxed">
                {repo.description}
              </p>
            )}
          </div>

          <div className="flex items-center gap-4 text-xs shrink-0">
            <div className="text-center">
              <p className="font-bold text-foreground">{repo.filesProcessed ?? "—"}</p>
              <p className="text-muted-foreground">files</p>
            </div>
            <div className="text-center">
              <p className="font-bold text-foreground">{repo.functionsFound ?? "—"}</p>
              <p className="text-muted-foreground">funcs</p>
            </div>
            <div className="text-center">
              <p className="font-bold text-foreground">{repo.classesFound ?? "—"}</p>
              <p className="text-muted-foreground">classes</p>
            </div>
          </div>
        </div>
      </CardHeader>

      <div className="px-5 pb-1">
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {repo.language && (
            <span className="flex items-center gap-1">
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: LANG_COLOURS[repo.language] ?? "#8b949e" }}
              />
              {repo.language}
            </span>
          )}
          {repo.stars > 0 && (
            <span className="flex items-center gap-0.5">★ {repo.stars}</span>
          )}
          {repo.forks > 0 && (
            <span className="flex items-center gap-0.5">⑂ {repo.forks}</span>
          )}
          <span className="ml-auto">Indexed {timeAgo(repo.ingestedAt)}</span>
        </div>
      </div>

      <CardContent className="px-0 pt-3 pb-4">
        {(prs ?? []).length > 0 ? (
          <div className="divide-y">
            {(prs ?? []).slice(0, 5).map((pr) => (
              <PRRow key={pr.prNumber} pr={pr} />
            ))}
            {(prs ?? []).length > 5 && (
              <div className="py-2 px-4 text-xs text-muted-foreground text-center">
                +{(prs ?? []).length - 5} more PRs
              </div>
            )}
          </div>
        ) : !loaded ? (
          <div className="space-y-3 px-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
        ) : (
          <div className="px-4 py-6 text-center text-xs text-muted-foreground">
            No PR reviews yet. Open a PR and mention @bugviper to get started.
          </div>
        )}

        {(prs ?? []).length > 0 && (
          <div className="px-4 pt-3">
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-xs h-8"
              onClick={() => setOpen(!open)}
            >
              {open ? "Show less" : `Show all ${(prs ?? []).length} PRs`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
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

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [repos, setRepos] = useState<RepoSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getDashboardStats(), listRepositories()])
      .then(([s, r]) => {
        setStats(s);
        setRepos(r);
      })
      .catch(() => {
        setStats(null);
        setRepos([]);
      })
      .finally(() => setLoading(false));
  }, []);

  const activeRepos = repos.filter((r) => r.ingestionStatus === "completed");
  const issuesFixed = stats ? stats.total_issues_raised - (stats.total_issues_raised - stats.total_issues_resolved) : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Your code review activity overview
          </p>
        </div>
        <div className="flex items-center gap-2">
          <BugViperFullLogo width={140} height={40} />
        </div>
      </div>

      {/* Stats */}
      {loading ? (
        <div className="flex gap-4 flex-wrap">
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-24 flex-1 min-w-[160px] rounded-xl" />
          ))}
        </div>
      ) : stats ? (
        <div className="flex gap-4 flex-wrap">
          <StatCard
            label="Repositories"
            value={stats.total_repos}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
              </svg>
            }
            color="var(--chart-1)"
          />
          <StatCard
            label="PRs Reviewed"
            value={stats.total_prs}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            }
            color="var(--chart-2)"
          />
          <StatCard
            label="Reviews Run"
            value={stats.total_reviews}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            }
            color="var(--chart-3)"
          />
          <StatCard
            label="Issues Raised"
            value={stats.total_issues_raised}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            }
            color="#dc2626"
          />
          <StatCard
            label="Issues Resolved"
            value={stats.total_issues_resolved}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
            color="#16a34a"
          />
          <StatCard
            label="Positives"
            value={stats.total_positives}
            icon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.828 14.828a4 4 0 01-5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
            color="var(--chart-2)"
          />
        </div>
      ) : null}

      {/* Repos */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Repositories</h2>
          <Button variant="outline" size="sm" asChild>
            <Link href="/repositories">Manage Repos</Link>
          </Button>
        </div>

        {loading ? (
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-48 w-full rounded-xl" />
            ))}
          </div>
        ) : repos.length === 0 ? (
          <Card className="py-16">
            <CardContent className="flex flex-col items-center justify-center gap-3 text-center">
              <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
                <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
                </svg>
              </div>
              <div>
                <p className="font-medium">No repositories indexed</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Add a repository to start tracking code reviews
                </p>
              </div>
              <Button asChild size="sm">
                <Link href="/repositories">Add Repository</Link>
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            {repos.map((repo) => (
              <RepoCard key={`${repo.owner}/${repo.repoName}`} repo={repo} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
