# BugViper

AI PR Reviews with codebase-aware DeepAgents + ESLint, Ruff, golangci-lint and more. Install the GitHub App, open a PR, and BugViper clones the repo, builds a blast radius and call graph on the fly in an E2B sandbox, then posts a full review — inline comments, issue tracking, and a summary — with no friction.

---

## Features

| | |
|---|---|
| 🤖 **AI DeepAgents** | Codebase-aware agents that understand cross-file function interactions, not just isolated snippets |
| 🫡 **Normal Mode** | Single generalist agent — fast, efficient reviews for everyday PRs |
| 🔬 **Deep Mode** | Three specialized sub-agents (correctness, security, performance) run in parallel for thorough analysis |
| 🏝️ **E2B Sandboxes** | Secure isolated sandboxes to clone and review code — no access to your environment |
| 🔍 **Blast Radius + Call Graph** | Builds call graph on the fly so agents see the real impact of every change |
| 🔧 **Third Party Static Analysis** | ESLint, Ruff, golangci-lint — fast lint-only mode with support for Python, JS/TS, Go, and more |
| ✅ **Verifier Pass** | Validates every finding against the actual diff — negates false positives and marks issues as: `nitpick` (low confidence), `valid` (high confidence), or `outside_diff` (not in the PR) |
| 🔀 **Dedup** | Deduplicates findings across batches and agents — same issue reported twice gets merged once |
| 🧠 **Any LLM Model** | Use any model — open source or closed source — via OpenRouter, Gemini, or MiniMax |
| 💬 **Inline Comments** | Posts high-confidence issues directly on the relevant lines in the PR |
| 📊 **Analytics Dashboard** | Tracks bugs caught, resolved, merge times, and PRs reviewed per day |
| 🔁 **Resolve Detection** | Tracks whether previously reported issues were fixed in subsequent PRs — automatically marks issues as `fixed` when the same issue no longer appears |
| 📈 **Per-Repo Analytics** | Stacked bar charts per repo for reviews per day, PRs reviewed, avg merge time, and addressed rate |
| 🐛 **Issue Lifecycle** | Full issue tracking — open, resolved, fixed — with per-issue GitHub comment linkage |
| ☁️ **Cloud Run + Cloud Tasks** | Production-ready deploy on GCP with async task dispatch |

---

## Coming Soon

| | |
|---|---|
| 🧠 **Global Knowledgebase** | Agents learn from review feedback across all repos — common issues and patterns get smarter over time |
| 🌍 **Repo Knowledgebase** | Per-repo memory so agents remember past issues and don't re-report the same things |
| 🛠️ **More Static Analysis** | Adding more third-party tools: Hadolint, ShellCheck, Terraform (HCL), and more |

---

## Architecture

```mermaid
flowchart LR
    GH["GitHub<br/>Webhooks & PRs"]

    subgraph API["API Service (FastAPI) — Port 8000"]
        WH[Webhook Router] --> RT[Review Tasks]
        WH --> IT[Ingestion Tasks]
        CT[Cloud Tasks<br/>Dispatcher]
    end

    subgraph Queue["Cloud Tasks Queue"]
        Q1["codeReview<br/>Queue"]
        Q2["ingestion-queue"]
    end

    subgraph Review["Review Service (Cloud Run) — Port 8100"]
        E2B[E2B Sandbox]
        CG[Call Graph<br/>tree-sitter]
        NORM[Normalize<br/>+ Dedup]
        VERT[Verifier Pass]
        SUB3[3 Sub-Agents<br/>Correctness·Security·Performance]
    end

    subgraph Lint["Lint Service"]
        LINT[ESLint · Ruff<br/>golangci-lint]
    end

    GH --> WH
    RT -->|DEBUG=true| E2B
    RT -->|DEBUG=false| CT
    CT --> Q1
    Q1 --> E2B
    IT -->|DEBUG=false| CT
    CT --> Q2

    E2B --> CG
    CG --> SUB3
    SUB3 --> VERT
    VERT --> NORM
    NORM --> GH

    RT --> LINT
    LINT --> GH

    subgraph Data["Data Layer"]
        FS[Firestore<br/>Users · PRs · Reviews<br/>Analytics]
        N4J[Neo4j<br/>Code Graph<br/>Call Graph]
    end

    E2B --> N4J
    NORM --> FS

    subgraph Dashboard["Frontend (Next.js) — Port 3000"]
        REPO[Repositories]
        ANAL[Analytics<br/>Charts]
    end

    FS --> ANAL
    FS --> REPO
```

### Service Overview

| Service | Port | Role |
|---------|------|------|
| **API** | 8000 | Receives GitHub webhooks, dispatches review & ingestion tasks, serves REST endpoints |
| **Review** | 8100 | Executes the AI review pipeline — clones repo, builds call graph, runs agents, posts comments |
| **Frontend** | 3000 | Next.js dashboard — analytics charts, repo management, tools config |

### Data Flow

1. **GitHub webhook** fires on PR event → hits API
2. **DEBUG=true** → direct HTTP to Review service; **DEBUG=false** → Cloud Tasks queues the job
3. **Review service** clones the PR head into an **E2B sandbox**, builds a **call graph** via tree-sitter, then runs **DeepAgent** (normal or deep mode)
4. **Verifier pass** validates every finding against the actual diff, marking as `nitpick` / `valid` / `outside_diff`
5. **Dedup** merges duplicate findings across batches
6. **GitHub** receives inline comments + PR review body; **Firestore** stores issues and analytics; **Neo4j** updates the code graph

---

## Full Review

> Comment `@bugviper full review` on any PR and get a complete AI-powered code review — inline comments, issue tracking, and a summary posted back to GitHub.

**Demo Video:** [Full Review Walkthrough](./screenshots/video/Full%20Review.mp4)
<video src="./screenshots/video/Full Review.mp4" width="100%" controls></video>

### How it works

1. **Trigger** — User comments `@bugviper full review` on a GitHub PR
2. **Clone + Blast Radius** — BugViper clones the repo at the PR head SHA into an E2B sandbox and generates a full call graph via tree-sitter to understand which files call which functions
3. **Scoring + Batching** — Every changed file is scored for its blast radius (how many downstream callers it affects). Files are then grouped into small batches using Louvain community detection so that tightly-connected files are reviewed together
4. **Agent Review** — In **Normal mode**, batches of 4 run sequentially through a single generalist agent. In **Deep mode**, batches of 2 run through 3 specialized sub-agents in parallel (Bug, Security, Performance), each in their own isolated E2B container
5. **Verifier Pass** — Every finding from the agents is validated against the actual diff by a verifier agent. It removes false positives and classifies each issue as `valid`, `nitpick` (low confidence), or `outside_diff`
6. **Dedup + Normalize** — Duplicate findings across batches and agents are merged into a single entry
7. **Post to GitHub** — High-confidence `valid` issues are posted as inline comments on the relevant lines; the full summary is posted as a PR review body

> **Real-world example:** A large PR with **58 files** was reviewed by splitting it into batches based on connected blast-radius components. In Deep mode, 3 agents (Bug, Security, Performance) ran in parallel across 2 batches at a time inside isolated E2B containers, with the verifier removing false positives before any comments were posted.

---

## Run Lint

> Comment `@bugviper lint` on any PR for a fast static-analysis review using ESLint, Ruff, golangci-lint, and more — results posted back to GitHub in seconds.

**Demo Video:** [Run Lint Walkthrough](./screenshots/video/Run%20Lint.mp4)
<video src="./screenshots/video/Run Lint.mp4" width="100%" controls></video>

### How it works

1. **Trigger** — User comments `@bugviper lint` on a GitHub PR
2. **Queue** — The request is dispatched to the review service via Cloud Tasks (or direct HTTP in local dev with `DEBUG=true`)
3. **Lint Execution** — The configured linters (ESLint, Ruff, golangci-lint) run against the changed files in an isolated E2B sandbox
4. **Parse + Filter** — Lint results are parsed and filtered to only issues that fall within the actual PR diff
5. **Post to GitHub** — Findings are posted as inline comments on the relevant lines, with a summary comment on the PR

> **Note:** Run Lint is designed for speed — no call graph, no AI agents, no batching. It's the fastest way to catch style violations, unused imports, and standard lint errors before merging.
