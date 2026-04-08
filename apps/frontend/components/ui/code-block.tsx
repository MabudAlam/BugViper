"use client";

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
  if (!code) return null;

  const lines = code.split("\n");
  const lineNumberWidth = String(startLine + lines.length - 1).length;

  return (
    <div className={cn("rounded border border-border overflow-hidden", className)}>
      {language && (
        <div className="bg-muted px-3 py-1 text-xs text-muted-foreground border-b border-border">
          {language}
        </div>
      )}
      <pre className={cn("text-xs font-mono overflow-auto", maxHeight)}>
        <code className="block">
          {lines.map((line, i) => {
            const lineNum = startLine + i;
            return (
              <div key={i} className="flex hover:bg-muted/50">
                <span
                  className="select-none text-muted-foreground text-right pr-3 pl-2 border-r border-border bg-muted/30 shrink-0"
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
    </div>
  );
}
