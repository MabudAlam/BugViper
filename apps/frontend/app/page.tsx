"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth-context";
import {
  Bug,
  Shield,
  Network,
  Code2,
  GitPullRequest,
  Zap,
  ArrowRight,
  LayoutDashboard,
} from "lucide-react";

const features = [
  {
    icon: Bug,
    title: "AI Bug Detection",
    description:
      "Identifies logic errors, edge cases, null pointer risks, and anti-patterns before they reach production.",
  },
  {
    icon: Shield,
    title: "Security Auditing",
    description:
      "Scans for OWASP Top 10 vulnerabilities, injection risks, authentication flaws, and insecure patterns.",
  },
  {
    icon: Network,
    title: "Graph Intelligence",
    description:
      "Maps every function, class, and import relationship in a Neo4j graph for deep structural understanding.",
  },
  {
    icon: Code2,
    title: "17 Language Parsers",
    description:
      "Python, TypeScript, Go, Rust, Java, C++, Ruby, Swift, and 9 more — all parsed with Tree-sitter ASTs.",
  },
  {
    icon: GitPullRequest,
    title: "PR-Native Reviews",
    description:
      "GitHub App webhooks trigger automatic reviews the moment a PR is opened or @bugviper is mentioned.",
  },
  {
    icon: Zap,
    title: "Incremental Updates",
    description:
      "Only re-analyses changed files on push. Your graph stays current without full re-ingestion overhead.",
  },
];

const steps = [
  {
    number: "01",
    title: "Connect Your Repository",
    description:
      "Install the GitHub App and authorize BugViper. Private repos, organizations, and monorepos are all supported.",
  },
  {
    number: "02",
    title: "Graph Gets Built",
    description:
      "BugViper parses your codebase with Tree-sitter and writes every relationship into a Neo4j knowledge graph.",
  },
  {
    number: "03",
    title: "Reviews Arrive Instantly",
    description:
      "Open a PR or mention @bugviper in any comment. Bug-hunter and security-auditor agents run in parallel.",
  },
];

const stats = [
  { value: "17", label: "Languages" },
  { value: "6", label: "Node Types" },
  { value: "2", label: "AI Agents" },
  { value: "<30s", label: "Review Time" },
];

export default function LandingPage() {
  const { user, loading } = useAuth();

  return (
    <div className="min-h-screen bg-[oklch(0.145_0_0)] text-[oklch(0.985_0_0)] font-(family-name:--font-geist-sans) overflow-x-hidden">

      {/* ── Ambient background glows ───────────────────────────────────────── */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden" aria-hidden>
        <div className="absolute -top-40 left-[8%] w-160 h-160 rounded-full bg-[oklch(0.723_0.219_142.1)] opacity-[0.07] blur-[130px]" />
        <div className="absolute top-[45%] -right-20 w-120 h-120 rounded-full bg-[oklch(0.488_0.243_264.376)] opacity-[0.05] blur-[110px]" />
        <div className="absolute bottom-[8%] left-[28%] w-140 h-105 rounded-full bg-[oklch(0.723_0.219_142.1)] opacity-[0.04] blur-[130px]" />
      </div>

      {/* ── Navigation ─────────────────────────────────────────────────────── */}
      <nav className="relative z-10 flex items-center justify-between px-6 py-5 max-w-6xl mx-auto">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-[oklch(0.723_0.219_142.1)] flex items-center justify-center shrink-0">
            <Bug className="w-4 h-4 text-[oklch(0.13_0.028_147)]" />
          </div>
          <span className="font-semibold text-base tracking-tight">BugViper</span>
        </div>
        <Button size="sm" asChild>
          <Link href={user ? "/repositories" : "/login"}>
            {user ? (
              <>
                <LayoutDashboard className="mr-1.5 w-3.5 h-3.5" />
                Dashboard
              </>
            ) : (
              <>
                Get Started
                <ArrowRight className="ml-1.5 w-3.5 h-3.5" />
              </>
            )}
          </Link>
        </Button>
      </nav>

      {/* ── Hero ───────────────────────────────────────────────────────────── */}
      <section className="relative z-10 px-6 pt-20 pb-28 max-w-6xl mx-auto text-center">
        <Badge
          variant="secondary"
          className="mb-7 gap-2 border border-white/10 bg-white/5 backdrop-blur-sm text-[oklch(0.985_0_0)] px-3 py-1"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[oklch(0.723_0.219_142.1)] shrink-0" />
          AI-powered code review · graph intelligence
        </Badge>

        <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold tracking-tight leading-[1.05] mb-6">
          Ship code.
          <br />
          <span className="text-[oklch(0.723_0.219_142.1)]">Not bugs.</span>
        </h1>

        <p className="text-lg text-[oklch(0.708_0_0)] max-w-2xl mx-auto mb-10 leading-relaxed">
          BugViper maps your entire repository into a knowledge graph, then
          runs parallel AI agents to detect bugs and audit security on every
          pull request — before your users find them.
        </p>

        <div className="flex items-center justify-center gap-3 flex-wrap">
          <Button size="lg" asChild>
            <Link href={user ? "/repositories" : "/login"}>
              {user ? "Go to Dashboard" : "Start Reviewing Free"}
              <ArrowRight className="ml-2 w-4 h-4" />
            </Link>
          </Button>
          <Button
            size="lg"
            variant="outline"
            className="border-white/10 bg-white/4 backdrop-blur-sm hover:bg-white/8 hover:border-white/20"
            asChild
          >
            <Link href="#how-it-works">See How It Works</Link>
          </Button>
        </div>

        {/* Hero code preview */}
        <div className="mt-16 relative max-w-3xl mx-auto">
          <div className="absolute inset-0 bg-[oklch(0.723_0.219_142.1)] opacity-[0.08] blur-[70px] rounded-3xl" />
          <div className="relative rounded-2xl border border-white/10 bg-white/3 backdrop-blur-xl p-1 shadow-2xl">
            <div className="rounded-xl bg-[oklch(0.11_0_0)] p-5 text-left overflow-x-auto">
              {/* Window chrome */}
              <div className="flex items-center gap-1.5 mb-5">
                <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
                <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
                <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
                <span className="ml-3 text-xs text-[oklch(0.708_0_0)] font-(family-name:--font-geist-mono)">
                  bugviper · review · auth.py
                </span>
              </div>
              <pre className="text-xs sm:text-sm font-(family-name:--font-geist-mono) leading-[1.8] whitespace-pre">
                <span className="text-[oklch(0.55_0_0)]">{"// BugViper detected 2 issues in auth.py\n\n"}</span>
                <span className="text-red-400 font-semibold">{"❌ [BUG] "}</span>
                <span className="text-[oklch(0.708_0_0)]">{"Line 47 — Missing bounds check\n"}</span>
                <span className="text-[oklch(0.75_0_0)]">{"   if user_id "}</span>
                <span className="text-[oklch(0.723_0.219_142.1)]">{">"}</span>
                <span className="text-[oklch(0.75_0_0)]">{" 0:"}</span>
                <span className="text-[oklch(0.55_0_0)]">{"  # ← also fails for user_id = 0\n"}</span>
                <span className="text-[oklch(0.75_0_0)]">{"       return get_user(user_id)\n\n"}</span>
                <span className="text-yellow-400 font-semibold">{"⚠ [SECURITY] "}</span>
                <span className="text-[oklch(0.708_0_0)]">{"Line 89 — SQL injection risk\n"}</span>
                <span className="text-[oklch(0.75_0_0)]">{'   query = f"SELECT * FROM users WHERE id = '}</span>
                <span className="text-red-400">{"{"}</span>
                <span className="text-[oklch(0.75_0_0)]">{"id"}</span>
                <span className="text-red-400">{"}"}</span>
                <span className="text-[oklch(0.75_0_0)]">{'"'}</span>
                <span className="text-[oklch(0.55_0_0)]">{"  # ← use parameterised queries"}</span>
              </pre>
            </div>
          </div>
        </div>
      </section>

      {/* ── Stats ──────────────────────────────────────────────────────────── */}
      <section className="relative z-10 px-6 pb-16 max-w-6xl mx-auto">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {stats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl border border-white/8 bg-white/4 backdrop-blur-sm p-5 text-center"
            >
              <div className="text-3xl font-bold text-[oklch(0.723_0.219_142.1)] font-(family-name:--font-geist-mono)">
                {stat.value}
              </div>
              <div className="text-xs text-[oklch(0.708_0_0)] mt-1.5 tracking-wide uppercase">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ───────────────────────────────────────────────────────── */}
      <section className="relative z-10 px-6 py-24 max-w-6xl mx-auto">
        <div className="text-center mb-14">
          <Badge
            variant="secondary"
            className="mb-4 border border-white/10 bg-white/5 backdrop-blur-sm text-[oklch(0.985_0_0)]"
          >
            Capabilities
          </Badge>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight">
            Intelligence at every layer
          </h2>
          <p className="text-[oklch(0.708_0_0)] mt-4 max-w-xl mx-auto leading-relaxed">
            From AST parsing to graph traversal to LLM reasoning — BugViper
            works through your code the way a senior engineer would.
          </p>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <div
                key={feature.title}
                className="group rounded-2xl border border-white/8 bg-white/4 backdrop-blur-sm p-6 hover:bg-white/[0.07] hover:border-white/15 transition-colors duration-200"
              >
                <div className="w-9 h-9 rounded-lg bg-[oklch(0.723_0.219_142.1)]/10 border border-[oklch(0.723_0.219_142.1)]/20 flex items-center justify-center mb-4 group-hover:bg-[oklch(0.723_0.219_142.1)]/20 transition-colors duration-200">
                  <Icon className="w-4 h-4 text-[oklch(0.723_0.219_142.1)]" />
                </div>
                <h3 className="font-semibold mb-2">{feature.title}</h3>
                <p className="text-sm text-[oklch(0.708_0_0)] leading-relaxed">
                  {feature.description}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── How it works ───────────────────────────────────────────────────── */}
      <section
        id="how-it-works"
        className="relative z-10 px-6 py-24 max-w-6xl mx-auto scroll-mt-8"
      >
        <div className="text-center mb-14">
          <Badge
            variant="secondary"
            className="mb-4 border border-white/10 bg-white/5 backdrop-blur-sm text-[oklch(0.985_0_0)]"
          >
            How It Works
          </Badge>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight">
            Three steps to full coverage
          </h2>
        </div>

        <div className="grid sm:grid-cols-3 gap-4 relative">
          <div
            className="hidden sm:block absolute top-8 left-[calc(16.67%+1.5rem)] right-[calc(16.67%+1.5rem)] h-px"
            style={{
              background:
                "linear-gradient(to right, transparent, oklch(1 0 0 / 10%), transparent)",
            }}
            aria-hidden
          />
          {steps.map((step) => (
            <div
              key={step.number}
              className="relative rounded-2xl border border-white/8 bg-white/4 backdrop-blur-sm p-7"
            >
              <div className="text-4xl font-bold text-[oklch(0.723_0.219_142.1)]/25 font-(family-name:--font-geist-mono) mb-4 leading-none select-none">
                {step.number}
              </div>
              <h3 className="font-semibold mb-2">{step.title}</h3>
              <p className="text-sm text-[oklch(0.708_0_0)] leading-relaxed">
                {step.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA ────────────────────────────────────────────────────────────── */}
      <section className="relative z-10 px-6 py-24 max-w-6xl mx-auto">
        <div className="relative rounded-3xl border border-white/8 bg-white/4 backdrop-blur-xl p-12 sm:p-16 text-center overflow-hidden">
          <div
            className="absolute inset-0 pointer-events-none"
            aria-hidden
            style={{
              background:
                "radial-gradient(ellipse at 50% 0%, oklch(0.723 0.219 142.1 / 0.08) 0%, transparent 70%)",
            }}
          />
          <div className="relative">
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight mb-4">
              Catch bugs before
              <br />
              <span className="text-[oklch(0.723_0.219_142.1)]">
                your users do.
              </span>
            </h2>
            <p className="text-[oklch(0.708_0_0)] mb-8 max-w-md mx-auto leading-relaxed">
              Connect your first repository and get a full AI review in under 30
              seconds. No credit card required.
            </p>
            <Button size="lg" asChild>
              <Link href={user ? "/repositories" : "/login"}>
                {user ? "Go to Dashboard" : "Get Started Free"}
                <ArrowRight className="ml-2 w-4 h-4" />
              </Link>
            </Button>
          </div>
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className="relative z-10 px-6 py-8 max-w-6xl mx-auto border-t border-white/6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded bg-[oklch(0.723_0.219_142.1)] flex items-center justify-center shrink-0">
              <Bug className="w-3 h-3 text-[oklch(0.13_0.028_147)]" />
            </div>
            <span className="text-sm font-medium">BugViper</span>
            <span className="text-xs text-[oklch(0.708_0_0)] ml-1">© 2026</span>
          </div>
          <Link
            href="#how-it-works"
            className="text-xs text-[oklch(0.708_0_0)] hover:text-[oklch(0.985_0_0)] transition-colors"
          >
            How It Works
          </Link>
        </div>
      </footer>
    </div>
  );
}
