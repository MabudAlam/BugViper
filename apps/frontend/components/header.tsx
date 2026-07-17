"use client";

import { usePathname } from "next/navigation";

const pageMeta: Record<string, { title: string; subtitle: string }> = {
  "/dashboard": { title: "Dashboard", subtitle: "Your code review activity overview" },
  "/tools": { title: "Tools", subtitle: "Configure your linter tools" },
  "/support": { title: "Support", subtitle: "Get help with BugViper" },
};

export function Header() {
  const pathname = usePathname();
  const meta = pageMeta[pathname] ?? { title: "BugViper", subtitle: "" };

  return (
    <header className="h-[72px] border-b-2 border-border bg-secondary-background flex items-center px-4 md:px-6 shrink-0">
      <div>
        <h1 className="text-lg font-bold text-foreground">{meta.title}</h1>
        {meta.subtitle && <p className="text-xs text-muted-foreground mt-0.5">{meta.subtitle}</p>}
      </div>
    </header>
  );
}
