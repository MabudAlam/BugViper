# BugViper Local Development Setup

This guide walks contributors through setting up BugViper on their local machine.

---

## Prerequisites

- [Python 3.13+](https://www.python.org/downloads/)
- [Node.js 20+](https://nodejs.org/)
- [Docker](https://docs.docker.com/get-docker/)
- [uv](https://docs.astral.sh/uv/installation/) — fast Python package manager
- [Neo4j](https://neo4j.com/docs/quick-start/4.0/start-position/) (local or Aura)
- [ngrok](https://ngrok.com/download) — for local webhook testing
- A code editor (VS Code recommended)

---

## 1. Clone the Repository

```bash
git clone https://github.com/MabudAlam/BugViper.git
cd BugViper
```

---

## 2. Install Dependencies

### Python (backend)

```bash
uv sync
```

### Frontend

```bash
cd apps/frontend
npm install
cd ../..
```

---

## 3. Environment Configuration

### Copy the example env file

```bash
cp .env.example .env
```

### Fill in all required values

Open `.env` and set each variable:

#### 3.1 Neo4j Database

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_neo4j_password
NEO4J_DATABASE=neo4j
```

**For local Neo4j:** Install via [Neo4j Desktop](https://neo4j.com/download/) or Docker:
```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:5
```

**For Neo4j Aura** (cloud): Use the connection string from your Aura dashboard.

#### 3.2 Firebase (Service Account)

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project → **Project Settings** → **Service Accounts**
3. Click **Generate new private key** → save as `service-account.json`
4. Set the path in `.env`:

```env
SERVICE_FILE_LOC=/path/to/your/service-account.json
```

#### 3.3 OpenRouter (LLM)

1. Sign up at [openrouter.ai](https://openrouter.ai/)
2. Get your API key from the dashboard
3. Set in `.env`:

```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
REVIEW_MODEL=anthropic/claude-sonnet-4-5
SYNTHESIS_MODEL=openai/gpt-4o-mini
```

#### 3.4 GitHub App (Webhook + API Access)

You need a GitHub App to:
- Receive webhook events (PR comments, issue triggers)
- Clone repositories
- Post review comments

**Step 1: Create a GitHub App**

1. Go to your GitHub organization settings → **Developer settings** → **GitHub Apps**
2. Click **New GitHub App**
3. Fill in:
   - **GitHub App name**: `bugviper-dev` (must be unique)
   - **Homepage URL**: `http://localhost:3000`
   - **Webhook URL**: `https://your-ngrok-url.ngrok.io` (add this later after ngrok setup)
   - **Webhook secret**: Generate a random string, e.g., `openssl rand -hex 20`
   - **Permissions** (set under Permissions & events):
     - **Repository permissions**:
       - Contents: Read
       - Issues: Read & Write
       - Pull requests: Read & Write
       - Metadata: Read
       - Commit comments: Read & Write
       - GitHub Actions: Read
4. Click **Create GitHub App**
5. Generate and download the **private key** (`.pem` file)

**Step 2: Install the App**

1. In your GitHub App settings, click **Install App**
2. Install it on your GitHub account or organization
3. Grant access to repositories you want to test with

**Step 3: Get the App ID**

1. In your GitHub App settings, find **App ID** (shown at the top)
2. Set in `.env`:

```env
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_PATH=/path/to/your-app.private-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret_here
```

#### 3.5 Firebase Authentication (GitHub OAuth)

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project → **Authentication** → **Sign-in method**
3. Click **GitHub**
4. Fill in:
   - **Client ID**: From your GitHub OAuth App (see below)
   - **Client Secret**: From your GitHub OAuth App
5. Click **Save**

**Creating a GitHub OAuth App:**

1. Go to your GitHub account → **Settings** → **Developer settings** → **OAuth Apps**
2. Click **New OAuth App**
3. Fill in:
   - **Application name**: `BugViper Dev`
   - **Homepage URL**: `http://localhost:3000`
   - **Authorization callback URL**: `https://localhost:3000/api/auth/callback/github`
4. Click **Register application**
5. Copy the **Client ID** and generate a **Client Secret**

#### 3.6 Ngrok (Local Webhook Tunneling)

**Required for:** Receiving GitHub webhooks on your local machine

1. Sign up at [ngrok.com](https://ngrok.com/) (free tier works)
2. Download and install ngrok
3. Authenticate: `ngrok config add-authtoken YOUR_TOKEN`
4. Start ngrok for port 3000:
   ```bash
   ngrok http 3000 --domain=your-reserved-domain.ngrok-free.app
   ```
   (Reserve a domain in ngrok dashboard for stable webhooks)

5. Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`)
6. Update your GitHub App webhook URL with this ngrok URL
7. Set in `.env` (optional):

```env
NGROK_DOMAIN=your-reserved-domain.ngrok-free.app
```

#### 3.7 Optional: Cloud Tasks (Async Processing)

For production-like async ingestion and review queue:

```env
CLOUD_TASKS_QUEUE=ingestion-queue
CLOUD_TASKS_REVIEW_QUEUE=codeReview
INGESTION_SERVICE_URL=http://localhost:8080
REVIEW_SERVICE_URL=http://localhost:8100
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=us-central1
CLOUD_TASKS_SA_EMAIL=your-sa@your-project.iam.gserviceaccount.com
```

Leave unset for local dev (services call each other directly via HTTP).

---

## 4. Verify Your Setup

### Test environment variables

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('NEO4J_URI:', os.getenv('NEO4J_URI'))"
```

### Test Docker builds

```bash
docker compose build
```

### Run all services

```bash
docker compose up
```

Visit:
- Frontend: http://localhost:3000
- API: http://localhost:8000
- API docs: http://localhost:8000/docs
- Ingestion: http://localhost:8080
- Review: http://localhost:8100
- Lint: http://localhost:8090

---

## 5. Firebase Admin SDK

The `SERVICE_FILE_LOC` points to a Firebase service account JSON file. This enables:
- User management (Firestore)
- PR review storage
- Repository metadata storage

**Never commit this file** — it's already in `.gitignore`.

---

## 6. Troubleshooting

### "Firebase not initialized"

Ensure `SERVICE_FILE_LOC` points to a valid service account JSON.

### "Neo4j connection refused"

Make sure Neo4j is running:
```bash
docker ps | grep neo4j
```

### "GitHub webhook not received"

1. Check ngrok is running: `curl https://your-ngrok-url.ngrok.io/health`
2. Verify GitHub App webhook URL matches ngrok URL
3. Check GitHub App has correct permissions

### "Module not found"

```bash
uv sync
```

---

## 7. Project Structure

```
BugViper/
├── apps/frontend/          # Next.js frontend
├── src/
│   ├── api/               # FastAPI backend
│   ├── common/            # Shared utilities
│   ├── db/                # Neo4j client
│   ├── code_review_agent/ # LangGraph review agent
│   ├── ingestion_service/ # Repository ingestion
│   └── lint_service/      # Multi-language linting
├── infra/
│   ├── docker/            # Dockerfiles
│   └── cloudbuild/        # Cloud Build configs
├── docker-compose.yml
├── cloudbuild.yaml
├── .env.example
└── SETUP.md
```

---

## 8. Quick Reference

| Service | Port | URL |
|---------|------|-----|
| Frontend | 3000 | http://localhost:3000 |
| API | 8000 | http://localhost:8000 |
| Ingestion | 8080 | http://localhost:8080 |
| Review | 8100 | http://localhost:8100 |
| Lint | 8090 | http://localhost:8090 |
| Neo4j Browser | 7474 | http://localhost:7474 |

| Environment Variable | Description |
|---------------------|-------------|
| `OPENROUTER_API_KEY` | LLM API key |
| `GITHUB_APP_ID` | GitHub App ID |
| `GITHUB_PRIVATE_KEY_PATH` | Path to `.pem` file |
| `GITHUB_WEBHOOK_SECRET` | Webhook verification secret |
| `SERVICE_FILE_LOC` | Firebase service account JSON |
| `NEO4J_*` | Neo4j connection settings |
| `LINT_SERVICE_URL` | URL of lint service |
| `INGESTION_SERVICE_URL` | URL of ingestion service |
| `REVIEW_SERVICE_URL` | URL of review service |
