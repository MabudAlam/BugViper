"use client";

import { Skeleton } from "@/components/ui/skeleton";
import {
  getRepoOverview,
  getDashboardAnalytics,
  type DashboardStats,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

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

function AnalyticsCharts({ dailyData }: { dailyData: { date: string; caught: number; resolved: number; reviews: number }[] }) {
  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-foreground">Analytics</h3>
      {dailyData.length === 0 ? (
        <p className="text-sm text-muted-foreground">No analytics data yet. Run a review to see charts.</p>
      ) : (
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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <BarChart
              data={dailyData}
              labelKey="date"
              valueKey="reviews"
              color="var(--main)"
              label="Reviews per Day"
              valueLabel="reviews"
            />
            <BarChart
              data={dailyData}
              labelKey="date"
              valueKey="caught"
              color="var(--main)"
              label="Bugs Caught per Day"
              valueLabel="bugs"
            />
          </div>
        </>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [dailyData, setDailyData] = useState<{ date: string; caught: number; resolved: number; reviews: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getRepoOverview(),
      getDashboardAnalytics(),
    ])
      .then(([overview, analytics]) => {
        setStats(overview.stats);
        setDailyData(analytics.dailyBreakdown ?? []);
      })
      .catch(() => {
        setStats(null);
      })
      .finally(() => setLoading(false));
  }, []);

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

          <AnalyticsCharts dailyData={dailyData} />
        </>
      ) : (
        <div className="text-center py-20 text-muted-foreground">Failed to load stats.</div>
      )}
    </div>
  );
}
