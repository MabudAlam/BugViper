#!/bin/bash

set -u

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get absolute path to project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Python packages live under src/
export PYTHONPATH="$PROJECT_ROOT/src"

API_PORT="${API_PORT:-8000}"
INGESTION_PORT="${INGESTION_PORT:-8080}"
REVIEW_PORT="${REVIEW_PORT:-8100}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
LINT_HOST_PORT="${LINT_HOST_PORT:-8090}"

NGROK_DOMAIN="${NGROK_DOMAIN:-}"
NGROK_URL="${NGROK_URL:-}"

NGROK_PID=""
NGROK_STARTED=false

# Create logs directory if it doesn't exist
mkdir -p logs

# PID file to track running processes (use absolute path)
PID_FILE="$PROJECT_ROOT/logs/pids.txt"
> "$PID_FILE"

echo -e "${BLUE}Starting BugViper...${NC}\n"

# Avoid accidental use of stale Google ADC paths from the shell.
# If you want Cloud Tasks locally, set GOOGLE_APPLICATION_CREDENTIALS explicitly
# before running this script.
unset GOOGLE_APPLICATION_CREDENTIALS 2>/dev/null || true

if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo -e "${RED}✗ Python virtualenv not found at .venv${NC}"
    echo -e "  Create it with: ${YELLOW}uv sync${NC}"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo -e "${YELLOW}Warning: npm not found. Frontend will not start.${NC}"
    FRONTEND_AVAILABLE=false
else
    FRONTEND_AVAILABLE=true
fi

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Stopping all services...${NC}"
    if [ -f "$PID_FILE" ]; then
        while read -r pid; do
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
            fi
        done < "$PID_FILE"
        rm "$PID_FILE"
    fi

    # Kill any remaining processes
    pkill -f "uvicorn api.app:app" 2>/dev/null
    pkill -f "uvicorn ingestion_service.app:app" 2>/dev/null
    pkill -f "uvicorn code_review_agent.app:app" 2>/dev/null
    pkill -f "uvicorn lint_service.app:app" 2>/dev/null
    docker stop bugviper-lint 2>/dev/null
    pkill -f "next dev" 2>/dev/null
    pkill -f "ngrok http" 2>/dev/null

    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo -e "${YELLOW}Warning: ngrok not found. Install it from https://ngrok.com/download${NC}"
    echo -e "${YELLOW}Continuing without ngrok...${NC}\n"
    NGROK_AVAILABLE=false
else
    NGROK_AVAILABLE=true
fi

# If NGROK_URL isn't exported, load it from .env (without sourcing the whole file).
if [ -z "$NGROK_URL" ] && [ -f "$PROJECT_ROOT/.env" ]; then
    while IFS= read -r line; do
        case "$line" in
            NGROK_URL=*)
                NGROK_URL="${line#NGROK_URL=}"
                # strip optional surrounding quotes
                NGROK_URL="${NGROK_URL#\"}"
                NGROK_URL="${NGROK_URL%\"}"
                NGROK_URL="${NGROK_URL#\'}"
                NGROK_URL="${NGROK_URL%\'}"
                break
                ;;
        esac
    done < "$PROJECT_ROOT/.env"
fi

# If user provided a fixed NGROK_URL, derive NGROK_DOMAIN so we can start ngrok
# with a reserved domain and avoid random URLs on each run.
if [ -z "$NGROK_DOMAIN" ] && [ -n "$NGROK_URL" ]; then
    _tmp="$NGROK_URL"
    _tmp="${_tmp#https://}"
    _tmp="${_tmp#http://}"
    NGROK_DOMAIN="${_tmp%%/*}"
fi

# Start API
echo -e "${BLUE}[1/6] Starting API server...${NC}"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn api.app:app --host 0.0.0.0 --port "$API_PORT" --reload > "$PROJECT_ROOT/logs/api.log" 2>&1 &
API_PID=$!
echo $API_PID >> "$PID_FILE"
echo -e "${GREEN}✓ API started (PID: $API_PID)${NC}"
echo -e "  Log file: logs/api.log"

# Start Ingestion Service
echo -e "\n${BLUE}[2/6] Starting Ingestion Service...${NC}"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn ingestion_service.app:app --host 0.0.0.0 --port "$INGESTION_PORT" --reload > "$PROJECT_ROOT/logs/ingestion.log" 2>&1 &
INGESTION_PID=$!
echo $INGESTION_PID >> "$PID_FILE"
echo -e "${GREEN}✓ Ingestion Service started (PID: $INGESTION_PID)${NC}"
echo -e "  Log file: logs/ingestion.log"

# Start Review Service
echo -e "\n${BLUE}[3/6] Starting Review Service...${NC}"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn code_review_agent.app:app --host 0.0.0.0 --port "$REVIEW_PORT" --reload > "$PROJECT_ROOT/logs/review.log" 2>&1 &
REVIEW_PID=$!
echo $REVIEW_PID >> "$PID_FILE"
echo -e "${GREEN}✓ Review Service started (PID: $REVIEW_PID)${NC}"
echo -e "  Log file: logs/review.log"

# Start Lint Service (Docker)
echo -e "\n${BLUE}[4/6] Starting Lint Service (Docker)...${NC}"
if command -v docker &> /dev/null && docker info &> /dev/null 2>&1; then
    # Stop any existing container
    docker stop bugviper-lint 2>/dev/null
    docker rm bugviper-lint 2>/dev/null

    # Build the image if it doesn't exist
    if ! docker image inspect bugviper-lint-service &>/dev/null; then
        echo -e "  Building lint service image (first time, ~2 min)..."
        docker build -q -t bugviper-lint-service -f infra/docker/Dockerfile.lint . > "$PROJECT_ROOT/logs/lint-build.log" 2>&1
        if [ $? -ne 0 ]; then
            echo -e "  ${YELLOW}⚠ Lint image build failed — check logs/lint-build.log${NC}"
            LINT_AVAILABLE=false
        else
            LINT_AVAILABLE=true
        fi
    else
        LINT_AVAILABLE=true
    fi

    if [ "$LINT_AVAILABLE" = true ]; then
        docker run -d --name bugviper-lint -p "$LINT_HOST_PORT":8080 bugviper-lint-service > /dev/null 2>&1
        echo -e "${GREEN}✓ Lint Service started (http://localhost:${LINT_HOST_PORT})${NC}"
        echo -e "  ${YELLOW}Add LINT_SERVICE_URL=http://localhost:${LINT_HOST_PORT} to .env to enable linting${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠ Docker not running — lint service skipped${NC}"
    echo -e "  ${YELLOW}  Start Docker Desktop and re-run to enable linting${NC}"
fi

# Start Frontend
echo -e "\n${BLUE}[5/6] Starting Frontend...${NC}"
if [ "$FRONTEND_AVAILABLE" = true ]; then
    cd "$PROJECT_ROOT/apps/frontend"

    FRONTEND_PID=""

    if [ ! -d "node_modules" ]; then
        echo -e "  Installing frontend dependencies (npm ci)..."
        npm ci > "$PROJECT_ROOT/logs/frontend-install.log" 2>&1
        if [ $? -ne 0 ]; then
            echo -e "  ${YELLOW}⚠ Frontend npm ci failed — check logs/frontend-install.log${NC}"
            echo -e "  ${YELLOW}  Frontend skipped${NC}"
        else
            echo -e "  ${GREEN}✓ Frontend deps installed${NC}"
        fi
    fi

    if [ -d "node_modules" ]; then
        # Force webpack dev server to avoid Turbopack panics on some machines.
        NEXT_TELEMETRY_DISABLED=1 PORT="$FRONTEND_PORT" npm run dev -- --webpack > "$PROJECT_ROOT/logs/frontend.log" 2>&1 &
        FRONTEND_PID=$!
    fi

    if [ "$FRONTEND_PID" != "" ]; then
        echo $FRONTEND_PID >> "$PID_FILE"
        echo -e "${GREEN}✓ Frontend started (PID: $FRONTEND_PID)${NC}"
        echo -e "  Log file: logs/frontend.log"
    fi
else
    echo -e "  ${YELLOW}⚠ Frontend skipped (npm not found)${NC}"
fi

# Wait for API to be ready
echo -e "\n${BLUE}Waiting for API to be ready...${NC}"
cd "$PROJECT_ROOT"
for i in {1..30}; do
    if curl -s "http://localhost:${API_PORT}/docs" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ API is ready!${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}✗ API failed to start within 30 seconds${NC}"
        echo -e "${YELLOW}Check logs/api.log for errors${NC}"
        cleanup
    fi
    sleep 1
done

# Start ngrok if available
if [ "$NGROK_AVAILABLE" = true ]; then
    echo -e "\n${BLUE}[6/6] Starting ngrok tunnel...${NC}"
    if [ -n "$NGROK_DOMAIN" ]; then
        ngrok http "$API_PORT" --domain="$NGROK_DOMAIN" > "$PROJECT_ROOT/logs/ngrok.log" 2>&1 &
    else
        ngrok http "$API_PORT" > "$PROJECT_ROOT/logs/ngrok.log" 2>&1 &
    fi
    NGROK_PID=$!
    NGROK_STARTED=true
    echo $NGROK_PID >> "$PID_FILE"
    echo -e "${GREEN}✓ Ngrok started (PID: $NGROK_PID)${NC}"
    echo -e "  Log file: logs/ngrok.log"

    # Wait for ngrok to initialize and get URL
    echo -n "  Waiting for URL..."
    for i in {1..10}; do
        NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*' | grep -o 'https://[^"]*' | head -1)
        if [ -n "$NGROK_URL" ]; then
            echo -e "\r  URL: $NGROK_URL          "
            break
        fi
        sleep 1
    done
    if [ -z "$NGROK_URL" ]; then
        echo -e "\r  ${YELLOW}Could not retrieve ngrok URL${NC}"
    fi
else
    echo -e "\n${YELLOW}[6/6] Skipping ngrok (not installed)${NC}"
fi

# Display summary
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}        BugViper is now running!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo -e "${BLUE}URLs:${NC}"
echo -e "  Frontend:    ${YELLOW}http://localhost:${FRONTEND_PORT}${NC}"
echo -e "  API:         ${YELLOW}http://localhost:${API_PORT}${NC}"
echo -e "  API Docs:    ${YELLOW}http://localhost:${API_PORT}/docs${NC}"
echo -e "  Ingestion:   ${YELLOW}http://localhost:${INGESTION_PORT}${NC}"
echo -e "  Ingest Docs: ${YELLOW}http://localhost:${INGESTION_PORT}/docs${NC}"
echo -e "  Review:      ${YELLOW}http://localhost:${REVIEW_PORT}${NC}"
echo -e "  Review Docs: ${YELLOW}http://localhost:${REVIEW_PORT}/docs${NC}"
echo -e "  Lint:        ${YELLOW}http://localhost:${LINT_HOST_PORT}${NC}"

if [ "$NGROK_AVAILABLE" = true ] && [ -n "$NGROK_URL" ]; then
    echo -e "  Ngrok:       ${YELLOW}$NGROK_URL${NC}"
    if [ "$NGROK_STARTED" = true ]; then
        echo -e "  Ngrok Admin: ${YELLOW}http://localhost:4040${NC}"
    fi
    echo -e "  Webhook:     ${YELLOW}${NGROK_URL%/}/api/v1/webhook/onComment${NC}"
fi

echo -e "\n${BLUE}View Logs:${NC}"
echo -e "  API:         ${YELLOW}tail -f logs/api.log${NC}"
echo -e "  Ingestion:   ${YELLOW}tail -f logs/ingestion.log${NC}"
echo -e "  Frontend:    ${YELLOW}tail -f logs/frontend.log${NC}"
if [ "$NGROK_STARTED" = true ]; then
    echo -e "  Ngrok:       ${YELLOW}tail -f logs/ngrok.log${NC}"
fi
echo -e "  All:         ${YELLOW}tail -f logs/*.log${NC}"

echo -e "\n${BLUE}Process IDs:${NC}"
echo -e "  API:         ${YELLOW}$API_PID${NC}"
echo -e "  Ingestion:   ${YELLOW}$INGESTION_PID${NC}"
echo -e "  Review:      ${YELLOW}$REVIEW_PID${NC}"
if [ "${FRONTEND_PID:-}" != "" ]; then
    echo -e "  Frontend:    ${YELLOW}$FRONTEND_PID${NC}"
fi
if [ "$NGROK_STARTED" = true ] && [ "$NGROK_PID" != "" ]; then
    echo -e "  Ngrok:       ${YELLOW}$NGROK_PID${NC}"
fi

echo -e "\n${RED}Press Ctrl+C to stop all services${NC}\n"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# Keep this script running so background processes don't get terminated
# when the non-interactive shell exits. Also detects crashes.
while true; do
    sleep 2

    for pid in "$API_PID" "$INGESTION_PID" "$REVIEW_PID"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo -e "\n${RED}✗ A service process exited (PID: $pid). Stopping all services...${NC}"
            cleanup
        fi
    done

    if [ "${FRONTEND_PID:-}" != "" ] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo -e "\n${RED}✗ Frontend process exited (PID: $FRONTEND_PID). Stopping all services...${NC}"
        cleanup
    fi

    if [ "$NGROK_STARTED" = true ] && [ "$NGROK_PID" != "" ] && ! kill -0 "$NGROK_PID" 2>/dev/null; then
        echo -e "\n${RED}✗ Ngrok process exited (PID: $NGROK_PID). Stopping all services...${NC}"
        cleanup
    fi
done
