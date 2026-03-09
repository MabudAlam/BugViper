"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, User, Send, Loader2, ChevronDown, FileCode, ChevronRight, X, SquarePen } from "lucide-react";
import { askAgent, getMySession, clearMySession, listRepositories, peekFileLines, type AgentSource } from "@/lib/api";
import { CodeBlock } from "@/components/ui/code-block";
import { cn } from "@/lib/utils";

// ─── Types ───────────────────────────────────────────────────────────────────

interface PeekLine {
  line_number: number;
  content: string;
  is_anchor: boolean;
}

interface PeekResult {
  path: string;
  anchor_line: number;
  total_lines: number;
  window: PeekLine[];
}

// Carries the source that produced this state so we can derive "loading"
// without a synchronous setState call in an effect.
type SheetState =
  | { status: "idle" }
  | { status: "ok"; source: AgentSource; data: PeekResult }
  | { status: "error"; source: AgentSource; message: string };

interface Message {
  role: "user" | "agent";
  content: string;
  sources?: AgentSource[];
}

interface Repo {
  fullName: string;
  owner: string;
  repoName: string;
}

// ─── Source side-sheet ───────────────────────────────────────────────────────

function SourceSheet({ source, onClose }: { source: AgentSource | null; onClose: () => void }) {
  const [state, setState] = useState<SheetState>({ status: "idle" });

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    peekFileLines(source.path, source.line_number ?? 1, 30, 30)
      .then((res) => {
        if (!cancelled) setState({ status: "ok", source, data: res as PeekResult });
      })
      .catch((e) => {
        if (!cancelled)
          setState({
            status: "error",
            source,
            message: e instanceof Error ? e.message : "Failed to load",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  useEffect(() => {
    if (!source) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [source, onClose]);

  if (typeof document === "undefined" || !source) return null;

  // Derive loading: no result yet, or the result is for a different source
  const stateSource = state.status !== "idle" ? state.source : null;
  const isLoading = state.status === "idle" || stateSource !== source;
  const peekData = state.status === "ok" && state.source === source ? state.data : null;
  const errorMsg =
    state.status === "error" && state.source === source ? state.message : null;

  const code = peekData ? peekData.window.map((l) => l.content).join("\n") : "";
  const startLine = peekData?.window[0]?.line_number ?? 1;

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 z-50 h-full w-full max-w-[560px] flex flex-col bg-background border-l border-border shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="min-w-0 pr-3">
            <p className="text-xs font-mono text-primary truncate">{source.path}</p>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {source.name && (
                <span className="text-sm font-medium text-foreground">{source.name}</span>
              )}
              {source.type && (
                <span className="text-[11px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                  {source.type}
                </span>
              )}
              {source.line_number && (
                <span className="text-xs text-muted-foreground">line {source.line_number}</span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors shrink-0"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-4">
          {isLoading && (
            <div className="flex items-center justify-center h-32 gap-2 text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span className="text-sm">Loading…</span>
            </div>
          )}
          {errorMsg && <p className="text-sm text-destructive p-2">{errorMsg}</p>}
          {peekData && <CodeBlock code={code} startLine={startLine} maxHeight="max-h-full" />}
        </div>
      </div>
    </>,
    document.body
  );
}

// ─── Markdown renderer ───────────────────────────────────────────────────────

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => (
          <h1 className="text-base font-semibold mt-4 mb-2 first:mt-0 border-b border-border/40 pb-1">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-sm font-semibold mt-3 mb-1.5 first:mt-0">{children}</h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-sm font-medium mt-2.5 mb-1 first:mt-0 text-foreground/80">
            {children}
          </h3>
        ),
        p: ({ children }) => (
          <p className="mb-2.5 last:mb-0 leading-relaxed text-sm">{children}</p>
        ),
        ul: ({ children }) => (
          <ul className="list-disc pl-5 space-y-1 mb-2.5 text-sm">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="list-decimal pl-5 space-y-1 mb-2.5 text-sm">{children}</ol>
        ),
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        pre: ({ children }) => (
          <pre className="my-2.5 rounded-lg border border-border bg-background p-3.5 text-xs font-mono leading-relaxed overflow-x-auto">
            {children}
          </pre>
        ),
        code: ({ children, className }) =>
          className ? (
            <code className={cn("text-xs", className)}>{children}</code>
          ) : (
            <code className="px-1.5 py-0.5 rounded-md text-[11px] font-mono bg-background border border-border/70 text-primary whitespace-nowrap">
              {children}
            </code>
          ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-primary/50 pl-3 text-muted-foreground my-2.5 italic text-sm">
            {children}
          </blockquote>
        ),
        a: ({ href, children }) => (
          <a
            href={href}
            className="text-primary underline underline-offset-2 hover:opacity-80"
            target="_blank"
            rel="noopener noreferrer"
          >
            {children}
          </a>
        ),
        table: ({ children }) => (
          <div className="overflow-x-auto my-2.5">
            <table className="text-xs border-collapse w-full">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-border px-3 py-1.5 bg-muted/80 font-medium text-left">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border border-border px-3 py-1.5">{children}</td>
        ),
        hr: () => <hr className="border-border/50 my-3" />,
        strong: ({ children }) => (
          <strong className="font-semibold text-foreground">{children}</strong>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

// ─── Source chip ─────────────────────────────────────────────────────────────

function SourceChip({ source, onClick }: { source: AgentSource; onClick: () => void }) {
  const label = source.line_number ? `${source.path}:${source.line_number}` : source.path;
  return (
    <button
      onClick={onClick}
      title={source.name ? `${source.type ?? "symbol"}: ${source.name}` : label}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono bg-background border border-border text-muted-foreground hover:text-foreground hover:border-primary/50 hover:bg-primary/5 transition-colors"
    >
      <FileCode className="w-3 h-3 shrink-0" />
      {label}
    </button>
  );
}

// ─── Agent message ────────────────────────────────────────────────────────────

function AgentMessage({
  msg,
  onSourceClick,
}: {
  msg: Message;
  onSourceClick: (s: AgentSource) => void;
}) {
  const [showSources, setShowSources] = useState(false);
  const hasSources = msg.sources && msg.sources.length > 0;

  return (
    <div className="flex gap-3 max-w-3xl">
      <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5 border border-primary/20">
        <Bot className="w-3.5 h-3.5 text-primary" />
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        {/* Bubble */}
        <div className="bg-muted/60 border border-border/60 rounded-2xl rounded-tl-sm px-4 py-3 min-w-0 overflow-hidden">
          <MarkdownContent content={msg.content} />
        </div>
        {/* Sources toggle — sits below the bubble */}
        {hasSources && (
          <div className="pl-1 space-y-1.5">
            <button
              onClick={() => setShowSources((v) => !v)}
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronRight
                className={cn(
                  "w-3 h-3 transition-transform duration-150",
                  showSources && "rotate-90"
                )}
              />
              {msg.sources!.length} source{msg.sources!.length !== 1 ? "s" : ""}
            </button>
            {showSources && (
              <div className="flex flex-wrap gap-1.5">
                {msg.sources!.map((s, i) => (
                  <SourceChip key={i} source={s} onClick={() => onSourceClick(s)} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AgentPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [activeSource, setActiveSource] = useState<AgentSource | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load repos once
  useEffect(() => {
    listRepositories()
      .then((data: { repositories: Repo[] }) => setRepos(data.repositories ?? []))
      .catch(() => {});
  }, []);

  // Whenever the selected repo changes, fetch the user's session history from the server.
  // Session key is computed server-side from (firebase_uid, repo_id) — no localStorage needed.
  useEffect(() => {
    setMessages([]);
    setHistoryLoading(true);
    getMySession(selectedRepo || undefined)
      .then((data) => {
        setMessages(
          data.messages.map((m) => ({
            role: m.role === "human" ? "user" : "agent",
            content: m.content,
            sources: m.sources ?? [],
          }))
        );
      })
      .catch(() => {
        // No session yet or network error — start with empty chat
        setMessages([]);
      })
      .finally(() => setHistoryLoading(false));
  }, [selectedRepo]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 144) + "px";
  };

  const handleNewChat = async () => {
    await clearMySession(selectedRepo || undefined).catch(() => {});
    setMessages([]);
  };

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setMessages((m) => [...m, { role: "user", content: question }]);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setLoading(true);

    try {
      const res = await askAgent({
        question,
        repo_id: selectedRepo || undefined,
      });
      setMessages((m) => [
        ...m,
        { role: "agent", content: res.answer, sources: res.sources ?? [] },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "agent",
          content: `**Error:** ${e instanceof Error ? e.message : "Something went wrong"}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const handleSourceClick = useCallback((s: AgentSource) => setActiveSource(s), []);
  const closeSheet = useCallback(() => setActiveSource(null), []);

  return (
    <>
      {/*
        -m-8 escapes the layout's p-8, h-screen fills full viewport.
        The sidebar is fixed at w-56 so this fills exactly the remaining area.
      */}
      <div className="-m-8 h-screen flex flex-col overflow-hidden">
        {/* Top bar */}
        <div className="shrink-0 flex items-center justify-between gap-4 px-6 py-3 border-b border-border bg-background">
          <div>
            <h1 className="text-sm font-semibold">Ask Agent</h1>
            <p className="text-xs text-muted-foreground">
              Chat with your codebase — searches Neo4j and reasons over your code.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="relative">
              <select
                value={selectedRepo}
                onChange={(e) => setSelectedRepo(e.target.value)}
                className="appearance-none rounded-lg border border-input bg-background pl-3 pr-7 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
              >
                <option value="">All repos</option>
                {repos.map((r) => (
                  <option key={r.fullName} value={r.fullName}>
                    {r.fullName}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
            </div>
            <button
              onClick={handleNewChat}
              title="New chat"
              className="p-1.5 rounded-lg border border-input bg-background hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            >
              <SquarePen className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Messages — scrollable */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {historyLoading && (
            <div className="flex items-center justify-center h-full gap-2 text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span className="text-xs">Restoring conversation…</span>
            </div>
          )}

          {!historyLoading && messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
              <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                <Bot className="w-5 h-5 text-primary" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium">
                  {selectedRepo ? `Chatting with ${selectedRepo}` : "Ask about your codebase"}
                </p>
                <p className="text-xs text-muted-foreground max-w-xs">
                  Functions, architecture, dependencies, complexity — just ask.
                </p>
              </div>
            </div>
          )}

          {messages.map((msg, i) =>
            msg.role === "user" ? (
              <div key={i} className="flex gap-3 justify-end">
                <div className="max-w-2xl rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm bg-primary text-primary-foreground leading-relaxed">
                  {msg.content}
                </div>
                <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center shrink-0 mt-0.5">
                  <User className="w-3.5 h-3.5 text-primary" />
                </div>
              </div>
            ) : (
              <AgentMessage key={i} msg={msg} onSourceClick={handleSourceClick} />
            )
          )}

          {loading && (
            <div className="flex gap-3 max-w-3xl">
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5 border border-primary/20">
                <Bot className="w-3.5 h-3.5 text-primary" />
              </div>
              <div className="bg-muted/60 border border-border/60 rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                <span className="text-xs">BugViper is thinking…</span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input bar */}
        <div className="shrink-0 border-t border-border bg-background px-6 py-4">
          <div className="max-w-3xl mx-auto flex gap-2 items-end">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={onKeyDown}
              placeholder="Ask anything about the codebase…"
              rows={1}
              autoFocus
              className="flex-1 resize-none rounded-xl border border-input bg-muted/40 px-4 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring leading-relaxed"
              style={{ minHeight: "42px", maxHeight: "144px" }}
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              className="h-[42px] w-[42px] flex items-center justify-center rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </div>
          <p className="text-[10px] text-muted-foreground/40 mt-1.5 text-center">
            Enter to send · Shift+Enter for new line
          </p>
        </div>
      </div>

      <SourceSheet source={activeSource} onClose={closeSheet} />
    </>
  );
}
