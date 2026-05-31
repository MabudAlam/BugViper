"use client";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface CodeBlockProps {
  code: string;
  startLine?: number;
  language?: string;
  maxHeight?: string;
  className?: string;
}

export function CodeBlock({
  code,
  startLine = 1,
  language,
  maxHeight = "max-h-64",
  className
}: CodeBlockProps) {
  const [expanded, setExpanded] = useState(false);
  if (!code) return null;

  const lines = code.split("\n");
  const lineNumberWidth = String(startLine + lines.length - 1).length;
  const shouldCollapse = lines.length > 12;

  return (
    <div className={cn("rounded border border-border overflow-hidden", className)}>
      {language && (
        <div className="bg-muted px-3 py-1 text-xs text-muted-foreground border-b border-border">
          {language}
        </div>
      )}
      <pre
        className={cn(
          "text-xs font-mono overflow-auto transition-all",
          shouldCollapse && !expanded ? "max-h-48" : maxHeight
        )}
      >
        <code className="block">
          {lines.map((line, i) => {
            const lineNum = startLine + i;
            return (
              <div key={i} className="flex hover:bg-muted/50">
                <span
                  className="select-none text-muted-foreground/80 dark:text-muted-foreground/60 text-right pr-3 pl-2 border-r border-border bg-muted/40 dark:bg-muted/30 shrink-0"
                  style={{ minWidth: `${lineNumberWidth + 2}ch` }}
                >
                  {lineNum}
                </span>
                <span className="pl-3 pr-2 whitespace-pre">{line || " "}</span>
              </div>
            );
          })}
        </code>
      </pre>

      {shouldCollapse && (
        <div className="border-t border-border bg-muted/30 px-3 py-2">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-primary hover:underline"
          >
            {expanded ? "Show Less" : "Show More"}
          </button>
        </div>
      )}
    </div>
  );
}