"use client";

import { useEffect, useRef, useState } from "react";
import { X, Send, Loader2, Bot, User } from "lucide-react";
import { askAgent } from "@/lib/api";

interface Message {
  role: "user" | "agent";
  content: string;
}

interface AgentChatProps {
  open: boolean;
  onClose: () => void;
}

export function AgentChat({ open, onClose }: AgentChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setMessages((m) => [...m, { role: "user", content: question }]);
    setInput("");
    setLoading(true);

    try {
      const res = await askAgent({ question });
      setMessages((m) => [...m, { role: "agent", content: res.answer }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "agent", content: `Error: ${e instanceof Error ? e.message : "Something went wrong"}` },
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

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 transition-opacity duration-200 ease-out ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className={`fixed right-0 top-0 h-screen w-[420px] z-50 flex flex-col bg-background border-l border-border shadow-xl transition-transform duration-200 ease-out ${open ? "translate-x-0" : "translate-x-full"}`}
      >

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Bot className="w-4 h-4 text-primary" />
            <span className="font-semibold text-sm">Ask the Codebase</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.length === 0 && (
            <p className="text-xs text-muted-foreground text-center pt-8">
              Ask anything about the indexed codebase. The agent will search and reason to answer.
            </p>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              {msg.role === "agent" && (
                <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
                  <Bot className="w-3.5 h-3.5 text-primary" />
                </div>
              )}
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground"
                }`}
              >
                {msg.content}
              </div>
              {msg.role === "user" && (
                <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center shrink-0 mt-0.5">
                  <User className="w-3.5 h-3.5 text-primary" />
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex gap-2.5 justify-start">
              <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                <Bot className="w-3.5 h-3.5 text-primary" />
              </div>
              <div className="bg-muted rounded-lg px-3 py-2">
                <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="shrink-0 border-t border-border px-4 py-3">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about the codebase… (Enter to send)"
              rows={1}
              className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring max-h-32 overflow-y-auto"
              style={{ minHeight: "38px" }}
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              className="shrink-0 h-[38px] w-[38px] flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
          <p className="text-[10px] text-muted-foreground mt-1.5">Shift+Enter for new line</p>
        </div>
      </div>
    </>
  );
}
