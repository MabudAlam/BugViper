import Link from "next/link";
import { BugViperFullLogo } from "@/components/logo";
import { Button } from "@/components/ui/button";

export const metadata = {
  title: "Third-Party Services — BugViper",
  description: "A complete list of third-party services used by the BugViper platform.",
};

const LAST_UPDATED = "March 10, 2026";

type Service = {
  name: string;
  purpose: string;
  dataShared: string;
  privacyUrl: string;
  termsUrl: string;
};

const SERVICES: Service[] = [
  {
    name: "GitHub",
    purpose:
      "User authentication (OAuth), reading repository source code, posting PR review comments via GitHub App webhooks.",
    dataShared: "GitHub username, email, avatar, OAuth access token, repository source code for connected repos.",
    privacyUrl: "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement",
    termsUrl: "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
  },
  {
    name: "Firebase Authentication (Google)",
    purpose:
      "Secure user identity management. We use Firebase Auth to issue and verify ID tokens for every authenticated request.",
    dataShared: "Email address, display name, profile photo URL, Firebase UID.",
    privacyUrl: "https://firebase.google.com/support/privacy",
    termsUrl: "https://firebase.google.com/terms",
  },
  {
    name: "Cloud Firestore (Google)",
    purpose:
      "Primary metadata store for user profiles, repository ingestion status, PR review records, and customer support queries.",
    dataShared:
      "User profile data, repository metadata, PR metadata, review results, support form submissions.",
    privacyUrl: "https://firebase.google.com/support/privacy",
    termsUrl: "https://firebase.google.com/terms",
  },
  {
    name: "Google Cloud (GCP)",
    purpose:
      "Cloud infrastructure for running the BugViper API on Cloud Run and managing asynchronous ingestion jobs via Cloud Tasks.",
    dataShared: "Application logs, task payloads (repository owner/name/branch).",
    privacyUrl: "https://cloud.google.com/terms/cloud-privacy-notice",
    termsUrl: "https://cloud.google.com/terms",
  },
  {
    name: "Neo4j Aura",
    purpose:
      "Graph database that stores the code knowledge graph — functions, classes, files, variables, modules, and their relationships extracted from your repositories.",
    dataShared: "Parsed code structure (node names, file paths, source code snippets, call relationships).",
    privacyUrl: "https://neo4j.com/privacy-policy/",
    termsUrl: "https://neo4j.com/terms/neo4j-aura-tou/",
  },
  {
    name: "OpenRouter",
    purpose:
      "AI gateway used to route LLM requests for PR code reviews. Code diffs and graph context are sent to models such as Claude and GPT-4 via OpenRouter.",
    dataShared: "Code diffs, surrounding code context, review prompts.",
    privacyUrl: "https://openrouter.ai/privacy",
    termsUrl: "https://openrouter.ai/terms",
  },
  {
    name: "Anthropic Claude (via OpenRouter)",
    purpose:
      "Large language model used as the default review model for bug detection and security auditing.",
    dataShared: "Code diffs and context passed through OpenRouter.",
    privacyUrl: "https://www.anthropic.com/privacy",
    termsUrl: "https://www.anthropic.com/legal/consumer-terms",
  },
  {
    name: "Logfire (Pydantic)",
    purpose:
      "Application observability — structured logging, distributed tracing, and error monitoring for the BugViper API. Only enabled when ENABLE_LOGFIRE is set.",
    dataShared: "API request traces, error messages, performance metrics. No source code is included in traces.",
    privacyUrl: "https://pydantic.dev/privacy",
    termsUrl: "https://pydantic.dev/terms",
  },
];

export default function ThirdPartyServicesPage() {
  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <BugViperFullLogo />
        </Link>
        <div className="flex items-center gap-3">
          <Link
            href="/login"
            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            Sign in
          </Link>
          <Link href="/login">
            <Button size="sm" variant="outline">
              Get Started
            </Button>
          </Link>
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 px-4 py-12">
        <div className="max-w-3xl mx-auto space-y-10">
          {/* Title */}
          <div className="space-y-2">
            <h1 className="text-3xl font-bold tracking-tight">Third-Party Services</h1>
            <p className="text-sm text-muted-foreground">Last updated: {LAST_UPDATED}</p>
          </div>

          <p className="text-muted-foreground leading-relaxed">
            BugViper integrates with a number of third-party services to deliver its features.
            This page lists every external service we use, what data is shared with each, and
            links to their privacy policies and terms of service. We are committed to using only
            reputable providers and to minimising the data shared with each.
          </p>

          {/* Services list */}
          <div className="space-y-6">
            {SERVICES.map((s) => (
              <ServiceCard key={s.name} service={s} />
            ))}
          </div>

          {/* Note */}
          <div className="rounded-lg border border-border bg-muted/40 px-5 py-4 space-y-1.5">
            <p className="text-sm font-medium">Data minimisation commitment</p>
            <p className="text-sm text-muted-foreground leading-relaxed">
              We only send the minimum data required to each provider. Source code is never
              stored by LLM providers — it is sent only at inference time for the review request.
              We do not use your code to train any models. Observatory traces never contain raw
              source code.
            </p>
          </div>

          <p className="text-sm text-muted-foreground">
            Questions? See our{" "}
            <Link href="/privacy" className="underline hover:text-foreground">
              Privacy Policy
            </Link>
            , our{" "}
            <Link href="/terms" className="underline hover:text-foreground">
              Terms of Service
            </Link>
            , or{" "}
            <Link href="/support" className="underline hover:text-foreground">
              contact support
            </Link>
            .
          </p>
        </div>
      </main>

      <Footer />
    </div>
  );
}

function ServiceCard({ service }: { service: Service }) {
  return (
    <div className="rounded-lg border border-border p-5 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-base font-semibold">{service.name}</h2>
        <div className="flex items-center gap-3 text-xs">
          <a
            href={service.privacyUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground underline transition-colors"
          >
            Privacy Policy
          </a>
          <span className="text-border">|</span>
          <a
            href={service.termsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground underline transition-colors"
          >
            Terms of Service
          </a>
        </div>
      </div>

      <div className="space-y-1.5">
        <Row label="Purpose" value={service.purpose} />
        <Row label="Data shared" value={service.dataShared} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[90px_1fr] gap-2 text-sm">
      <span className="text-muted-foreground font-medium shrink-0">{label}</span>
      <span className="text-muted-foreground leading-relaxed">{value}</span>
    </div>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border px-6 py-6">
      <div className="max-w-3xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>© {new Date().getFullYear()} BugViper. All rights reserved.</span>
        <div className="flex items-center gap-4">
          <Link href="/privacy" className="hover:text-foreground transition-colors">
            Privacy Policy
          </Link>
          <Link href="/terms" className="hover:text-foreground transition-colors">
            Terms of Service
          </Link>
          <Link href="/third-party" className="hover:text-foreground transition-colors font-medium text-foreground">
            Third-Party Services
          </Link>
          <Link href="/support" className="hover:text-foreground transition-colors">
            Support
          </Link>
        </div>
      </div>
    </footer>
  );
}
