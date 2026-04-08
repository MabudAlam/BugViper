import Link from "next/link";
import { BugViperFullLogo } from "@/components/logo";
import { Button } from "@/components/ui/button";

export const metadata = {
  title: "Privacy Policy — BugViper",
  description: "How BugViper collects, uses, and protects your data.",
};

const LAST_UPDATED = "March 10, 2026";

export default function PrivacyPolicyPage() {
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
            <h1 className="text-3xl font-bold tracking-tight">Privacy Policy</h1>
            <p className="text-sm text-muted-foreground">Last updated: {LAST_UPDATED}</p>
          </div>

          <p className="text-muted-foreground leading-relaxed">
            BugViper (&quot;we&quot;, &quot;our&quot;, or &quot;us&quot;) is committed to protecting your personal
            information. This Privacy Policy explains what data we collect, how we use it, and
            your rights regarding that data when you use our platform at{" "}
            <span className="font-medium text-foreground">bugviper.dev</span>.
          </p>

          <Section title="1. Information We Collect">
            <SubSection heading="1.1 Account Information">
              When you sign in with GitHub, we receive your GitHub profile data including your
              name, email address, avatar URL, and GitHub username. We store this information in
              Firebase Authentication and Firestore to manage your account.
            </SubSection>
            <SubSection heading="1.2 Repository Data">
              To provide code analysis, BugViper reads the source code of repositories you
              explicitly connect. This data is processed to build a graph representation stored
              in our Neo4j database. We do not share your source code with third parties beyond
              the LLM providers listed in our{" "}
              <Link href="/third-party" className="underline hover:text-foreground">
                Third-Party Services
              </Link>{" "}
              page.
            </SubSection>
            <SubSection heading="1.3 Usage Data">
              We collect anonymised telemetry data (page views, feature usage, error logs) to
              improve the product. This data does not include source code or personal identifiers.
            </SubSection>
            <SubSection heading="1.4 Communications">
              If you contact us via the{" "}
              <Link href="/support" className="underline hover:text-foreground">
                support form
              </Link>
              , we collect your name, email, and message content to respond to your inquiry.
            </SubSection>
          </Section>

          <Section title="2. How We Use Your Data">
            <ul className="list-disc list-inside space-y-2 text-muted-foreground leading-relaxed">
              <li>Authenticate your identity and manage your account.</li>
              <li>Analyse code repositories you connect to provide review results.</li>
              <li>Send transactional notifications about review completions and errors.</li>
              <li>Improve our models, search quality, and platform features.</li>
              <li>Respond to support requests you submit.</li>
              <li>Comply with legal obligations.</li>
            </ul>
          </Section>

          <Section title="3. Data Sharing">
            <p className="text-muted-foreground leading-relaxed">
              We do not sell your personal data. We share data only with:
            </p>
            <ul className="list-disc list-inside space-y-2 text-muted-foreground leading-relaxed mt-3">
              <li>
                <span className="font-medium text-foreground">LLM providers</span> (e.g.
                OpenRouter) — code diffs and context are sent to generate review comments.
              </li>
              <li>
                <span className="font-medium text-foreground">Firebase / Google Cloud</span> —
                authentication, database storage, and infrastructure.
              </li>
              <li>
                <span className="font-medium text-foreground">GitHub</span> — to fetch
                repository content and post PR review comments.
              </li>
              <li>
                <span className="font-medium text-foreground">Logfire / Pydantic</span> —
                observability and error tracing (if enabled).
              </li>
            </ul>
            <p className="text-muted-foreground leading-relaxed mt-3">
              See our{" "}
              <Link href="/third-party" className="underline hover:text-foreground">
                Third-Party Services
              </Link>{" "}
              page for full details.
            </p>
          </Section>

          <Section title="4. Data Retention">
            <p className="text-muted-foreground leading-relaxed">
              Repository graph data is retained for as long as the repository remains connected
              to BugViper. You can delete a repository at any time from the dashboard, which
              permanently removes all associated graph nodes and metadata. Account data is
              retained until you request deletion. Support query records are retained for up to
              2 years.
            </p>
          </Section>

          <Section title="5. Security">
            <p className="text-muted-foreground leading-relaxed">
              We use industry-standard practices including TLS encryption in transit, Firebase
              Authentication for identity management, and role-based access controls on our
              database infrastructure. No system is 100% secure; we encourage you to use strong
              passwords and keep your GitHub tokens confidential.
            </p>
          </Section>

          <Section title="6. Your Rights">
            <ul className="list-disc list-inside space-y-2 text-muted-foreground leading-relaxed">
              <li>
                <span className="font-medium text-foreground">Access</span> — request a copy of
                the personal data we hold about you.
              </li>
              <li>
                <span className="font-medium text-foreground">Correction</span> — ask us to
                correct inaccurate data.
              </li>
              <li>
                <span className="font-medium text-foreground">Deletion</span> — request
                permanent removal of your account and associated data.
              </li>
              <li>
                <span className="font-medium text-foreground">Portability</span> — request an
                export of your data in a machine-readable format.
              </li>
              <li>
                <span className="font-medium text-foreground">Objection</span> — object to
                certain processing activities.
              </li>
            </ul>
            <p className="text-muted-foreground leading-relaxed mt-3">
              To exercise any of these rights, contact us at{" "}
              <a href="mailto:privacy@bugviper.dev" className="underline hover:text-foreground">
                privacy@bugviper.dev
              </a>
              .
            </p>
          </Section>

          <Section title="7. Cookies">
            <p className="text-muted-foreground leading-relaxed">
              BugViper uses session storage (not third-party cookies) to keep you signed in
              during a browser session. We do not use advertising or tracking cookies. Firebase
              Authentication may set cookies for session management on your behalf.
            </p>
          </Section>

          <Section title="8. Children's Privacy">
            <p className="text-muted-foreground leading-relaxed">
              BugViper is not directed at children under 13. We do not knowingly collect
              personal information from children. If you believe a child has provided us with
              personal data, please contact us and we will delete it promptly.
            </p>
          </Section>

          <Section title="9. Changes to This Policy">
            <p className="text-muted-foreground leading-relaxed">
              We may update this Privacy Policy from time to time. We will notify you of
              material changes by updating the &quot;Last updated&quot; date at the top of this page.
              Continued use of BugViper after changes constitutes acceptance of the revised
              policy.
            </p>
          </Section>

          <Section title="10. Contact">
            <p className="text-muted-foreground leading-relaxed">
              Questions about this Privacy Policy? Email us at{" "}
              <a href="mailto:privacy@bugviper.dev" className="underline hover:text-foreground">
                privacy@bugviper.dev
              </a>{" "}
              or use our{" "}
              <Link href="/support" className="underline hover:text-foreground">
                support form
              </Link>
              .
            </p>
          </Section>
        </div>
      </main>

      <Footer />
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function SubSection({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-semibold text-foreground">{heading}</h3>
      <p className="text-muted-foreground leading-relaxed">{children}</p>
    </div>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border px-6 py-6">
      <div className="max-w-3xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>© {new Date().getFullYear()} BugViper. All rights reserved.</span>
        <div className="flex items-center gap-4">
          <Link href="/privacy" className="hover:text-foreground transition-colors font-medium text-foreground">
            Privacy Policy
          </Link>
          <Link href="/terms" className="hover:text-foreground transition-colors">
            Terms of Service
          </Link>
          <Link href="/third-party" className="hover:text-foreground transition-colors">
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
