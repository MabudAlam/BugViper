"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getToolsConfig,
  saveToolsConfig,
  type ToolsConfig,
  type LinterToolConfig,
} from "@/lib/api";
import { useEffect, useState } from "react";

function fmtToolKey(key: string) {
  switch (key) {
    case "golangciLint": return "golangci-lint";
    case "ruff": return "Ruff";
    case "eslint": return "ESLint";
    default: return key;
  }
}

export default function ToolsPage() {
  const [config, setConfig] = useState<ToolsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getToolsConfig()
      .then(setConfig)
      .catch(() => setConfig(null))
      .finally(() => setLoading(false));
  }, []);

  const updateTool = (key: keyof ToolsConfig, patch: Partial<LinterToolConfig>) => {
    if (!config) return;
    setConfig({ ...config, [key]: { ...config[key], ...patch } });
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setSaved(false);
    try {
      await saveToolsConfig(config);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <Skeleton className="h-40 w-full rounded-base" />
        <Skeleton className="h-40 w-full rounded-base" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="max-w-3xl mx-auto text-center py-20">
        <p className="text-muted-foreground">Failed to load tools configuration.</p>
      </div>
    );
  }

  const tools: { key: keyof ToolsConfig; tool: LinterToolConfig }[] = [
    { key: "ruff", tool: config.ruff },
    { key: "eslint", tool: config.eslint },
    { key: "golangciLint", tool: config.golangciLint },
  ];

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {tools.map(({ key, tool }, i) => (
        <Card key={key} className="bg-secondary-background shadow-shadow overflow-hidden animate-fade-in-up" style={{ animationDelay: `${i * 80}ms` }}>
          <CardContent className="p-6 space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-base bg-main/10 border-2 border-main/30 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5 text-main" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">{tool.name || fmtToolKey(key)}</h3>
                  {tool.url && (
                    <a href={tool.url} target="_blank" rel="noopener noreferrer" className="text-xs text-muted-foreground hover:text-main underline">
                      {tool.url}
                    </a>
                  )}
                </div>
              </div>
              <button
                onClick={() => updateTool(key, { enabled: !tool.enabled })}
                className={`relative w-11 h-6 rounded-full border-2 transition-all duration-150 ${
                  tool.enabled
                    ? "bg-main border-main"
                    : "bg-background border-border"
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white border-2 transition-all duration-150 ${
                  tool.enabled
                    ? "translate-x-5 border-main"
                    : "translate-x-0 border-border"
                }`} />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">File Extensions</p>
                <div className="flex flex-wrap gap-1.5">
                  {tool.extensions.map((ext) => (
                    <span key={ext} className="text-[11px] px-2 py-0.5 rounded-base border-2 border-border text-muted-foreground font-mono">
                      {ext}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Config File</p>
                <select
                  value={tool.configFile}
                  onChange={(e) => updateTool(key, { configFile: e.target.value })}
                  disabled={!tool.enabled}
                  className="w-full rounded-base border-2 border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-50"
                >
                  <option value="">Auto-detect</option>
                  {tool.configFiles.map((f) => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}

      <div className="flex items-center gap-3">
        <Button
          variant="default"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "Saving..." : "Save Configuration"}
        </Button>
        {saved && (
          <span className="text-sm text-main font-semibold animate-fade-in-up">
            Saved successfully
          </span>
        )}
      </div>
    </div>
  );
}
