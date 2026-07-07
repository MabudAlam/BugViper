"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getInstallationStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { BugViperFullLogo } from "@/components/logo";

const FEATURES = [
  {
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
      </svg>
    ),
    title: "AI-Powered Reviews",
    desc: "Automated code review powered by LLMs. Catch bugs, security issues, and style violations before they ship.",
  },
  {
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 21v-8.25M15.75 21v-8.25M8.25 21v-8.25M3 9l9-6 9 6m-1.5 12V10.332A48.36 48.36 0 0012 9.75c-2.551 0-5.056.2-7.5.582V21M3 21h18M12 6.75h.008v.008H12V6.75z" />
      </svg>
    ),
    title: "Multi-Repo Dashboard",
    desc: "Track issues, reviews, and resolution rates across all your repositories from one place.",
  },
  {
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
      </svg>
    ),
    title: "Fast & Context-Aware",
    desc: "Incremental reviews that only analyze changed code. Full diff awareness for accurate, relevant feedback.",
  },
  {
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
    title: "Actionable Insights",
    desc: "Severity-rated issues with code snippets and suggestions. Track fix rates and team improvement over time.",
  },
];

const INSTALL_URL = process.env.NEXT_PUBLIC_GITHUB_APP_INSTALL_URL;

export default function OnboardingPage() {
  const { user, loading, signInWithGitHub } = useAuth();
  const router = useRouter();
  const [signingIn, setSigningIn] = useState(false);
  const [needsInstall, setNeedsInstall] = useState(false);
  const [linking, setLinking] = useState(false);

  useEffect(() => {
    if (loading || !user) return;
    checkAndRedirect();
  }, [user, loading]);

  async function checkAndRedirect() {
    setLinking(true);
    for (let i = 0; i < 10; i++) {
      try {
        const status = await getInstallationStatus();
        if (status.linked) {
          router.replace("/dashboard");
          return;
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 500));
    }
    setNeedsInstall(true);
    setLinking(false);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-[3px] border-main border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-muted-foreground">Setting things up...</p>
        </div>
      </div>
    );
  }

  // Checking status after signin
  if (linking || signingIn) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="flex flex-col items-center gap-4">
          <BugViperFullLogo width={200} height={56} />
          <div className="w-8 h-8 border-[3px] border-main border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-muted-foreground">Linking your GitHub installation...</p>
        </div>
      </div>
    );
  }

  // Signed in but no installation found
  if (needsInstall) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="max-w-md w-full mx-auto px-4 text-center space-y-6">
          <BugViperFullLogo width={200} height={56} />
          <div className="w-16 h-16 mx-auto rounded-full bg-main/10 border-2 border-main/30 flex items-center justify-center">
            <svg className="w-8 h-8 text-main" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
          </div>
          <div>
            <h2 className="text-xl font-bold text-foreground">Install the GitHub App</h2>
            <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
              We found your account but couldn't find a linked GitHub App installation.
              Install BugViper on your repositories to get started.
            </p>
          </div>
          <Button asChild size="lg" className="gap-3">
            <a href={INSTALL_URL || "#"} target="_blank" rel="noopener noreferrer">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              Install GitHub App
            </a>
          </Button>
          <p className="text-xs text-muted-foreground">
            After installing, come back here and refresh.
          </p>

          <hr className="border-border" />

          <div className="space-y-3">
            <p className="font-bold text-foreground">Ready to simplify your code reviews?</p>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Sign in once and your installation is linked. Then just mention
              {" "}<code className="px-1.5 py-0.5 rounded-sm bg-main/10 text-main text-xs font-mono">@bugviper</code>
              {" "}on any PR to get started.
            </p>
          </div>
        </div>
      </div>
    );
  }

  async function handleSignIn() {
    setSigningIn(true);
    try {
      await signInWithGitHub();
    } catch {
      setSigningIn(false);
    }
  }

  // Not signed in — landing page
  return (
    <div className="min-h-screen bg-background">
      <section className="relative overflow-hidden border-b-2 border-border">
        <div className="absolute inset-0 bg-gradient-to-b from-main/5 to-transparent pointer-events-none" />
        <div className="max-w-5xl mx-auto px-4 pt-20 pb-24 text-center relative">
          <div className="flex justify-center mb-8">
            <BugViperFullLogo width={280} height={78} />
          </div>
          <h1 className="text-4xl md:text-5xl font-bold text-foreground leading-tight max-w-2xl mx-auto">
            Your GitHub App is
            <span className="text-main block">Ready to Go</span>
          </h1>
          <p className="mt-4 text-lg text-muted-foreground max-w-lg mx-auto leading-relaxed">
            Sign in with GitHub to connect your installation and start
            getting AI-powered code reviews on every pull request.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4">
            <Button
              onClick={handleSignIn}
              disabled={signingIn}
              className="gap-3 h-14 px-10 text-base font-semibold shadow-[4px_4px_0px_0px_var(--main)] hover:shadow-[2px_2px_0px_0px_var(--main)] hover:translate-x-0.5 hover:translate-y-0.5 transition-all"
            >
              <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              {signingIn ? "Signing in..." : "Sign in with GitHub"}
            </Button>
            <p className="text-xs text-muted-foreground">
              Your installation will be linked to your account automatically.
            </p>
          </div>
        </div>
      </section>

      <section className="max-w-5xl mx-auto px-4 py-20">
        <div className="text-center mb-14">
          <h2 className="text-2xl md:text-3xl font-bold text-foreground">
            What BugViper does for your code
          </h2>
          <p className="mt-3 text-muted-foreground max-w-lg mx-auto">
            Automated code review that keeps your team shipping quality code.
          </p>
        </div>
        <div className="grid sm:grid-cols-2 gap-6">
          {FEATURES.map((f, i) => (
            <div
              key={i}
              className="bg-secondary-background border-2 border-border rounded-base p-6 space-y-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_0px_var(--main)]"
            >
              <div className="w-11 h-11 rounded-base bg-main/10 border-2 border-main/30 flex items-center justify-center text-main">
                {f.icon}
              </div>
              <div>
                <h3 className="font-bold text-foreground">{f.title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">{f.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>


    </div>
  );
}
