import { getAuth } from "@/lib/firebase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function getFirebaseToken(): Promise<string | null> {
  try {
    const auth = getAuth();
    if (!auth.currentUser) return null;
    return await auth.currentUser.getIdToken();
  } catch {
    return null;
  }
}

async function apiFetch(path: string, options?: RequestInit) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string>),
  };

  const token = await getFirebaseToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `API error: ${res.status}`);
  }
  return res.json();
}

export interface DashboardStats {
  total_repos: number;
  total_prs: number;
  total_reviews: number;
  total_issues_raised: number;
  total_issues_resolved: number;
  total_positives: number;
  prs_per_week: number;
  addressed_rate: number;
  avg_merge_time_hours: number;
}

export interface RepoOverview {
  stats: DashboardStats;
  repos: RepoSummary[];
}

export const getRepoOverview = (): Promise<RepoOverview> =>
  apiFetch("/api/v1/repos/overview");

export interface RepoSummary {
  owner: string;
  repoName: string;
  fullName: string;
  description: string | null;
  language: string | null;
  stars: number;
  forks: number;
  private: boolean;
  defaultBranch: string;
  topics: string[];
  filesProcessed: number | null;
  ingestedAt: string | null;
  openIssueCount: number;
  totalIssuesRaised: number;
  reviewCount: number;
}

export interface PRReviewSummary {
  owner: string;
  repo: string;
  prNumber: number;
  repoId: string;
  reviewStatus: string | null;
  reviewCount: number;
  openIssueCount: number;
  totalIssuesRaised: number;
  totalPositives: number;
  lastReviewType: string | null;
  lastReviewedAt: string | null;
  lastReviewedSha: string | null;
  createdAt: string | null;
}

export interface RepoAnalyticsRun {
  runNumber: number;
  issues: number;
  resolved: number;
}

export interface RepoAnalyticsPR {
  owner: string;
  repo: string;
  prNumber: number;
  repoId: string;
  reviewStatus: string | null;
  reviewCount: number;
  openIssueCount: number;
  totalIssuesRaised: number;
  totalPositives: number;
  lastReviewType: string | null;
  lastReviewedAt: string | null;
  lastReviewedSha: string | null;
  createdAt: string | null;
  mergedAt: string | null;
  closedAt: string | null;
  runs: RepoAnalyticsRun[];
}

export interface RepoAnalyticsDetail {
  owner: string;
  repoName: string;
  totalPrs: number;
  totalReviews: number;
  totalIssuesGenerated: number;
  totalIssuesResolved: number;
  totalPositives: number;
  prsPerWeek: number;
  addressedRate: number;
  avgMergeTimeHours: number;
  dailyBreakdown: { date: string; caught: number; resolved: number; reviews: number }[];
  prs: RepoAnalyticsPR[];
}

export const getRepoAnalytics = (owner: string, repo: string): Promise<RepoAnalyticsDetail> =>
  apiFetch(`/api/v1/repos/${owner}/${repo}/analytics`);

export const getDashboardAnalytics = (): Promise<{ dailyBreakdown: { date: string; caught: number; resolved: number; reviews: number }[] }> =>
  apiFetch("/api/v1/repos/dashboard/analytics");

export const listRepoPRs = (owner: string, repo: string): Promise<PRReviewSummary[]> =>
  apiFetch(`/api/v1/repos/${owner}/${repo}/prs`);

export interface ReviewRunSummary {
  runNumber: number;
  issuesCount: number;
  positivesCount: number;
  walkthroughCount: number;
  summary: string;
  filesChanged: string[];
  reviewType: string;
  startedAt: string | null;
  endedAt: string | null;
  durationSeconds: number | null;
}

export const listPRReviews = (owner: string, repo: string, prNumber: number, limit?: number): Promise<ReviewRunSummary[]> => {
  const params = limit ? `?limit=${limit}` : "";
  return apiFetch(`/api/v1/repos/${owner}/${repo}/prs/${prNumber}/reviews${params}`);
};

export interface ReviewIssue {
  file?: string;
  line_start?: number;
  line_end?: number;
  severity?: string;
  title?: string;
  description?: string;
  suggestion?: string;
  category?: string;
  status?: string;
  impact?: string;
  issue_type?: string;
  confidence?: number;
  code_snippet?: string;
}

export interface ReviewComment {
  comment_id?: number;
  thread_id?: number;
  file?: string;
  line?: number;
  title?: string;
  status?: string;
  resolvedAt?: string;
  githubResolved?: boolean;
}

export interface ReviewRunDetail {
  runNumber: number;
  issues: ReviewIssue[];
  positiveFindings: string[];
  summary: string;
  filesChanged: string[];
  reviewType: string;
  issuesCount: number;
  positivesCount: number;
  walkthroughCount: number;
  headSha: string | null;
  baseSha: string | null;
  startedAt: string | null;
  endedAt: string | null;
  durationSeconds: number | null;
  createdAt: string | null;
  githubCommentIds: ReviewComment[];
}

export const getPRReviewRun = (owner: string, repo: string, prNumber: number, runNumber: number): Promise<ReviewRunDetail> =>
  apiFetch(`/api/v1/repos/${owner}/${repo}/prs/${prNumber}/reviews/${runNumber}`);

export const loginUser = (data: { github_access_token: string }) =>
  apiFetch("/api/v1/auth/login", { method: "POST", body: JSON.stringify(data) });

export const ensureUser = () => apiFetch("/api/v1/auth/ensure", { method: "POST" });

export interface SupportQueryPayload {
  name: string;
  email: string;
  subject: string;
  category: string;
  message: string;
  priority?: string;
}

export interface SupportQueryResult {
  query_id: string;
  message: string;
}

export interface InstallationStatus {
  installationId: number | null;
  githubUsername: string | null;
  linked: boolean;
  settingsUrl: string | null;
}

export const getInstallationStatus = (): Promise<InstallationStatus> =>
  apiFetch("/api/v1/auth/installation");

export interface LinterToolConfig {
  name: string;
  enabled: boolean;
  url: string;
  extensions: string[];
  configFiles: string[];
  configFile: string;
}

export interface ToolsConfig {
  ruff: LinterToolConfig;
  eslint: LinterToolConfig;
  golangciLint: LinterToolConfig;
}

export const getToolsConfig = (): Promise<ToolsConfig> =>
  apiFetch("/api/v1/tools/config");

export const saveToolsConfig = (config: ToolsConfig): Promise<ToolsConfig> =>
  apiFetch("/api/v1/tools/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });

export const submitSupportQuery = (data: SupportQueryPayload): Promise<SupportQueryResult> =>
  fetch(`${API_BASE}/api/v1/support/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `API error: ${res.status}`);
    }
    return res.json();
  });
