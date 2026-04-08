"use client";

import { useState, useRef, useCallback, useEffect, type ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { CodeBlock } from "@/components/ui/code-block";
import { toast } from "sonner";
import {
  Search, GitBranch, Code2, BarChart2, GitPullRequest,
  X, Loader2, Sparkles, ChevronDown,
} from "lucide-react";
import * as api from "@/lib/api";

// ─── Types ────────────────────────────────────────────────────────────────────

interface SearchHit {
  name?: string;
  type?: string;
  path?: string;
  line_number?: number;
  match_line?: string;
  score?: number;
  source_code?: string | null;
  docstring?: string | null;
}

interface PeekLine {
  line_number: number;
  content: string;
  is_anchor: boolean;
}

interface PeekResult {
  path: string;
  anchor_line: number;
  window: PeekLine[];
  total_lines: number;
}

// ─── PeekViewer ───────────────────────────────────────────────────────────────

function PeekViewer({ path, anchorLine }: { path: string; anchorLine: number }) {
  const STEP = 20;
  const [above, setAbove] = useState(10);
  const [below, setBelow] = useState(10);
  const [result, setResult] = useState<PeekResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [opened, setOpened] = useState(false);

  const load = useCallback(async (a: number, b: number) => {
    setLoading(true);
    try {
      const data = await api.peekFileLines(path, anchorLine, a, b);
      setResult(data as PeekResult);
      setAbove(a);
      setBelow(b);
    } catch {
      toast.error("Failed to load context");
    } finally {
      setLoading(false);
    }
  }, [path, anchorLine]);

  if (!opened) {
    return (
      <button
        className="text-xs text-primary underline-offset-2 hover:underline mt-1"
        onClick={() => { setOpened(true); load(above, below); }}
      >
        Show context
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex flex-wrap gap-2 items-center text-xs text-muted-foreground">
        <button
          className="hover:text-foreground disabled:opacity-40"
          disabled={loading}
          onClick={() => load(above + STEP, below)}
        >
          ↑ {STEP} more above
        </button>
        <span>·</span>
        <button
          className="hover:text-foreground disabled:opacity-40"
          disabled={loading}
          onClick={() => load(above, below + STEP)}
        >
          {STEP} more below ↓
        </button>
        <span>·</span>
        <button className="hover:text-foreground" onClick={() => setOpened(false)}>
          collapse
        </button>
        {result && (
          <span className="ml-auto">
            lines {result.window[0]?.line_number}–{result.window[result.window.length - 1]?.line_number}
            {" "}/ {result.total_lines}
          </span>
        )}
      </div>

      {loading && (
        <div className="py-4 flex items-center justify-center gap-2 text-muted-foreground text-xs">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>Loading…</span>
        </div>
      )}

      {result && (
        <div className="rounded border border-border overflow-x-auto text-xs font-mono">
          {result.window.map((ln) => (
            <div
              key={ln.line_number}
              className={`flex ${ln.is_anchor
                ? "bg-yellow-400/15 dark:bg-yellow-500/15 border-l-2 border-yellow-500"
                : "hover:bg-muted/40"
              }`}
            >
              <span className="select-none w-12 text-right px-2 py-0.5 text-muted-foreground/60 shrink-0 border-r border-border">
                {ln.line_number}
              </span>
              <span className="px-3 py-0.5 whitespace-pre">{ln.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Type badge colours ───────────────────────────────────────────────────────

const TYPE_STYLES: Record<string, string> = {
  function: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  class:    "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
  variable: "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20",
  line:     "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
};

function TypeBadge({ type }: { type: string }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium border ${TYPE_STYLES[type] ?? "bg-muted text-muted-foreground border-border"}`}>
      {type}
    </span>
  );
}

// ─── Search Result Card ───────────────────────────────────────────────────────

function SearchHitCard({ hit }: { hit: SearchHit }) {
  const filename = hit.path ? hit.path.split("/").pop() : null;
  const dirPath  = hit.path && filename ? hit.path.slice(0, hit.path.length - filename.length - 1) : null;
  const hasLine  = hit.line_number != null && hit.line_number > 0;
  const hasSource = Boolean(hit.source_code);
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="group rounded-lg border border-border bg-card px-4 py-3 space-y-1.5 hover:border-primary/40 transition-colors">
      {/* Header */}
      <div className="flex items-start gap-2">
        {hit.type && <TypeBadge type={hit.type} />}
        <span className="font-mono font-semibold text-sm leading-tight break-all">
          {hit.name ?? hit.match_line?.trim()}
        </span>
        {hit.score != null && (
          <span className="ml-auto shrink-0 text-xs text-muted-foreground/50">
            {hit.score.toFixed(2)}
          </span>
        )}
      </div>

      {/* Docstring */}
      {hit.docstring && (
        <p className="text-xs text-muted-foreground italic line-clamp-2">{hit.docstring}</p>
      )}

      {/* Path */}
      {hit.path && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground font-mono">
          {dirPath && <span className="opacity-60 truncate">{dirPath}/</span>}
          {filename && <span className="text-foreground/80 font-medium">{filename}</span>}
          {hasLine && (
            <span className="shrink-0 px-1 rounded bg-muted text-foreground/70">:{hit.line_number}</span>
          )}
        </div>
      )}

      {/* Match snippet */}
      {hit.type === "line" && hit.name && (
        <pre className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1 overflow-x-auto">{hit.name}</pre>
      )}

      {/* Source code */}
      {hasSource && (
        <div className="space-y-1.5">
          <button
            className="text-xs text-primary underline-offset-2 hover:underline"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "▲ hide code" : "▼ show code"}
          </button>
          {expanded && (
            <CodeBlock code={hit.source_code!} startLine={hit.line_number ?? 1} maxHeight="max-h-72" />
          )}
        </div>
      )}

      {/* Peek fallback when no stored source */}
      {!hasSource && hit.path && hasLine && (
        <PeekViewer path={hit.path} anchorLine={hit.line_number!} />
      )}
    </div>
  );
}

// ─── Unified Search Results ───────────────────────────────────────────────────

function SearchResultsView({ data }: { data: unknown }) {
  let hits: SearchHit[] = [];
  if (Array.isArray(data)) {
    hits = data as SearchHit[];
  } else if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const inner = obj.results ?? obj.symbols ?? obj.ranked_results ?? [];
    if (Array.isArray(inner)) hits = inner as SearchHit[];
  }

  if (hits.length === 0) {
    return (
      <div className="py-12 text-center text-muted-foreground text-sm">
        No results found.
      </div>
    );
  }

  const symbols = hits.filter((h) => h.type !== "line");
  const lines   = hits.filter((h) => h.type === "line");

  return (
    <div className="space-y-4">
      <div className="flex gap-2 text-xs text-muted-foreground">
        <span>{hits.length} results</span>
        {symbols.length > 0 && <span>· {symbols.length} symbols</span>}
        {lines.length > 0   && <span>· {lines.length} file matches</span>}
      </div>

      {symbols.length > 0 && (
        <section className="space-y-2">
          {symbols.length > 0 && lines.length > 0 && (
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Symbols</h3>
          )}
          {symbols.map((hit, i) => <SearchHitCard key={i} hit={hit} />)}
        </section>
      )}

      {lines.length > 0 && (
        <section className="space-y-2">
          {symbols.length > 0 && lines.length > 0 && (
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">File matches</h3>
          )}
          {lines.map((hit, i) => <SearchHitCard key={i} hit={hit} />)}
        </section>
      )}
    </div>
  );
}

// ─── Search Tab ───────────────────────────────────────────────────────────────

function SearchTab({ repoOwner, repoName }: { repoOwner: string; repoName: string }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<unknown>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const filter = repoOwner && repoName ? { repoOwner, repoName } : undefined;

  const runSearch = async (q = query) => {
    const term = q.trim();
    if (!term) { inputRef.current?.focus(); return; }
    setLoading(true);
    setResults(null);
    try {
      const data = await api.searchCode(term, 30, filter);
      setResults(data);
    } catch (e) {
      toast.error(`Search failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4 pt-4">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            ref={inputRef}
            placeholder="Search by name, snippet, or code pattern — e.g. GitHubRepo or class LoginRequest"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
            className="text-sm pr-8"
            autoFocus
          />
          {query && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => { setQuery(""); setResults(null); inputRef.current?.focus(); }}
              aria-label="Clear search"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <Button onClick={() => runSearch()} disabled={loading} className="shrink-0">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
      </div>

      {results === null && !loading && (
        <p className="text-xs text-muted-foreground">
          Searches symbols (functions, classes, variables) and file content. Supports identifiers, code snippets, and keywords.
        </p>
      )}

      {loading && (
        <div className="py-10 flex items-center justify-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Searching…</span>
        </div>
      )}

      {!loading && results !== null && <SearchResultsView data={results} />}
    </div>
  );
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

/** Node properties may store source under either key depending on ingestion path */
function getNodeSource(item: Record<string, unknown>): string | null {
  return (item.source_code as string) || (item.source as string) || null;
}

function shortPath(p: string | null | undefined): { dir: string | null; file: string } {
  if (!p) return { dir: null, file: "—" };
  const parts = p.split("/");
  const file = parts[parts.length - 1];
  const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : null;
  return { dir, file };
}

// ─── Analysis-specific result renderers ───────────────────────────────────────

interface CallerEntry {
  caller: string;
  type: string;
  file: string;
  line: number | null;
  source?: "call_graph" | "text_reference";
  source_code?: string | null;
}

interface FunctionDefinition {
  name: string;
  symbol_type: string;
  file_path: string | null;
  relative_path: string | null;
  class_name: string | null;
  line_number: number | null;
  end_line_number: number | null;
  docstring: string | null;
  complexity: number | null;
  source_code?: string | null;
}

interface FindCallersData {
  callers: CallerEntry[];
  symbol: string;
  total: number;
  definitions?: FunctionDefinition[];
  fallback_used?: boolean;
}

function DefinitionCard({ def }: { def: FunctionDefinition }) {
  const { dir, file } = shortPath(def.file_path);
  const defSource = def.source_code ?? getNodeSource(def as unknown as Record<string, unknown>);
  return (
    <div className="rounded-lg border border-border bg-muted/20 overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 border border-blue-500/20">
            {def.symbol_type?.toLowerCase() ?? "fn"}
          </span>
          {def.class_name && (
            <>
              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-500 border border-purple-500/20">
                {def.class_name}
              </span>
              <span className="text-muted-foreground text-xs">·</span>
            </>
          )}
          <span className="font-mono font-bold text-sm">{def.name}</span>
          {def.complexity != null && def.complexity > 1 && (
            <span className="ml-auto text-xs text-muted-foreground">
              complexity{" "}
              <span className={def.complexity >= 11 ? "text-orange-500 font-semibold" : "text-foreground"}>
                {def.complexity}
              </span>
            </span>
          )}
        </div>
        {def.docstring && (
          <p className="text-xs text-muted-foreground italic leading-relaxed">{def.docstring}</p>
        )}
        <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground">
          {dir && <span className="opacity-60 truncate">{dir}/</span>}
          <span className="text-foreground/80 font-medium">{file}</span>
          {def.line_number != null && (
            <span className="px-1 rounded bg-muted text-foreground/70">:{def.line_number}</span>
          )}
          {def.line_number != null && def.end_line_number != null && (
            <span className="text-muted-foreground/60">–{def.end_line_number}</span>
          )}
        </div>
      </div>
      {/* Source code */}
      {defSource ? (
        <CodeBlock code={defSource} startLine={def.line_number ?? 1} maxHeight="max-h-72" />
      ) : def.file_path && def.line_number != null ? (
        <div className="px-4 pb-3">
          <PeekViewer path={def.file_path} anchorLine={def.line_number} />
        </div>
      ) : null}
    </div>
  );
}

function FindCallersView({ data }: { data: FindCallersData }) {
  if (!data?.callers) return <ResultsView data={data} />;

  const defs    = data.definitions ?? [];
  const callers = data.callers;
  const hasDefs = defs.length > 0;

  return (
    <div className="space-y-4">
      {/* Definition(s) */}
      {hasDefs && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Definition</p>
          {defs.map((d, i) => <DefinitionCard key={i} def={d} />)}
        </div>
      )}

      {/* Separator */}
      {hasDefs && <div className="border-t border-border" />}

      {/* Callers */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {callers.length === 0 ? "Callers" : `${callers.length} Caller${callers.length !== 1 ? "s" : ""}`}
          </p>
          {data.fallback_used && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/20">
              text references — no call-graph edges
            </span>
          )}
        </div>

        {callers.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">
            {hasDefs
              ? "No callers found. The function may not have been called yet, or call edges weren't ingested."
              : `Symbol "${data.symbol}" not found in the graph.`}
          </p>
        ) : (
          callers.map((c, i) => {
            const { dir: cDir, file: cFile } = shortPath(c.file);
            const callerSource = c.source_code ?? getNodeSource(c as unknown as Record<string, unknown>);
            return (
              <div key={i} className="rounded-lg border border-border hover:border-primary/30 transition-colors overflow-hidden">
                {/* Caller header */}
                <div className="flex items-start gap-3 px-3 py-2.5">
                  <span className="shrink-0 text-xs font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground capitalize">
                    {c.type?.toLowerCase() ?? "fn"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-sm font-medium">{c.caller}</p>
                    <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground mt-0.5">
                      {cDir && <span className="opacity-60 truncate">{cDir}/</span>}
                      <span className="text-foreground/70 font-medium shrink-0">{cFile}</span>
                      {c.line && <span className="px-1 rounded bg-muted text-foreground/60 shrink-0">:{c.line}</span>}
                    </div>
                  </div>
                  {c.source === "text_reference" && (
                    <span className="shrink-0 self-center text-xs text-muted-foreground/60">ref</span>
                  )}
                </div>
                {/* Source: stored code or peek fallback */}
                {callerSource ? (
                  <CodeBlock code={callerSource} startLine={c.line ?? 1} maxHeight="max-h-56" />
                ) : c.file && c.line ? (
                  <div className="px-3 pb-2">
                    <PeekViewer path={c.file} anchorLine={c.line} />
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

interface MethodUsageEntry {
  method: Record<string, unknown> | null;
  file: string | null;
  callers: { caller: Record<string, unknown>; line: number | null; file: string | null }[];
}

function MethodUsagesView({ data }: { data: { usages: MethodUsageEntry[] } }) {
  if (!data?.usages) return <ResultsView data={data} />;
  if (data.usages.length === 0) {
    return <p className="text-sm text-muted-foreground py-6 text-center">No usages found.</p>;
  }
  return (
    <div className="space-y-6">
      {data.usages.map((u, i) => {
        const m = u.method ?? {};
        const methodName = (m.name as string) ?? "—";
        const methodSource = getNodeSource(m);
        const methodDocstring = m.docstring as string | undefined;
        const methodLine = m.line_number as number | undefined;
        const { dir, file } = shortPath(u.file);

        return (
          <div key={i} className="space-y-3">
            {/* Method definition card */}
            <div className="rounded-lg border border-primary/30 bg-primary/5 overflow-hidden">
              <div className="px-4 py-3 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 border border-blue-500/20 font-medium">fn</span>
                  <span className="font-mono font-bold text-sm">{methodName}</span>
                  {u.file && (
                    <span className="ml-auto text-xs font-mono text-muted-foreground">
                      {dir && <span className="opacity-60">{dir}/</span>}
                      <span className="text-foreground/80 font-medium">{file}</span>
                      {methodLine && <span className="px-1 rounded bg-muted ml-0.5">:{methodLine}</span>}
                    </span>
                  )}
                </div>
                {methodDocstring && (
                  <p className="text-xs text-muted-foreground italic">{methodDocstring}</p>
                )}
              </div>
              {methodSource && (
                <CodeBlock code={methodSource} startLine={methodLine ?? 1} maxHeight="max-h-72" />
              )}
            </div>

            {/* Callers */}
            <div className="space-y-2 pl-4 border-l-2 border-border">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {u.callers.length === 0 ? "No callers found" : `${u.callers.length} caller${u.callers.length !== 1 ? "s" : ""}`}
              </p>
              {u.callers.map((c, j) => {
                const callerNode = (typeof c.caller === "object" && c.caller !== null)
                  ? c.caller as Record<string, unknown>
                  : {};
                const callerName = (callerNode.name as string) ?? String(c.caller ?? "—");
                const callerSource = getNodeSource(callerNode);
                const callerDocstring = callerNode.docstring as string | undefined;
                const callerLine = c.line ?? (callerNode.line_number as number | undefined);
                const { dir: cDir, file: cFile } = shortPath(c.file);
                return (
                  <div key={j} className="rounded-lg border border-border overflow-hidden hover:border-primary/30 transition-colors">
                    <div className="flex items-start gap-3 px-3 py-2.5">
                      <span className="shrink-0 text-xs font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground">fn</span>
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-sm font-medium">{callerName}</p>
                        {callerDocstring && (
                          <p className="text-xs text-muted-foreground italic truncate">{callerDocstring}</p>
                        )}
                        <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground mt-0.5">
                          {cDir && <span className="opacity-60 truncate">{cDir}/</span>}
                          <span className="text-foreground/70 font-medium shrink-0">{cFile}</span>
                          {callerLine && <span className="px-1 rounded bg-muted text-foreground/60 shrink-0">:{callerLine}</span>}
                        </div>
                      </div>
                    </div>
                    {callerSource && (
                      <CodeBlock code={callerSource} startLine={callerLine ?? 1} maxHeight="max-h-56" />
                    )}
                    {!callerSource && c.file && callerLine && (
                      <div className="px-3 pb-2">
                        <PeekViewer path={c.file} anchorLine={callerLine} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── CodeFinder result renderer ───────────────────────────────────────────────

interface CodeFinderItem {
  name?: string;
  path?: string;
  line_number?: number;
  source?: string | null;
  source_code?: string | null;
  docstring?: string | null;
  is_dependency?: boolean;
  type?: string;
  // variable-specific
  value?: string | null;
  context?: string | null;
  // import-specific
  alias?: string | null;
  imported_name?: string | null;
  module_name?: string | null;
  // line-search-specific
  match_line?: string | null;
  // module-specific
  lang?: string | null;
}

function CodeFinderItem({ item, itemType }: { item: CodeFinderItem; itemType?: string }) {
  const [expanded, setExpanded] = useState(false);
  const source = getNodeSource(item as Record<string, unknown>);
  const hasSource = Boolean(source);
  const { dir, file } = shortPath(item.path);
  const displayType = item.type ?? itemType ?? null;

  // Import entries: show structured import info instead of code
  const isImport = !!(item.alias || item.imported_name || item.module_name);
  if (isImport) {
    return (
      <div className="rounded-lg border border-border bg-card px-4 py-3 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-500 border border-cyan-500/20 font-medium">import</span>
          <span className="font-mono font-semibold text-sm">
            {item.imported_name ?? item.alias ?? item.module_name ?? "—"}
          </span>
          {item.alias && item.alias !== item.imported_name && (
            <span className="text-xs text-muted-foreground font-mono">as {item.alias}</span>
          )}
          {item.module_name && (
            <span className="text-xs text-muted-foreground font-mono ml-auto">from {item.module_name}</span>
          )}
        </div>
        {item.path && (
          <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground">
            {dir && <span className="opacity-60 truncate">{dir}/</span>}
            <span className="text-foreground/80 font-medium">{file}</span>
            {item.line_number && <span className="px-1 rounded bg-muted text-foreground/70">:{item.line_number}</span>}
          </div>
        )}
      </div>
    );
  }

  // Line-search entries: show the matching line + PeekViewer
  if (item.match_line != null) {
    return (
      <div className="rounded-lg border border-border bg-card px-4 py-3 space-y-1.5">
        <pre className="text-xs text-foreground font-mono whitespace-pre-wrap break-all bg-muted/40 rounded px-2 py-1.5">
          {item.match_line.trim()}
        </pre>
        {item.path && (
          <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground">
            {dir && <span className="opacity-60 truncate">{dir}/</span>}
            <span className="text-foreground/80 font-medium">{file}</span>
            {item.line_number && <span className="px-1 rounded bg-muted text-foreground/70">:{item.line_number}</span>}
          </div>
        )}
        {item.path && item.line_number && (
          <PeekViewer path={item.path} anchorLine={item.line_number} />
        )}
      </div>
    );
  }

  // Module entries (no source or path)
  if (!item.path && item.name) {
    return (
      <div className="rounded-lg border border-border bg-card px-4 py-3 flex items-center gap-2">
        <span className="text-xs px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-500 border border-orange-500/20 font-medium">module</span>
        <span className="font-mono text-sm font-medium">{item.name}</span>
        {item.lang && <span className="ml-auto text-xs text-muted-foreground">{item.lang}</span>}
      </div>
    );
  }

  // Standard function/class/variable entry
  const typeStyle: Record<string, string> = {
    function: "bg-blue-500/10 text-blue-500 border-blue-500/20",
    class:    "bg-purple-500/10 text-purple-500 border-purple-500/20",
    variable: "bg-green-500/10 text-green-500 border-green-500/20",
  };

  return (
    <div className={`rounded-lg border overflow-hidden transition-colors ${hasSource ? "hover:border-primary/30" : ""} border-border bg-card`}>
      {/* Header */}
      <button
        className="w-full text-left px-4 py-3 space-y-1"
        onClick={() => hasSource && setExpanded((v) => !v)}
        disabled={!hasSource}
      >
        <div className="flex items-center gap-2 flex-wrap">
          {displayType && (
            <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${typeStyle[displayType] ?? "bg-muted text-muted-foreground border-border"}`}>
              {displayType}
            </span>
          )}
          <span className="font-mono font-semibold text-sm">{item.name ?? "—"}</span>
          {item.value && (
            <span className="text-xs text-muted-foreground font-mono truncate max-w-xs">= {item.value}</span>
          )}
          {item.is_dependency && (
            <span className="text-xs text-muted-foreground/60 ml-auto">dep</span>
          )}
          {hasSource && (
            <span className="text-xs text-muted-foreground shrink-0 ml-auto">{expanded ? "▲ hide" : "▼ code"}</span>
          )}
        </div>
        {item.docstring && (
          <p className="text-xs text-muted-foreground italic leading-relaxed line-clamp-2">{item.docstring}</p>
        )}
        {item.context && (
          <p className="text-xs text-muted-foreground font-mono">{item.context}</p>
        )}
        {item.path && (
          <div className="flex items-center gap-1 text-xs font-mono text-muted-foreground">
            {dir && <span className="opacity-60 truncate">{dir}/</span>}
            <span className="text-foreground/80 font-medium">{file}</span>
            {item.line_number && <span className="px-1 rounded bg-muted text-foreground/70">:{item.line_number}</span>}
          </div>
        )}
      </button>
      {/* Source code — shown when expanded, or always if small */}
      {hasSource && expanded && (
        <CodeBlock code={source!} startLine={item.line_number ?? 1} maxHeight="max-h-96" />
      )}
      {/* Peek fallback when there's no stored source */}
      {!hasSource && item.path && item.line_number && (
        <div className="px-4 pb-3">
          <PeekViewer path={item.path} anchorLine={item.line_number} />
        </div>
      )}
    </div>
  );
}

function CodeFinderResultsView({ data }: { data: unknown }) {
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const results = Array.isArray(obj.results) ? obj.results as CodeFinderItem[] : null;
  if (!results) return <ResultsView data={data} />;

  const total = (obj.total as number) ?? results.length;
  const queryKey = obj.function_name ?? obj.class_name ?? obj.variable_name ??
    obj.search_query ?? obj.module_name ?? obj.import_name ?? "";
  const itemType = obj.function_name ? "function"
    : obj.class_name ? "class"
    : obj.variable_name ? "variable"
    : undefined;

  // Auto-expand source for first 3 small results
  if (results.length === 0) {
    return <p className="text-sm text-muted-foreground py-6 text-center">No results found.</p>;
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        {total} result{total !== 1 ? "s" : ""}
        {queryKey ? ` for "${queryKey}"` : ""}
      </p>
      {results.map((item, i) => (
        <CodeFinderItem key={i} item={item} itemType={itemType} />
      ))}
    </div>
  );
}

function ChangeImpactView({ data }: { data: { symbol: string; callers: CallerEntry[]; definitions?: FunctionDefinition[]; impact_level: string; usages: unknown } }) {
  if (!data?.symbol) return <ResultsView data={data} />;
  const impactStyles: Record<string, string> = {
    high:   "text-red-500 bg-red-500/10 border-red-500/20",
    medium: "text-yellow-500 bg-yellow-500/10 border-yellow-500/20",
    low:    "text-green-500 bg-green-500/10 border-green-500/20",
  };
  const impactClass = impactStyles[data.impact_level] ?? "text-muted-foreground bg-muted border-border";
  const callers = data.callers ?? [];
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-mono font-semibold">{data.symbol}</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full border capitalize ${impactClass}`}>
          {data.impact_level ?? "unknown"} impact
        </span>
      </div>
      <FindCallersView data={{
        callers,
        symbol: data.symbol,
        total: callers.length,
        definitions: data.definitions,
      }} />
    </div>
  );
}

// ─── Class Hierarchy view ─────────────────────────────────────────────────────

interface HierarchyNode {
  name: string;
  file_path: string | null;
  line_number: number | null;
  source_code?: string | null;
  docstring?: string | null;
}

function ClassHierarchyView({ data }: { data: unknown }) {
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  if (!obj.class_name && !obj.found) return <ResultsView data={data} />;

  if (obj.found === false) {
    return (
      <p className="text-sm text-muted-foreground py-6 text-center">
        Class <span className="font-mono text-foreground">{String(obj.class_name)}</span> not found in the graph.
      </p>
    );
  }

  const ancestors   = (obj.ancestors   as HierarchyNode[]) ?? [];
  const descendants = (obj.descendants as HierarchyNode[]) ?? [];
  const filename    = (obj.file_path as string)?.split("/").pop() ?? null;
  const selfNode: HierarchyNode = {
    name: String(obj.class_name),
    file_path: (obj.file_path as string) ?? null,
    line_number: (obj.line_number as number) ?? null,
    source_code: (obj.source_code as string) ?? null,
    docstring: (obj.docstring as string) ?? null,
  };

  return (
    <div className="space-y-5">
      {/* Self */}
      <div className="rounded-lg border border-primary/40 bg-primary/5 overflow-hidden">
        <div className="px-4 py-3 space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-xs px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-500 border border-purple-500/20 font-medium">class</span>
            <span className="font-mono font-bold">{String(obj.class_name)}</span>
            {filename && (
              <span className="ml-auto text-xs font-mono text-muted-foreground">
                {filename}{selfNode.line_number ? `:${selfNode.line_number}` : ""}
              </span>
            )}
          </div>
          {selfNode.docstring && (
            <p className="text-xs text-muted-foreground italic">{selfNode.docstring}</p>
          )}
        </div>
        {selfNode.source_code ? (
          <CodeBlock code={selfNode.source_code} startLine={selfNode.line_number ?? 1} maxHeight="max-h-72" />
        ) : selfNode.file_path && selfNode.line_number != null ? (
          <div className="px-4 pb-3">
            <PeekViewer path={selfNode.file_path} anchorLine={selfNode.line_number} />
          </div>
        ) : null}
      </div>

      {/* Ancestors (what this class inherits from) */}
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Inherits from ({ancestors.length})
        </p>
        {ancestors.length === 0 ? (
          <p className="text-xs text-muted-foreground">No parent classes.</p>
        ) : (
          ancestors.map((a, i) => <HierarchyRow key={i} node={a} direction="up" />)
        )}
      </div>

      {/* Descendants (subclasses) */}
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Subclasses ({descendants.length})
        </p>
        {descendants.length === 0 ? (
          <p className="text-xs text-muted-foreground">No known subclasses.</p>
        ) : (
          descendants.map((d, i) => <HierarchyRow key={i} node={d} direction="down" />)
        )}
      </div>
    </div>
  );
}

function HierarchyRow({ node, direction }: { node: HierarchyNode; direction: "up" | "down" }) {
  const [expanded, setExpanded] = useState(false);
  const filename  = node.file_path?.split("/").pop() ?? null;
  const hasCode   = Boolean(node.source_code);
  const hasPeek   = !hasCode && Boolean(node.file_path && node.line_number != null);
  const canExpand = hasCode || hasPeek;

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-muted/30 transition-colors text-sm text-left"
        onClick={() => canExpand && setExpanded((v) => !v)}
        disabled={!canExpand}
      >
        <span className="text-muted-foreground text-xs shrink-0">{direction === "up" ? "↑" : "↓"}</span>
        <span className="text-xs px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-500 border border-purple-500/20 font-medium shrink-0">class</span>
        <span className="font-mono font-medium">{node.name ?? "—"}</span>
        {node.docstring && (
          <span className="text-xs text-muted-foreground truncate hidden sm:block ml-2">{node.docstring}</span>
        )}
        {filename && (
          <span className="ml-auto text-xs font-mono text-muted-foreground shrink-0">
            {filename}{node.line_number ? `:${node.line_number}` : ""}
          </span>
        )}
        {canExpand && (
          <span className="text-xs text-muted-foreground shrink-0 ml-1">{expanded ? "▲" : "▼"}</span>
        )}
      </button>
      {expanded && (
        hasCode ? (
          <CodeBlock code={node.source_code!} startLine={node.line_number ?? 1} maxHeight="max-h-72" />
        ) : hasPeek ? (
          <div className="px-3 pb-3">
            <PeekViewer path={node.file_path!} anchorLine={node.line_number!} />
          </div>
        ) : null
      )}
    </div>
  );
}

// ─── Metrics-specific result renderers ────────────────────────────────────────

function GraphStatsView({ data }: { data: unknown }) {
  const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const obj = (raw.global_metrics ?? raw) as Record<string, unknown>;
  const statDefs = [
    { key: "functions",    label: "Functions",    color: "text-blue-500" },
    { key: "classes",      label: "Classes",      color: "text-purple-500" },
    { key: "files",        label: "Files",        color: "text-orange-500" },
    { key: "variables",    label: "Variables",    color: "text-green-500" },
    { key: "imports",      label: "Imports",      color: "text-cyan-500" },
    { key: "repositories", label: "Repositories", color: "text-pink-500" },
  ].filter((s) => obj[s.key] != null);
  if (statDefs.length === 0) return <ResultsView data={data} />;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
      {statDefs.map((s) => (
        <div key={s.key} className="rounded-xl border border-border bg-muted/20 p-4 text-center space-y-1">
          <p className={`text-2xl font-bold tabular-nums ${s.color}`}>
            {Number(obj[s.key]).toLocaleString()}
          </p>
          <p className="text-xs text-muted-foreground">{s.label}</p>
        </div>
      ))}
    </div>
  );
}

function LanguageStatsView({ data }: { data: unknown }) {
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const langs = Array.isArray(obj.languages) ? obj.languages as Record<string, unknown>[] : null;
  if (!langs) return <ResultsView data={data} />;
  return (
    <div className="space-y-2">
      {obj.total_languages != null && (
        <p className="text-xs text-muted-foreground">{String(obj.total_languages)} languages indexed</p>
      )}
      <div className="rounded-lg border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40">
              <th className="text-left px-3 py-2 text-xs font-medium text-muted-foreground">Language</th>
              <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">Files</th>
              <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">Functions</th>
              <th className="text-right px-3 py-2 text-xs font-medium text-muted-foreground">Classes</th>
            </tr>
          </thead>
          <tbody>
            {langs.map((lang, i) => (
              <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/20 transition-colors">
                <td className="px-3 py-2 font-medium">{String(lang.language ?? "—")}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{String(lang.file_count ?? "—")}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{String(lang.function_count ?? "—")}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{String(lang.class_count ?? "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ComplexFunctionsView({ data }: { data: unknown }) {
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const results = Array.isArray(obj.results) ? obj.results as Record<string, unknown>[] : null;
  if (!results) return <ResultsView data={data} />;

  const getScore = (item: Record<string, unknown>): number => {
    if (typeof item.cyclomatic_complexity === "number") return item.cyclomatic_complexity;
    if (item.complexity && typeof item.complexity === "object")
      return ((item.complexity as Record<string, unknown>).cyclomatic_complexity as number) ?? 0;
    return 0;
  };
  const scoreColor = (n: number) => {
    if (n >= 20) return "text-red-500";
    if (n >= 11) return "text-orange-500";
    if (n >= 6)  return "text-yellow-500";
    return "text-green-500";
  };

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted-foreground pb-1">{results.length} functions ranked by cyclomatic complexity</p>
      {results.map((item, i) => {
        const score    = getScore(item);
        const name     = (item.name as string) ?? (item.function_name as string) ?? "—";
        const fullPath = (item.path as string) ?? "";
        const filename = fullPath.split("/").pop() ?? fullPath;
        return (
          <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border hover:border-primary/30 transition-colors">
            <span className="text-xs font-medium text-muted-foreground w-5 text-right shrink-0">{i + 1}</span>
            <div className="flex-1 min-w-0">
              <p className="font-mono text-sm font-medium truncate">{name}</p>
              <p className="text-xs text-muted-foreground font-mono truncate">{filename}</p>
            </div>
            <span className={`shrink-0 text-xl font-bold tabular-nums ${scoreColor(score)}`}>{score}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Generic query tab ────────────────────────────────────────────────────────

type QueryDef = {
  label: string;
  placeholder: string;
  fn: (q: string) => Promise<unknown>;
  needsInput: boolean;
  renderResults?: (data: unknown) => ReactNode;
};

function buildQueryGroups(filter: api.RepoFilter | undefined): Record<string, QueryDef[]> {
  return {
    Analysis: [
      {
        label: "Method Usages", placeholder: "Method name…",
        fn: (q) => api.getMethodUsages(q, filter), needsInput: true,
        renderResults: (d) => <MethodUsagesView data={d as { usages: MethodUsageEntry[] }} />,
      },
      {
        label: "Find Callers", placeholder: "Symbol name…",
        fn: (q) => api.findCallers(q, filter), needsInput: true,
        renderResults: (d) => <FindCallersView data={d as FindCallersData} />,
      },
      {
        label: "Class Hierarchy", placeholder: "Class name…",
        fn: (q) => api.getClassHierarchy(q, filter), needsInput: true,
        renderResults: (d) => <ClassHierarchyView data={d} />,
      },
      {
        label: "Change Impact", placeholder: "Symbol name…",
        fn: (q) => api.getChangeImpact(q, filter), needsInput: true,
        renderResults: (d) => <ChangeImpactView data={d as { symbol: string; callers: CallerEntry[]; definitions?: FunctionDefinition[]; impact_level: string; usages: unknown }} />,
      },
    ],
    CodeFinder: [
      { label: "Function",    placeholder: "Function name…",     fn: (q) => api.findFunction(q, filter),  needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Class",       placeholder: "Class name…",        fn: (q) => api.findClass(q, filter),     needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Variable",    placeholder: "Variable name…",     fn: (q) => api.findVariable(q, filter),  needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Content",     placeholder: "Content query…",     fn: (q) => api.findContent(q, filter),   needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Module",      placeholder: "Module name…",       fn: (q) => api.findModule(q),            needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Imports",     placeholder: "Import name…",       fn: (q) => api.findImports(q, filter),   needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
      { label: "Line Search", placeholder: "Code line to find…", fn: (q) => api.findByLine(q, filter),    needsInput: true,  renderResults: (d) => <CodeFinderResultsView data={d} /> },
    ],
    Metrics: [
      {
        label: "Graph Stats", placeholder: "",
        fn: (_q) => api.getGraphStats(), needsInput: false,
        renderResults: (d) => <GraphStatsView data={d} />,
      },
      {
        label: "Language Stats", placeholder: "",
        fn: (_q) => api.getLanguageStats(filter), needsInput: false,
        renderResults: (d) => <LanguageStatsView data={d} />,
      },
      {
        label: "Top Complex Functions", placeholder: "",
        fn: (_q) => api.getTopComplexFunctions(filter), needsInput: false,
        renderResults: (d) => <ComplexFunctionsView data={d} />,
      },
    ],
  };
}

function GenericQueryTab({ queries }: { queries: QueryDef[] }) {
  const [input, setInput]         = useState("");
  const [loading, setLoading]     = useState(false);
  const [results, setResults]     = useState<unknown>(null);
  const [activeQuery, setActiveQuery] = useState<QueryDef | null>(null);

  const run = async (q: QueryDef) => {
    if (q.needsInput && !input.trim()) { toast.error("Enter a query first"); return; }
    setLoading(true);
    setActiveQuery(q);
    setResults(null);
    try {
      setResults(await q.fn(input.trim()));
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  const hasInput  = queries.some((q) => q.needsInput);
  const activePlaceholder = activeQuery?.placeholder
    ?? queries.find((q) => q.needsInput)?.placeholder
    ?? "Enter name or query…";

  return (
    <div className="space-y-4 pt-4">
      {hasInput && (
        <Input
          placeholder={activePlaceholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && activeQuery) run(activeQuery); }}
          className="text-sm"
        />
      )}

      <div className="flex flex-wrap gap-2">
        {queries.map((q) => (
          <Button
            key={q.label}
            variant={activeQuery?.label === q.label ? "default" : "secondary"}
            size="sm"
            onClick={() => run(q)}
            disabled={loading}
          >
            {loading && activeQuery?.label === q.label
              ? <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" />{q.label}</>
              : q.label}
          </Button>
        ))}
      </div>

      {activeQuery && !loading && hasInput && (
        <p className="text-xs text-muted-foreground">
          Press{" "}
          <kbd className="px-1.5 py-0.5 rounded bg-muted text-xs font-mono border border-border">Enter</kbd>
          {" "}to re-run <span className="text-foreground font-medium">{activeQuery.label}</span>
        </p>
      )}

      {loading && (
        <div className="py-8 flex items-center justify-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Loading…</span>
        </div>
      )}

      {!loading && results !== null && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium text-muted-foreground">{activeQuery?.label}</CardTitle>
          </CardHeader>
          <CardContent>
            {activeQuery?.renderResults
              ? activeQuery.renderResults(results)
              : <ResultsView data={results} />
            }
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ─── Semantic Result Card ─────────────────────────────────────────────────────

function SemanticResultCard({ hit }: { hit: api.SemanticHit }) {
  const [expanded, setExpanded] = useState(true);
  const filename = hit.path ? hit.path.split("/").pop() : null;
  const dirPath  = hit.path && filename ? hit.path.slice(0, hit.path.length - filename.length - 1) : null;
  const hasLine  = hit.line_number != null && hit.line_number > 0;
  const pct      = Math.round(hit.score * 100);

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden hover:border-primary/40 transition-colors">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-4 py-3 cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        <TypeBadge type={hit.type} />
        <span className="font-mono font-semibold text-sm leading-tight break-all flex-1">
          {hit.name ?? filename ?? hit.type}
        </span>
        <span className="shrink-0 text-xs font-medium tabular-nums text-emerald-500/80">{pct}%</span>
        <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`} />
      </div>

      {/* Path */}
      {hit.path && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground font-mono px-4 pb-2 -mt-1">
          {dirPath && <span className="opacity-60 truncate">{dirPath}/</span>}
          {filename && <span className="text-foreground/80 font-medium">{filename}</span>}
          {hasLine && (
            <span className="shrink-0 px-1 rounded bg-muted text-foreground/70">:{hit.line_number}</span>
          )}
        </div>
      )}

      {/* Docstring */}
      {expanded && hit.docstring && (
        <p className="px-4 pb-2 text-xs text-muted-foreground italic">{hit.docstring}</p>
      )}

      {/* Source code */}
      {expanded && hit.source_code ? (
        <CodeBlock code={hit.source_code} startLine={hit.line_number ?? 1} maxHeight="max-h-72" />
      ) : expanded && hit.path && hasLine ? (
        <div className="px-4 pb-3">
          <PeekViewer path={hit.path} anchorLine={hit.line_number!} />
        </div>
      ) : null}
    </div>
  );
}

// ─── Semantic Search Tab ──────────────────────────────────────────────────────

function SemanticTab({ repoOwner, repoName }: { repoOwner: string; repoName: string }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<api.SemanticHit[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = async (q = query) => {
    const question = q.trim();
    if (!question) { inputRef.current?.focus(); return; }
    setLoading(true);
    setResults(null);
    try {
      const res = await api.askCode({
        question,
        repoOwner: repoOwner || undefined,
        repoName: repoName || undefined,
      });
      setResults(res.results);
    } catch (e) {
      toast.error(`Search failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4 pt-4">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            ref={inputRef}
            placeholder="Describe what you're looking for — e.g. authentication handler, database connection"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") run(); }}
            className="text-sm pr-8"
            autoFocus
          />
          {query && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => { setQuery(""); setResults(null); inputRef.current?.focus(); }}
              aria-label="Clear"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <Button onClick={() => run()} disabled={loading} className="shrink-0">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
      </div>

      {results === null && !loading && (
        <p className="text-xs text-muted-foreground">
          Searches by meaning, not keyword — uses vector embeddings to find semantically similar functions, classes, and files.
        </p>
      )}

      {loading && (
        <div className="py-10 flex items-center justify-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Searching…</span>
        </div>
      )}

      {!loading && results !== null && (
        results.length === 0 ? (
          <div className="py-12 text-center text-muted-foreground text-sm">No results found.</div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">{results.length} semantic matches</p>
            {results.map((r, i) => (
              <SemanticResultCard key={`${r.path}-${r.name}-${i}`} hit={r} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function QueryPage() {
  const [diffRepoId, setDiffRepoId]           = useState("");
  const [diffChangesText, setDiffChangesText] = useState("");
  const [diffLoading, setDiffLoading]         = useState(false);
  const [diffResults, setDiffResults]         = useState<unknown>(null);

  // Shared repo selector
  const [repos, setRepos] = useState<{ fullName?: string; owner?: string; repoName?: string }[]>([]);
  const [selectedRepo, setSelectedRepo] = useState("");

  useEffect(() => {
    api.listRepositories()
      .then((data: { repositories?: { fullName?: string; owner?: string; repoName?: string }[] }) =>
        setRepos(data?.repositories ?? []))
      .catch(() => {});
  }, []);

  const [repoOwner, repoName] = selectedRepo ? selectedRepo.split("/") : ["", ""];
  const filter: api.RepoFilter | undefined =
    repoOwner && repoName ? { repoOwner, repoName } : undefined;
  const queryGroups = buildQueryGroups(filter);

  const runDiffContext = async () => {
    if (!diffRepoId.trim()) { toast.error("Enter a repository ID"); return; }
    const lines = diffChangesText.trim().split("\n").filter(Boolean);
    const changes = lines.map((line) => {
      const [filePath, range] = line.split(":");
      if (range) {
        const [start, end] = range.split("-").map(Number);
        return { file_path: filePath.trim(), start_line: start || 1, end_line: end || 999999 };
      }
      return { file_path: filePath.trim() };
    });
    if (changes.length === 0) { toast.error("Enter at least one file change"); return; }
    setDiffLoading(true);
    setDiffResults(null);
    try {
      setDiffResults(await api.getDiffContext({ repo_id: diffRepoId.trim(), changes }));
    } catch (e) {
      toast.error(`Diff context failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setDiffLoading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Query</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Search code, explore relationships, and analyze your indexed repositories.
        </p>
      </div>

      {/* Shared repo selector */}
      <div className="flex items-center gap-3">
        <label className="text-sm text-muted-foreground shrink-0">Repository:</label>
        <div className="relative">
          <select
            value={selectedRepo}
            onChange={(e) => setSelectedRepo(e.target.value)}
            className="appearance-none rounded-lg border border-input bg-background pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
          >
            <option value="">All repositories</option>
            {repos.map((r) => {
              const label = r.fullName ?? (r.owner && r.repoName ? `${r.owner}/${r.repoName}` : null);
              if (!label) return null;
              return <option key={label} value={label}>{label}</option>;
            })}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
        </div>
      </div>

      <Tabs defaultValue="Ask">
        <TabsList>
          <TabsTrigger value="Ask" className="gap-1.5">
            <Sparkles className="w-3.5 h-3.5" />Ask
          </TabsTrigger>
          <TabsTrigger value="Search" className="gap-1.5">
            <Search className="w-3.5 h-3.5" />Search
          </TabsTrigger>
          <TabsTrigger value="Analysis" className="gap-1.5">
            <GitBranch className="w-3.5 h-3.5" />Analysis
          </TabsTrigger>
          <TabsTrigger value="CodeFinder" className="gap-1.5">
            <Code2 className="w-3.5 h-3.5" />CodeFinder
          </TabsTrigger>
          <TabsTrigger value="Metrics" className="gap-1.5">
            <BarChart2 className="w-3.5 h-3.5" />Metrics
          </TabsTrigger>
          <TabsTrigger value="DiffContext" className="gap-1.5">
            <GitPullRequest className="w-3.5 h-3.5" />Code Review
          </TabsTrigger>
        </TabsList>

        <TabsContent value="Ask">
          <SemanticTab repoOwner={repoOwner} repoName={repoName} />
        </TabsContent>

        <TabsContent value="Search">
          <SearchTab repoOwner={repoOwner} repoName={repoName} />
        </TabsContent>

        {Object.entries(queryGroups).map(([group, queries]) => (
          <TabsContent key={group} value={group}>
            <GenericQueryTab key={`${group}-${selectedRepo}`} queries={queries} />
          </TabsContent>
        ))}

        <TabsContent value="DiffContext" className="space-y-4 pt-4">
          <Card>
            <CardHeader>
              <CardTitle>Diff Context Builder</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Build RAG context for code review. Provide the repository and changed files
                to get affected symbols, callers, class hierarchy, and source.
              </p>
              <div className="space-y-2">
                <Label>Repository ID</Label>
                <Input
                  placeholder="owner/repo_name"
                  value={diffRepoId}
                  onChange={(e) => setDiffRepoId(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>Changed Files</Label>
                <Textarea
                  placeholder={"One per line — file_path:start_line-end_line\n\nExamples:\nsrc/main.py:10-30\nsrc/utils.py:1-50"}
                  value={diffChangesText}
                  onChange={(e) => setDiffChangesText(e.target.value)}
                  rows={6}
                  className="font-mono text-sm"
                />
              </div>
              <Button onClick={runDiffContext} disabled={diffLoading}>
                {diffLoading
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Loading…</>
                  : "Get Diff Context"}
              </Button>
            </CardContent>
          </Card>
          {diffResults !== null && <DiffContextResults data={diffResults as DiffContextData} />}
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ─── Diff Context Results ──────────────────────────────────────────────────────

interface DiffContextData {
  affected_symbols: {
    type: string; name: string; start_line: number; end_line: number;
    source: string | null; docstring: string | null; change_file: string;
  }[];
  callers: {
    symbol: string; symbol_type: string;
    callers: { type: string; name: string; path: string; source: string | null }[];
  }[];
  class_hierarchy: { class: string; hierarchy: { name: string; path: string }[] }[];
  file_sources: Record<string, string>;
  total_affected: number;
  total_files: number;
}

function DiffContextResults({ data }: { data: DiffContextData }) {
  return (
    <div className="space-y-4">
      <div className="flex gap-3 flex-wrap">
        <Badge variant="secondary">{data.total_files} files</Badge>
        <Badge variant="secondary">{data.total_affected} affected symbols</Badge>
        <Badge variant="secondary">{data.callers.length} caller chains</Badge>
        <Badge variant="secondary">{data.class_hierarchy.length} class hierarchies</Badge>
      </div>

      {data.affected_symbols.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Affected Symbols</CardTitle></CardHeader>
          <CardContent className="space-y-2 max-h-96 overflow-y-auto">
            {data.affected_symbols.map((sym, i) => (
              <div key={i} className="p-3 rounded border border-border space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant={sym.type === "function" ? "default" : "secondary"}>{sym.type}</Badge>
                  <span className="font-medium font-mono">{sym.name}</span>
                  <span className="text-xs text-muted-foreground">{sym.change_file}:{sym.start_line}-{sym.end_line}</span>
                </div>
                {sym.docstring && <p className="text-xs text-muted-foreground">{sym.docstring}</p>}
                {sym.source && <CodeBlock code={sym.source} startLine={sym.start_line} maxHeight="max-h-48" />}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {data.callers.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Callers</CardTitle></CardHeader>
          <CardContent className="space-y-3 max-h-96 overflow-y-auto">
            {data.callers.map((group, i) => (
              <div key={i} className="space-y-1">
                <p className="text-sm font-medium">
                  <Badge variant="outline" className="mr-1">{group.symbol_type}</Badge>
                  <span className="font-mono">{group.symbol}</span>
                  <span className="text-muted-foreground ml-1">called by:</span>
                </p>
                {group.callers.map((caller, j) => (
                  <div key={j} className="ml-4 p-2 rounded border border-border text-sm">
                    <span className="font-mono">{caller.name}</span>
                    <span className="text-xs text-muted-foreground ml-2">{caller.path}</span>
                    {caller.source && <CodeBlock code={caller.source} maxHeight="max-h-32" className="mt-1" />}
                  </div>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {data.class_hierarchy.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Class Hierarchy</CardTitle></CardHeader>
          <CardContent className="space-y-2 max-h-72 overflow-y-auto">
            {data.class_hierarchy.map((h, i) => (
              <div key={i} className="space-y-1">
                <p className="text-sm font-medium font-mono">{h.class}</p>
                <div className="ml-4 space-y-1">
                  {h.hierarchy.map((parent, j) => (
                    <div key={j} className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{j === 0 ? "" : "└─"}</span>
                      <span className="font-mono">{parent.name}</span>
                      <span className="text-xs text-muted-foreground">{parent.path}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {Object.keys(data.file_sources).length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">File Sources</CardTitle></CardHeader>
          <CardContent className="space-y-3 max-h-96 overflow-y-auto">
            {Object.entries(data.file_sources).map(([path, source]) => (
              <div key={path} className="space-y-1">
                <p className="text-sm font-mono text-primary">{path}</p>
                <CodeBlock code={source} maxHeight="max-h-64" />
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ─── Generic JSON fallback renderer ───────────────────────────────────────────

function ResultsView({ data }: { data: unknown }) {
  if (!data) return <p className="text-muted-foreground text-sm">No results</p>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <p className="text-muted-foreground text-sm">No results found</p>;
    return (
      <div className="space-y-2 max-h-150 overflow-y-auto">
        {data.map((item, i) => (
          <div key={i} className="p-3 rounded border border-border text-sm font-mono whitespace-pre-wrap break-all">
            {typeof item === "object" ? <ObjectView obj={item} /> : String(item)}
          </div>
        ))}
      </div>
    );
  }

  if (typeof data === "object") return <ObjectView obj={data as Record<string, unknown>} />;
  return <pre className="text-sm font-mono">{String(data)}</pre>;
}

function ObjectView({ obj }: { obj: Record<string, unknown> }) {
  return (
    <div className="space-y-1">
      {Object.entries(obj).map(([key, value]) => {
        if (key === "source" && typeof value === "string" && value) {
          const startLine = (obj.line_number as number) || (obj.start_line as number) || 1;
          return (
            <div key={key} className="space-y-1">
              <span className="text-primary font-medium">{key}:</span>
              <CodeBlock code={value} startLine={startLine} maxHeight="max-h-64" />
            </div>
          );
        }
        if (Array.isArray(value)) {
          return (
            <div key={key} className="space-y-1">
              <span className="text-primary font-medium">{key}:</span>
              {value.length === 0
                ? <span className="text-muted-foreground ml-2">[]</span>
                : (
                  <div className="ml-4 space-y-2">
                    {value.map((item, i) => (
                      <div key={i} className="p-3 rounded border border-border text-sm font-mono whitespace-pre-wrap break-all">
                        {typeof item === "object" && item !== null ? <ObjectView obj={item as Record<string, unknown>} /> : String(item)}
                      </div>
                    ))}
                  </div>
                )}
            </div>
          );
        }
        if (typeof value === "object" && value !== null) {
          return (
            <div key={key} className="space-y-1">
              <span className="text-primary font-medium">{key}:</span>
              <div className="ml-4 p-3 rounded border border-border text-sm font-mono whitespace-pre-wrap break-all">
                <ObjectView obj={value as Record<string, unknown>} />
              </div>
            </div>
          );
        }
        return (
          <div key={key} className="flex gap-2">
            <span className="text-primary font-medium shrink-0">{key}:</span>
            <span className="text-muted-foreground break-all">{String(value ?? "—")}</span>
          </div>
        );
      })}
    </div>
  );
}
