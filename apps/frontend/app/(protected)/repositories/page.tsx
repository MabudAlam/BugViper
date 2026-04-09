"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  listRepositories,
  deleteRepository,
  ingestGithub,
  getIngestionJobStatus,
  getGitHubRepos,
  embedRepository,
  type GitHubRepo,
} from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

interface FirestoreRepo {
  owner: string;
  repoName: string;
  fullName: string;
  description?: string | null;
  language?: string | null;
  stars?: number;
  forks?: number;
  private?: boolean;
  defaultBranch?: string;
  branch?: string;
  size?: number;
  topics?: string[];
  ingestionStatus?: string;
  filesProcessed?: number;
  filesSkipped?: number;
  classesFound?: number;
  functionsFound?: number;
  importsFound?: number;
  totalLines?: number;
  ingestedAt?: string;
  createdAt?: string;
  updatedAt?: string;
}

interface IngestingJob {
  jobId: string;
  status: string;
  repo: GitHubRepo;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt(n?: number) {
  if (n === undefined || n === null) return null;
  return n.toLocaleString();
}

function timeAgo(iso?: string) {
  if (!iso) return null;
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

// ── Status badge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status?: string }) {
  const s = status ?? "";
  if (["pending", "dispatched", "running"].includes(s)) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
        <svg className="w-2.5 h-2.5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
        </svg>
        Syncing
      </span>
    );
  }
  if (s === "ingested" || s === "completed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
        <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        Indexed
      </span>
    );
  }
  if (s === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-destructive/10 text-destructive border border-destructive/20">
        <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
        Failed
      </span>
    );
  }
  return null;
}

// ── Language dot colour ────────────────────────────────────────────────────────

const LANG_COLOURS: Record<string, string> = {
  Go: "#00ADD8", Python: "#3572A5", TypeScript: "#3178c6", JavaScript: "#f1e05a",
  Rust: "#dea584", Java: "#b07219", "C++": "#f34b7d", C: "#555555",
  Ruby: "#701516", Swift: "#F05138", Kotlin: "#A97BFF", PHP: "#4F5D95",
  Scala: "#c22d40", Shell: "#89e051", Dart: "#00B4AB", HTML: "#e34c26",
  CSS: "#563d7c",
};

function LangDot({ lang }: { lang?: string | null }) {
  if (!lang) return null;
  const colour = LANG_COLOURS[lang] ?? "#8b949e";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: colour }} />
      {lang}
    </span>
  );
}

// ── Repo card ──────────────────────────────────────────────────────────────────

function RepoCard({
  repo,
  liveStatus,
  onDelete,
  onReEmbed,
  isEmbedding,
}: {
  repo: FirestoreRepo;
  liveStatus?: string;
  onDelete: () => void;
  onReEmbed: () => void;
  isEmbedding: boolean;
}) {
  const status = liveStatus ?? repo.ingestionStatus;
  const key = `${repo.owner}/${repo.repoName}`;

  const stats = [
    { label: "files",     value: fmt(repo.filesProcessed) },
    { label: "functions", value: fmt(repo.functionsFound) },
    { label: "classes",   value: fmt(repo.classesFound) },
    { label: "imports",   value: fmt(repo.importsFound) },
  ].filter((s) => s.value !== null);

  return (
    <div className="group rounded-xl border bg-card hover:border-primary/30 transition-colors duration-150 overflow-hidden">
      {/* Top bar */}
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Repo name row */}
          <div className="flex items-center gap-2 flex-wrap">
            <svg className="w-4 h-4 text-muted-foreground shrink-0" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
            </svg>
            <a
              href={`https://github.com/${key}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-sm hover:text-primary transition-colors truncate"
            >
              <span className="text-muted-foreground font-normal">{repo.owner}/</span>{repo.repoName}
            </a>
            {repo.private && (
              <span className="shrink-0 text-xs border rounded px-1.5 py-0.5 text-muted-foreground">
                Private
              </span>
            )}
            <StatusBadge status={status} />
          </div>

          {/* Description */}
          {repo.description && (
            <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
              {repo.description}
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150 shrink-0">
          {/* Re-embed button */}
          <button
            onClick={onReEmbed}
            disabled={isEmbedding}
            aria-label={`Re-embed ${key}`}
            title="Re-run semantic embeddings"
            className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg
              className={`w-4 h-4 ${isEmbedding ? "animate-spin" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>

          {/* Delete button */}
          <button
            onClick={onDelete}
            aria-label={`Delete ${key}`}
            className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      </div>

      {/* Stats row */}
      {stats.length > 0 && (
        <div className="px-5 pb-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {stats.map((s) => (
              <span key={s.label} className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{s.value}</span> {s.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center gap-3 px-5 py-2.5 border-t bg-muted/30 flex-wrap">
        <LangDot lang={repo.language} />

        {(repo.stars ?? 0) > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
            </svg>
            {repo.stars}
          </span>
        )}

        {(repo.forks ?? 0) > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <circle cx="12" cy="18" r="2" /><circle cx="6" cy="6" r="2" /><circle cx="18" cy="6" r="2" />
              <path strokeLinecap="round" d="M6 8v2a4 4 0 004 4h4a4 4 0 004-4V8" />
            </svg>
            {repo.forks}
          </span>
        )}

        {repo.topics && repo.topics.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {repo.topics.slice(0, 4).map((t) => (
              <span key={t} className="px-1.5 py-0.5 rounded-full text-xs bg-primary/10 text-primary/80 border border-primary/10">
                {t}
              </span>
            ))}
            {repo.topics.length > 4 && (
              <span className="px-1.5 py-0.5 rounded-full text-xs text-muted-foreground">
                +{repo.topics.length - 4}
              </span>
            )}
          </div>
        )}

        {repo.ingestedAt && (
          <span className="ml-auto text-xs text-muted-foreground shrink-0">
            Indexed {timeAgo(repo.ingestedAt)}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Pending card (optimistic, not yet in Firestore) ────────────────────────────

function PendingCard({ job }: { job: IngestingJob }) {
  return (
    <div className="rounded-xl border bg-card overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-4">
        <svg className="w-4 h-4 text-muted-foreground shrink-0" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
        </svg>
        <div className="flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm">{job.repo.full_name}</span>
            <StatusBadge status={job.status} />
          </div>
          {job.repo.language && <LangDot lang={job.repo.language} />}
        </div>
      </div>
      <div className="h-1 bg-muted overflow-hidden">
        <div className="h-full bg-primary/60 animate-pulse w-2/3 rounded-full" />
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function RepositoriesPage() {
  const [repositories, setRepositories] = useState<FirestoreRepo[]>([]);
  const [isLoadingRepos, setIsLoadingRepos] = useState(true);

  // GitHub picker
  const [showPicker, setShowPicker] = useState(false);
  const [githubRepos, setGithubRepos] = useState<GitHubRepo[]>([]);
  const [loadingGithubRepos, setLoadingGithubRepos] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");
  const [startingRepo, setStartingRepo] = useState<string | null>(null);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Re-embed
  const [embeddingRepo, setEmbeddingRepo] = useState<string | null>(null);

  // Active ingestion jobs (keyed by full_name e.g. "Pavel401/JobsScraper")
  const [ingestingJobs, setIngestingJobs] = useState<Record<string, IngestingJob>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const ingestingJobsRef = useRef(ingestingJobs);
  useEffect(() => { ingestingJobsRef.current = ingestingJobs; }, [ingestingJobs]);

  // ── Data loading ─────────────────────────────────────────────────────────────

  async function loadRepositories() {
    setIsLoadingRepos(true);
    try {
      const data = await listRepositories();
      const list: FirestoreRepo[] = Array.isArray(data) ? data : data?.repositories ?? [];
      setRepositories(list);
    } catch {
      toast.error("Failed to load repositories");
    } finally {
      setIsLoadingRepos(false);
    }
  }

  useEffect(() => { loadRepositories(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Job polling ───────────────────────────────────────────────────────────────

  const activeJobIds = useMemo(
    () =>
      Object.values(ingestingJobs)
        .filter((j) => !["completed", "failed"].includes(j.status))
        .map((j) => j.jobId)
        .sort()
        .join(","),
    [ingestingJobs],
  );

  useEffect(() => {
    if (!activeJobIds) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    async function poll() {
      const jobs = Object.values(ingestingJobsRef.current).filter(
        (j) => !["completed", "failed"].includes(j.status)
      );
      for (const job of jobs) {
        try {
          const res = await getIngestionJobStatus(job.jobId);
          const next = res.status;

          setIngestingJobs((prev) => {
            if (!prev[job.repo.full_name]) return prev;
            return { ...prev, [job.repo.full_name]: { ...prev[job.repo.full_name], status: next } };
          });

          if (next === "completed") {
            toast.success(`${job.repo.full_name} indexed successfully`);
            loadRepositories();
            setTimeout(() => {
              setIngestingJobs((prev) => {
                const copy = { ...prev };
                delete copy[job.repo.full_name];
                return copy;
              });
            }, 3000);
          } else if (next === "failed") {
            toast.error(`Indexing failed for ${job.repo.full_name}`);
          }
        } catch {
          // silent
        }
      }
    }

    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeJobIds]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Picker ────────────────────────────────────────────────────────────────────

  async function openPicker() {
    setShowPicker(true);
    setPickerSearch("");
    if (githubRepos.length > 0) return;
    setLoadingGithubRepos(true);
    try {
      setGithubRepos(await getGitHubRepos());
    } catch {
      toast.error("Failed to load GitHub repositories");
    } finally {
      setLoadingGithubRepos(false);
    }
  }

  async function handleStart(repo: GitHubRepo) {
    setStartingRepo(repo.full_name);
    try {
      const [owner, repoName] = repo.full_name.split("/");
      const res = await ingestGithub({ owner, repo_name: repoName, branch: repo.default_branch });
      setShowPicker(false);
      setIngestingJobs((prev) => ({
        ...prev,
        [repo.full_name]: { jobId: res.job_id, status: res.status, repo },
      }));
    } catch (err) {
      toast.error(`Failed to start: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setStartingRepo(null);
    }
  }

  async function handleReEmbed(owner: string, repoName: string) {
    const key = `${owner}/${repoName}`;
    setEmbeddingRepo(key);
    try {
      const res = await embedRepository(owner, repoName);
      const count = res.nodes_embedded ?? 0;
      toast.success(
        count > 0
          ? `Embedded ${count} nodes for ${key}`
          : `All nodes already had embeddings for ${key}`
      );
    } catch (err) {
      toast.error(`Re-embed failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setEmbeddingRepo(null);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteRepository(deleteTarget);
      toast.success("Repository deleted");
      setDeleteTarget(null);
      loadRepositories();
    } catch {
      toast.error("Failed to delete repository");
    } finally {
      setIsDeleting(false);
    }
  }

  // ── Derived ───────────────────────────────────────────────────────────────────

  const existingKeys = new Set(repositories.map((r) => r.fullName ?? `${r.owner}/${r.repoName}`));
  const pendingCards = Object.values(ingestingJobs).filter(
    (j) => !existingKeys.has(j.repo.full_name)
  );
  const filteredRepos = githubRepos.filter(
    (r) =>
      r.full_name.toLowerCase().includes(pickerSearch.toLowerCase()) ||
      (r.description ?? "").toLowerCase().includes(pickerSearch.toLowerCase())
  );

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="container py-8 space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Repositories</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {repositories.length + pendingCards.length}{" "}
            {repositories.length + pendingCards.length === 1 ? "repository" : "repositories"} indexed
          </p>
        </div>
        <Button onClick={openPicker} size="sm" className="gap-1.5">
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Add Repository
        </Button>
      </div>

      {/* Repository list */}
      <div className="space-y-3">
        {isLoadingRepos ? (
          <>
            <Skeleton className="h-32 w-full rounded-xl" />
            <Skeleton className="h-32 w-full rounded-xl" />
            <Skeleton className="h-32 w-full rounded-xl" />
          </>
        ) : repositories.length === 0 && pendingCards.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground border rounded-xl border-dashed gap-3">
            <svg className="w-10 h-10 opacity-30" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7v10M8 7l4-2 4 2" />
            </svg>
            <div className="text-center">
              <p className="font-medium text-foreground">No repositories yet</p>
              <p className="text-sm mt-1">Click <strong>Add Repository</strong> to index your first repo</p>
            </div>
          </div>
        ) : (
          <>
            {pendingCards.map((job) => (
              <PendingCard key={job.repo.full_name} job={job} />
            ))}
            {repositories.map((repo, i) => {
              const key = repo.fullName ?? `${repo.owner}/${repo.repoName}`;
              const job = ingestingJobs[key];
              return (
                <div key={key} className={`animate-fade-in-up stagger-${Math.min(i + 1, 8)}`}>
                  <RepoCard
                    repo={repo}
                    liveStatus={job?.status}
                    onDelete={() => setDeleteTarget(`${repo.owner}/${repo.repoName}`)}
                    onReEmbed={() => handleReEmbed(repo.owner, repo.repoName)}
                    isEmbedding={embeddingRepo === key}
                  />
                </div>
              );
            })}
          </>
        )}
      </div>

      {/* Delete confirmation dialog */}
      <Dialog.Root
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open && !isDeleting) setDeleteTarget(null); }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 mx-4 bg-background border rounded-xl shadow-2xl focus:outline-none"
            onInteractOutside={(e) => { if (isDeleting) e.preventDefault(); }}
          >
            <div className="flex items-start gap-3 px-6 pt-6 pb-4">
              <div className="flex items-center justify-center w-9 h-9 rounded-full bg-destructive/10 shrink-0 mt-0.5">
                <svg className="w-4 h-4 text-destructive" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </div>
              <div className="space-y-1">
                <Dialog.Title className="font-semibold text-sm">Delete repository</Dialog.Title>
                <Dialog.Description className="text-sm text-muted-foreground">
                  Remove{" "}
                  <span className="font-medium text-foreground">{deleteTarget}</span>{" "}
                  from the graph? All indexed data will be permanently deleted.
                </Dialog.Description>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 px-6 py-4 border-t">
              <Dialog.Close asChild>
                <button
                  disabled={isDeleting}
                  className="px-3 py-1.5 text-sm rounded-md border hover:bg-accent transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                onClick={confirmDelete}
                disabled={isDeleting}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-destructive text-white hover:bg-destructive/90 transition-colors disabled:opacity-50"
              >
                {isDeleting ? (
                  <>
                    <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                    </svg>
                    Deleting…
                  </>
                ) : "Delete"}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* GitHub Repo Picker */}
      {showPicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setShowPicker(false)}
          />
          <div className="relative z-10 w-full max-w-md mx-4 bg-background border rounded-xl shadow-2xl flex flex-col max-h-[75vh]">
            <div className="flex items-center justify-between px-5 py-4 border-b">
              <h2 className="font-semibold">Add Repository</h2>
              <button
                onClick={() => setShowPicker(false)}
                className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded-md hover:bg-accent"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-4 py-3 border-b">
              <Input
                placeholder="Search repositories..."
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                autoFocus
                className="h-8 text-sm"
              />
            </div>

            <div className="flex-1 overflow-y-auto py-1">
              {loadingGithubRepos ? (
                <div className="space-y-1 p-3">
                  {[1, 2, 3, 4, 5].map((n) => <Skeleton key={n} className="h-14 w-full rounded-lg" />)}
                </div>
              ) : filteredRepos.length === 0 ? (
                <div className="text-center py-10 text-sm text-muted-foreground">
                  {pickerSearch ? "No matching repositories" : "No repositories found"}
                </div>
              ) : (
                filteredRepos.map((repo) => {
                  const isStarting = startingRepo === repo.full_name;
                  const alreadyIngesting = repo.full_name in ingestingJobs;
                  const alreadyIngested = existingKeys.has(repo.full_name);
                  const disabled = isStarting || alreadyIngesting || alreadyIngested;

                  return (
                    <div
                      key={repo.full_name}
                      className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm truncate">{repo.full_name}</span>
                          {repo.private && (
                            <span className="text-xs text-muted-foreground border rounded px-1 shrink-0">Private</span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <LangDot lang={repo.language} />
                          {repo.stargazers_count > 0 && (
                            <span className="text-xs text-muted-foreground">★ {repo.stargazers_count}</span>
                          )}
                          {repo.description && (
                            <span className="text-xs text-muted-foreground truncate">{repo.description}</span>
                          )}
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant={alreadyIngested ? "outline" : "default"}
                        disabled={disabled}
                        onClick={() => handleStart(repo)}
                        className="shrink-0 h-7 text-xs px-3"
                      >
                        {isStarting ? (
                          <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                          </svg>
                        ) : alreadyIngested ? "Indexed" : alreadyIngesting ? "Indexing…" : "Index"}
                      </Button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
