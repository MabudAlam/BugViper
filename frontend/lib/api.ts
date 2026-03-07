import { getAuth } from "@/lib/firebase";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Always fetches the freshest token directly from Firebase.
// Firebase caches the token and auto-refreshes it before expiry — no manual
// refresh needed. Returns null if no user is signed in.
async function getFirebaseToken(): Promise<string | null> {
  try {
    const auth = getAuth();
    if (!auth.currentUser) return null;
    return await auth.currentUser.getIdToken();
  } catch {
    return null;
  }
}

// Type definitions for repository statistics
export interface RepositoryStatistics {
  files: number;
  classes: number;
  functions: number;
  methods: number;
  lines: number;
  imports: number;
  languages: string[];
}

export interface RepositoryStatsResponse {
  repository_id: string;
  statistics: RepositoryStatistics;
}

export interface GitHubRepo {
  name: string;
  full_name: string;
  description: string | null;
  language: string | null;
  stargazers_count: number;
  private: boolean;
  default_branch: string;
  html_url: string;
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

// Repositories
export const listRepositories = () => apiFetch("/api/v1/repos/");
export const deleteRepository = (id: string) =>
  apiFetch(`/api/v1/repos/${id}`, { method: "DELETE" });
export const getRepositoryStats = (owner: string, repoName: string) =>
  apiFetch(`/api/v1/repos/${owner}/${repoName}/stats`);

// Ingestion
export interface IngestionJobResponse {
  job_id: string;
  status: string;
  message: string;
  poll_url: string;
}

export interface JobStatusResponse {
  job_id: string;
  owner: string;
  repo_name: string;
  branch: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  stats: {
    files_processed: number;
    files_skipped: number;
    classes_found: number;
    functions_found: number;
    imports_found: number;
    total_lines: number;
    errors: string[];
  } | null;
  error_message: string | null;
}

export const ingestGithub = (data: {
  owner: string;
  repo_name: string;
  branch?: string;
  clear_existing?: boolean;
}): Promise<IngestionJobResponse> =>
  apiFetch("/api/v1/ingest/github", { method: "POST", body: JSON.stringify(data) });

export const getIngestionJobStatus = (jobId: string): Promise<JobStatusResponse> =>
  apiFetch(`/api/v1/ingest/jobs/${jobId}`);

export const listIngestionJobs = (): Promise<JobStatusResponse[]> =>
  apiFetch("/api/v1/ingest/jobs");

export const ingestRepository = (data: {
  repo_url: string;
  username: string;
  repo_name: string;
  clear_existing?: boolean;
}) => apiFetch("/api/v1/ingest/repository", { method: "POST", body: JSON.stringify(data) });

// Query - Search (unified)
export const searchCode = (query: string, limit = 30) =>
  apiFetch(`/api/v1/query/search?query=${encodeURIComponent(query)}&limit=${limit}`);

// Query - Analysis
export const getMethodUsages = (name: string) =>
  apiFetch(`/api/v1/query/method-usages?method_name=${encodeURIComponent(name)}`);
export const findCallers = (name: string) =>
  apiFetch(`/api/v1/query/find_callers?symbol_name=${encodeURIComponent(name)}`);
export const getClassHierarchy = (name: string) =>
  apiFetch(`/api/v1/query/class_hierarchy?class_name=${encodeURIComponent(name)}`);
export const getChangeImpact = (name: string) =>
  apiFetch(`/api/v1/query/change_impact?symbol_name=${encodeURIComponent(name)}`);

// Query - CodeFinder
export const findFunction = (name: string) =>
  apiFetch(`/api/v1/query/code-finder/function?name=${encodeURIComponent(name)}`);
export const findClass = (name: string) =>
  apiFetch(`/api/v1/query/code-finder/class?name=${encodeURIComponent(name)}`);
export const findVariable = (name: string) =>
  apiFetch(`/api/v1/query/code-finder/variable?name=${encodeURIComponent(name)}`);
export const findContent = (query: string) =>
  apiFetch(`/api/v1/query/code-finder/content?query=${encodeURIComponent(query)}`);
export const findModule = (name: string) =>
  apiFetch(`/api/v1/query/code-finder/module?name=${encodeURIComponent(name)}`);
export const findImports = (name: string) =>
  apiFetch(`/api/v1/query/code-finder/imports?name=${encodeURIComponent(name)}`);
export const findByLine = (query: string) =>
  apiFetch(`/api/v1/query/code-finder/line?query=${encodeURIComponent(query)}`);
export const peekFileLines = (path: string, line: number, above: number, below: number) =>
  apiFetch(`/api/v1/query/code-finder/peek?path=${encodeURIComponent(path)}&line=${line}&above=${above}&below=${below}`);

// Query - Metrics
export const getGraphStats = () => apiFetch("/api/v1/query/stats");
export const getLanguageStats = () => apiFetch("/api/v1/query/language/stats");
export const getTopComplexFunctions = () =>
  apiFetch("/api/v1/query/code-finder/complexity/top");

// Query - Code Review / Diff Context
export const getSymbolsAtLines = (repoId: string, filePath: string, startLine: number, endLine: number) =>
  apiFetch(`/api/v1/query/symbols-at-lines-relative?repo_id=${encodeURIComponent(repoId)}&file_path=${encodeURIComponent(filePath)}&start_line=${startLine}&end_line=${endLine}`);

export const getDiffContext = (data: {
  repo_id: string;
  changes: { file_path: string; start_line?: number; end_line?: number }[];
}) => apiFetch("/api/v1/query/diff-context", { method: "POST", body: JSON.stringify(data) });

export const getFileSource = (repoId: string, filePath: string) =>
  apiFetch(`/api/v1/query/file-source?repo_id=${encodeURIComponent(repoId)}&file_path=${encodeURIComponent(filePath)}`);

// Semantic search
export interface SemanticHit {
  name?: string;
  type: string;
  path?: string;
  line_number?: number;
  source_code?: string;
  docstring?: string;
  score: number;
}
export interface SemanticSearchResponse {
  results: SemanticHit[];
  total: number;
}
export const askCode = (data: {
  question: string;
  repoName?: string;
  repoOwner?: string;
}): Promise<SemanticSearchResponse> =>
  apiFetch("/api/v1/query/semantic", { method: "POST", body: JSON.stringify(data) });

// Auth
export const loginUser = (data: { github_access_token: string }) =>
  apiFetch("/api/v1/auth/login", { method: "POST", body: JSON.stringify(data) });

export const ensureUser = () =>
  apiFetch("/api/v1/auth/ensure", { method: "POST" });

export const getCurrentUser = () => apiFetch("/api/v1/auth/me");

export const getGitHubRepos = (): Promise<GitHubRepo[]> =>
  apiFetch("/api/v1/auth/github/repos");

// Additional query functions
export const getComplexity = (functionName: string, path?: string) => {
  const params = new URLSearchParams({ function_name: functionName });
  if (path) params.set("path", path);
  return apiFetch(`/api/v1/query/code-finder/complexity?${params}`);
};

export const findRelatedCode = (query: string, fuzzy = false, depth = 2) =>
  apiFetch(
    `/api/v1/query/code-finder/content?query=${encodeURIComponent(query)}&fuzzy=${fuzzy}&depth=${depth}`
  );

export const getLanguageSymbols = (language: string, symbolType: string, limit = 50) =>
  apiFetch(
    `/api/v1/query/language/symbols?language=${encodeURIComponent(language)}&symbol_type=${encodeURIComponent(symbolType)}&limit=${limit}`
  );

export const findDefinition = (symbolName: string, _repoId?: string) =>
  apiFetch(`/api/v1/query/search?query=${encodeURIComponent(symbolName)}`);

export const findMethodUsages = (methodName: string) =>
  apiFetch(`/api/v1/query/method-usages?method_name=${encodeURIComponent(methodName)}`);

export const getFileStructure = (repoId: string, filePath: string) =>
  apiFetch(
    `/api/v1/query/file-source?repo_id=${encodeURIComponent(repoId)}&file_path=${encodeURIComponent(filePath)}`
  );

export const analyzeRelationships = (queryType: string, target: string, context?: string) => {
  const params = new URLSearchParams({ symbol_name: target });
  if (context) params.set("context", context);
  const endpoint =
    queryType === "class_hierarchy"
      ? `/api/v1/query/class_hierarchy?class_name=${encodeURIComponent(target)}`
      : queryType === "change_impact"
      ? `/api/v1/query/change_impact?symbol_name=${encodeURIComponent(target)}`
      : `/api/v1/query/find_callers?symbol_name=${encodeURIComponent(target)}&query_type=${encodeURIComponent(queryType)}`;
  return apiFetch(endpoint);
};

// Default export for direct api.X() usage in components
const api = {
  findMethodUsages,
  getFileStructure,
  analyzeRelationships,
  findDefinition,
  getComplexity,
  findRelatedCode,
  getLanguageSymbols,
};

export default api;
