import Link from "next/link";
import { BugViperFullLogo } from "@/components/logo";
import { Button } from "@/components/ui/button";

export const metadata = {
  title: "Terms of Service — BugViper",
  description: "The terms governing your use of the BugViper platform.",
};

const LAST_UPDATED = "March 10, 2026";

export default function TermsOfServicePage() {
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
            <h1 className="text-3xl font-bold tracking-tight">Terms of Service</h1>
            <p className="text-sm text-muted-foreground">Last updated: {LAST_UPDATED}</p>
          </div>

          <p className="text-muted-foreground leading-relaxed">
            These Terms of Service (&quot;Terms&quot;) govern your access to and use of BugViper
            (&quot;Service&quot;), operated by BugViper (&quot;we&quot;, &quot;us&quot;, or &quot;our&quot;). By using the
            Service you agree to be bound by these Terms. If you do not agree, do not use the
            Service.
          </p>

          <Section title="1. Eligibility">
            <p className="text-muted-foreground leading-relaxed">
              You must be at least 13 years old to use BugViper. By using the Service you
              represent that you meet this requirement and that all information you provide is
              accurate and complete.
            </p>
          </Section>

          <Section title="2. Accounts">
            <p className="text-muted-foreground leading-relaxed">
              You sign in via GitHub OAuth. You are responsible for maintaining the security of
              your GitHub credentials and for all activity that occurs under your account. Notify
              us immediately at{" "}
              <a href="mailto:support@bugviper.dev" className="underline hover:text-foreground">
                support@bugviper.dev
              </a>{" "}
              if you suspect unauthorised access.
            </p>
          </Section>

          <Section title="3. Acceptable Use">
            <p className="text-muted-foreground leading-relaxed">You agree not to:</p>
            <ul className="list-disc list-inside space-y-2 text-muted-foreground leading-relaxed mt-2">
              <li>Use the Service for any unlawful purpose or in violation of any regulations.</li>
              <li>
                Reverse-engineer, decompile, or otherwise attempt to extract source code from
                BugViper.
              </li>
              <li>
                Scrape, crawl, or automatically query the Service in a way that places
                unreasonable load on our infrastructure.
              </li>
              <li>
                Attempt to gain unauthorised access to any part of the Service or its underlying
                systems.
              </li>
              <li>
                Upload or connect repositories that contain malware, illegal content, or content
                that violates third-party intellectual property rights.
              </li>
              <li>Resell or sublicense access to the Service without our written consent.</li>
            </ul>
          </Section>

          <Section title="4. Intellectual Property">
            <SubSection heading="4.1 Your Code">
              You retain all intellectual property rights in the source code you connect to
              BugViper. We claim no ownership over your repositories. By connecting a repository
              you grant us a limited licence to process and analyse that code solely to provide
              the Service.
            </SubSection>
            <SubSection heading="4.2 Our Platform">
              BugViper, its logos, UI, and underlying technology are our exclusive intellectual
              property. Nothing in these Terms grants you a licence to our intellectual property
              beyond the right to use the Service as described herein.
            </SubSection>
          </Section>

          <Section title="5. Third-Party Services">
            <p className="text-muted-foreground leading-relaxed">
              BugViper integrates with third-party services including GitHub, Firebase, Neo4j
              Aura, and LLM providers (via OpenRouter). Your use of those services is governed by
              their respective terms. See our{" "}
              <Link href="/third-party" className="underline hover:text-foreground">
                Third-Party Services
              </Link>{" "}
              page for a full list.
            </p>
          </Section>

          <Section title="6. Payments and Billing">
            <p className="text-muted-foreground leading-relaxed">
              BugViper is currently available in a free alpha. Pricing for paid plans will be
              announced before any charges are applied. All fees, if introduced, will be stated
              clearly on our pricing page. Refunds are evaluated on a case-by-case basis — contact{" "}
              <a href="mailto:billing@bugviper.dev" className="underline hover:text-foreground">
                billing@bugviper.dev
              </a>
              .
            </p>
          </Section>

          <Section title="7. Disclaimer of Warranties">
            <p className="text-muted-foreground leading-relaxed">
              THE SERVICE IS PROVIDED &quot;AS IS&quot; AND &quot;AS AVAILABLE&quot; WITHOUT WARRANTIES OF ANY
              KIND, EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF
              MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE DO NOT
              WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR THAT REVIEW RESULTS
              WILL BE ACCURATE OR COMPLETE.
            </p>
            <p className="text-muted-foreground leading-relaxed mt-2">
              AI-generated code review comments are provided for informational purposes only.
              You are solely responsible for validating and acting on review findings.
            </p>
          </Section>

          <Section title="8. Limitation of Liability">
            <p className="text-muted-foreground leading-relaxed">
              TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, BUGVIPER SHALL NOT BE LIABLE
              FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES,
              INCLUDING LOSS OF PROFITS, DATA, OR GOODWILL, ARISING OUT OF OR IN CONNECTION WITH
              YOUR USE OF THE SERVICE. OUR TOTAL CUMULATIVE LIABILITY FOR ANY CLAIMS UNDER THESE
              TERMS SHALL NOT EXCEED THE AMOUNT YOU PAID US IN THE 12 MONTHS PRIOR TO THE CLAIM.
            </p>
          </Section>

          <Section title="9. Indemnification">
            <p className="text-muted-foreground leading-relaxed">
              You agree to indemnify and hold harmless BugViper and its officers, directors,
              employees, and agents from any claims, damages, losses, or expenses (including
              reasonable legal fees) arising out of your use of the Service, your violation of
              these Terms, or your infringement of any third-party rights.
            </p>
          </Section>

          <Section title="10. Termination">
            <p className="text-muted-foreground leading-relaxed">
              We may suspend or terminate your access to the Service at any time, with or without
              notice, if we reasonably believe you have violated these Terms. You may stop using
              the Service at any time. Sections 4, 7, 8, 9, and 11 survive termination.
            </p>
          </Section>

          <Section title="11. Governing Law">
            <p className="text-muted-foreground leading-relaxed">
              These Terms are governed by and construed in accordance with the laws of the
              jurisdiction in which BugViper operates, without regard to conflict of law
              principles. Any disputes shall be resolved exclusively in the courts of that
              jurisdiction.
            </p>
          </Section>

          <Section title="12. Changes to These Terms">
            <p className="text-muted-foreground leading-relaxed">
              We may update these Terms from time to time. We will notify you of material changes
              by updating the &quot;Last updated&quot; date and, where appropriate, by sending an email
              notification. Continued use of the Service after changes constitutes acceptance of
              the revised Terms.
            </p>
          </Section>

          <Section title="13. Contact">
            <p className="text-muted-foreground leading-relaxed">
              Questions about these Terms? Email us at{" "}
              <a href="mailto:legal@bugviper.dev" className="underline hover:text-foreground">
                legal@bugviper.dev
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
          <Link href="/privacy" className="hover:text-foreground transition-colors">
            Privacy Policy
          </Link>
          <Link href="/terms" className="hover:text-foreground transition-colors font-medium text-foreground">
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
