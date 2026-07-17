"use client";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";
import { LogOut, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { BugViperLogo } from "./logo";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" },
  { href: "/tools", label: "Tools", icon: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" },
  { href: "/support", label: "Support", icon: "M18.364 5.636l-3.536 3.536m0 5.656l3.536 3.536M9.172 9.172L5.636 5.636m3.536 9.192l-3.536 3.536M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-5 0a4 4 0 11-8 0 4 4 0 018 0z" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="fixed top-4 left-4 z-40 w-9 h-9 rounded-base border-2 border-border bg-secondary-background flex items-center justify-center hover:bg-main hover:text-main-foreground hover:border-main transition-all md:hidden"
        aria-label="Open menu"
      >
        <Menu className="w-4 h-4" />
      </button>

      <div
        className={cn(
          "fixed inset-0 z-30 bg-overlay transition-opacity duration-200 md:hidden",
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={() => setOpen(false)}
      />

      <aside
        className={cn(
          "fixed top-0 left-0 h-screen w-56 bg-secondary-background border-r-2 border-border flex flex-col z-30 transition-transform duration-200",
          "md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex items-center gap-2 px-4 py-5 border-b-2 border-border">
          <BugViperLogo size={32} />
          <span className="text-lg font-heading text-foreground flex-1">BugViper</span>
          <button
            onClick={() => setOpen(false)}
            className="w-7 h-7 rounded-base border-2 border-border flex items-center justify-center hover:bg-main hover:text-main-foreground hover:border-main transition-all md:hidden"
            aria-label="Close menu"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
          {nav.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 rounded-base text-sm font-base transition-all duration-150",
                  active
                    ? "bg-main text-main-foreground shadow-shadow"
                    : "text-muted-foreground hover:bg-main hover:text-main-foreground"
                )}
              >
                <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
                </svg>
                {item.label}
              </Link>
            );
          })}
        </nav>

        {user && (
          <div className="border-t-2 border-border px-3 py-4 space-y-3">
            <div className="flex items-center gap-3">
              {user.photoURL ? (
                <img src={user.photoURL} alt="" className="w-8 h-8 rounded-full border-2 border-border shrink-0" referrerPolicy="no-referrer" />
              ) : (
                <div className="w-8 h-8 rounded-base bg-main/10 border-2 border-border flex items-center justify-center text-xs font-heading text-main shrink-0">
                  {(user.displayName?.[0] || user.email?.[0] || "?").toUpperCase()}
                </div>
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-base text-foreground truncate">{user.displayName || "User"}</p>
                <p className="text-xs text-muted-foreground truncate">{user.email}</p>
              </div>
            </div>
            <Button
              variant="neutral"
              size="sm"
              onClick={signOut}
              className="w-full justify-start gap-2 text-xs"
            >
              <LogOut className="size-3.5" />
              Sign out
            </Button>
          </div>
        )}
      </aside>
    </>
  );
}
