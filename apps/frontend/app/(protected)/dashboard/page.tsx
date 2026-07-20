"use client";

import { Info, TrendingUp } from "lucide-react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import {
  getRepoOverview,
  getDashboardAnalytics,
  type DashboardStats,
  type RepoAnalyticsSummary,
} from "@/lib/api";
import { useEffect, useMemo, useState } from "react";

const REPO_COLORS = [
  "#FFF3E0",
  "#FFE0B2",
  "#FFCC80",
  "#FFB74D",
  "#FFA726",
  "#FF9800",
  "#FB8C00",
  "#F57C00",
  "#EF6C00",
  "#E65100",
];

function repoColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++)
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return REPO_COLORS[Math.abs(hash) % REPO_COLORS.length];
}

function fmt(n: number | string | undefined | null) {
  if (n == null) return "0";
  if (typeof n === "string") return n;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toString();
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

const STATS_CONFIG: { label: string; icon: string; getValue: (s: DashboardStats) => number | string | undefined | null }[] = [
  { label: "Repositories", icon: "repo", getValue: (s) => s.total_repos },
  { label: "PRs Reviewed", icon: "pr", getValue: (s) => s.total_prs },
  { label: "Reviews Run", icon: "review", getValue: (s) => s.total_reviews },
  { label: "Bugs Caught", icon: "issue", getValue: (s) => s.total_issues_raised },
  { label: "Addressed Rate", icon: "resolved", getValue: (s) => `${Math.round(s.addressed_rate * 100)}%` },
  { label: "PRs / Week", icon: "pr", getValue: (s) => s.prs_per_week },
  { label: "Avg Merge Time", icon: "clock", getValue: (s) => {
    const h = s.avg_merge_time_hours;
    if (!h || h <= 0) return "—";
    if (h < 1 / 60) return `${Math.round(h * 3600)}s`;
    if (h < 1) return `${Math.round(h * 60)}m`;
    return `${h.toFixed(1)}h`;
  }},
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
    case "clock":
      return (
        <svg className={cls} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      );
    default:
      return null;
  }
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [repos, setRepos] = useState<RepoAnalyticsSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getRepoOverview(),
      getDashboardAnalytics(),
    ])
      .then(([overview, analytics]) => {
        setStats(overview.stats);
        setRepos(analytics.repos ?? []);
      })
      .catch(() => {
        setStats(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const { reviewData, prData, reviewTotal, prTotal, chartConfig } = useMemo(() => {
    if (!repos.length) return { reviewData: [], prData: [], reviewTotal: [], prTotal: [], chartConfig: {} as ChartConfig };

    const config: ChartConfig = {};
    const revMap: Record<string, Record<string, number>> = {};
    const prMap: Record<string, Record<string, number>> = {};
    let allDates = new Set<string>();

    repos.forEach((r) => {
      const color = repoColor(r.repoName);
      config[r.repoName] = { label: r.repoName, color };
      (r.dailyBreakdown || []).forEach((d) => {
        allDates.add(d.date);
        if (!revMap[d.date]) revMap[d.date] = {};
        if (!prMap[d.date]) prMap[d.date] = {};
        revMap[d.date][r.repoName] = (revMap[d.date][r.repoName] || 0) + (d.reviews || 0);
        prMap[d.date][r.repoName] = (prMap[d.date][r.repoName] || 0) + (d.prsReviewed || 0);
      });
    });

    const sorted = [...allDates].sort();
    const reviewData = sorted.map((date) => ({ date, ...revMap[date] }));
    const prData = sorted.map((date) => ({ date, ...prMap[date] }));
    const reviewTotal = sorted.map((date) => {
      const vals = Object.values(revMap[date] || {});
      return { date, reviews: vals.reduce((s, v) => s + v, 0) };
    });
    const prTotal = sorted.map((date) => {
      const vals = Object.values(prMap[date] || {});
      return { date, prs: vals.reduce((s, v) => s + v, 0) };
    });
    return { reviewData, prData, reviewTotal, prTotal, chartConfig: config satisfies ChartConfig };
  }, [repos]);

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {[1, 2, 3, 4, 5, 6, 7].map((i) => (
            <Skeleton key={i} className="h-24 rounded-base" />
          ))}
        </div>
      ) : stats ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
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

          <Card className="bg-secondary-background text-foreground">
            <CardHeader className="relative">
              <CardTitle>Total Reviews per Day</CardTitle>
              <CardDescription>Daily review count broken down by repository for the last 30 days</CardDescription>
              <div className="absolute right-4 top-4 group">
                <Info className="h-4 w-4 text-muted-foreground cursor-pointer" />
                <div className="absolute right-0 top-6 w-64 p-3 rounded-base bg-popover text-popover-foreground border border-border shadow-md text-xs opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50">
                  Each bar represents a single day. Colored segments show how many reviews each repository had that day. Stacked bars show the total across all repos.
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig}>
                <BarChart accessibilityLayer data={reviewData}>
                  <CartesianGrid vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickLine={false}
                    tickMargin={10}
                    axisLine={false}
                    tickFormatter={(v) => v.slice(5)}
                  />
                  <YAxis tickLine={false} axisLine={false} tickMargin={8} />
                  <ChartTooltip cursor={{ fill: "#8080804D" }} content={<ChartTooltipContent hideLabel />} />
                  <ChartLegend content={<ChartLegendContent />} />
                  {repos.map((r) => (
                    <Bar
                      key={r.repoName}
                      dataKey={r.repoName}
                      stackId="a"
                      fill={`var(--color-${r.repoName})`}
                      radius={4}
                    />
                  ))}
                </BarChart>
              </ChartContainer>
            </CardContent>
            <CardFooter className="flex-col items-start gap-2 text-sm">
              <div className="flex gap-2 leading-none font-medium">
                <TrendingUp className="h-4 w-4" /> {repos.length} repos tracked
              </div>
              <div className="text-muted-foreground leading-none">
                Total reviews across all repositories per day
              </div>
            </CardFooter>
          </Card>

          <Card className="bg-secondary-background text-foreground">
            <CardHeader className="relative">
              <CardTitle>PRs Reviewed per Day</CardTitle>
              <CardDescription>Daily PR review count broken down by repository for the last 30 days</CardDescription>
              <div className="absolute right-4 top-4 group">
                <Info className="h-4 w-4 text-muted-foreground cursor-pointer" />
                <div className="absolute right-0 top-6 w-64 p-3 rounded-base bg-popover text-popover-foreground border border-border shadow-md text-xs opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50">
                  Each bar represents a single day. Colored segments show how many unique PRs each repository had reviewed that day. Stacked bars show the total PRs reviewed across all repos.
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig}>
                <BarChart accessibilityLayer data={prData}>
                  <CartesianGrid vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickLine={false}
                    tickMargin={10}
                    axisLine={false}
                    tickFormatter={(v) => v.slice(5)}
                  />
                  <YAxis tickLine={false} axisLine={false} tickMargin={8} />
                  <ChartTooltip cursor={{ fill: "#8080804D" }} content={<ChartTooltipContent hideLabel />} />
                  <ChartLegend content={<ChartLegendContent />} />
                  {repos.map((r) => (
                    <Bar
                      key={r.repoName}
                      dataKey={r.repoName}
                      stackId="a"
                      fill={`var(--color-${r.repoName})`}
                      radius={4}
                    />
                  ))}
                </BarChart>
              </ChartContainer>
            </CardContent>
            <CardFooter className="flex-col items-start gap-2 text-sm">
              <div className="flex gap-2 leading-none font-medium">
                <TrendingUp className="h-4 w-4" /> {repos.length} repos tracked
              </div>
              <div className="text-muted-foreground leading-none">
                Total PRs reviewed across all repositories per day
              </div>
            </CardFooter>
          </Card>

          <div className="space-y-6">
            <h3 className="text-lg font-semibold text-foreground">Repository Comparison</h3>

            {[
              { label: "Avg Merge Time (h)", dataKey: "avgMergeTimeHours" as const, desc: "Average time from PR creation to merge per repository" },
              { label: "Addressed Rate", dataKey: "addressedRate" as const, desc: "Ratio of issues resolved vs total issues raised per repository" },
            ].map(({ label, dataKey, desc }) => (
              <Card key={dataKey} className="bg-secondary-background text-foreground">
                <CardHeader className="relative">
                  <CardTitle>{label}</CardTitle>
                  <CardDescription>{desc}</CardDescription>
                  <div className="absolute right-4 top-4 group">
                    <Info className="h-4 w-4 text-muted-foreground cursor-pointer" />
                    <div className="absolute right-0 top-6 w-64 p-3 rounded-base bg-popover text-popover-foreground border border-border shadow-md text-xs opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50">
                      {dataKey === "avgMergeTimeHours" ? "Average hours from PR creation to merge. For merged PRs uses actual merge time; for open PRs uses last review time as an estimate." : "Percentage of issues that have been resolved out of all issues generated by BugViper reviews for this repository."}
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <ChartContainer config={chartConfig}>
                    <BarChart accessibilityLayer data={repos.map((r) => ({ repo: r.repoName, value: Number(r[dataKey]) || 0 }))}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="repo" tickLine={false} tickMargin={10} axisLine={false} />
                      <YAxis tickLine={false} axisLine={false} tickMargin={8} />
                      <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
                      <Bar dataKey="value" fill="var(--main)" radius={4} />
                    </BarChart>
                  </ChartContainer>
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      ) : (
        <div className="text-center py-20 text-muted-foreground">Failed to load stats.</div>
      )}
    </div>
  );
}
