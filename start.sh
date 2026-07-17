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
REVIEW_PORT="${REVIEW_PORT:-8100}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"


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
                wait "$pid" 2>/dev/null
            fi
        done < "$PID_FILE"
        rm "$PID_FILE"
    fi

    # Kill any remaining processes on our ports
    for port in "$API_PORT" "$REVIEW_PORT"  "$FRONTEND_PORT"; do
        lsof -ti :"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
    done


    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Kill existing processes on all ports before starting
kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null)
    if [ -n "$pids" ]; then
        echo -e "  Killing existing process on port $port (PID: $(echo $pids | tr '\n' ' '))"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.5
    fi
}

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
echo -e "${BLUE}[1/5] Starting API server...${NC}"
kill_port "$API_PORT"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn api.app:app --host 0.0.0.0 --port "$API_PORT" --reload --reload-dir "$PROJECT_ROOT/src/api" --reload-dir "$PROJECT_ROOT/src/common" --reload-dir "$PROJECT_ROOT/src/ncodereview" > "$PROJECT_ROOT/logs/api.log" 2>&1 &
API_PID=$!
echo $API_PID >> "$PID_FILE"
echo -e "${GREEN}✓ API started (PID: $API_PID)${NC}"
echo -e "  Log file: logs/api.log"

# Start Review Service
echo -e "\n${BLUE}[2/5] Starting Review Service...${NC}"
kill_port "$REVIEW_PORT"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn ncodereview.app:app --host 0.0.0.0 --port "$REVIEW_PORT" --reload --reload-dir "$PROJECT_ROOT/src/ncodereview" --reload-dir "$PROJECT_ROOT/src/common" > "$PROJECT_ROOT/logs/review.log" 2>&1 &
REVIEW_PID=$!
echo $REVIEW_PID >> "$PID_FILE"
echo -e "${GREEN}✓ Review Service started (PID: $REVIEW_PID)${NC}"
echo -e "  Log file: logs/review.log"

# Start Frontend
echo -e "\n${BLUE}[3/5] Starting Frontend...${NC}"
kill_port "$FRONTEND_PORT"
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
    echo -e "\n${BLUE}[4/5] Starting ngrok tunnel...${NC}"
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
    echo -e "\n${YELLOW}[4/5] Skipping ngrok (not installed)${NC}"
fi

# Display summary
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}        BugViper is now running!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo -e "${BLUE}URLs:${NC}"
echo -e "  Frontend:    ${YELLOW}http://localhost:${FRONTEND_PORT}${NC}"
echo -e "  API:         ${YELLOW}http://localhost:${API_PORT}${NC}"
echo -e "  API Docs:    ${YELLOW}http://localhost:${API_PORT}/docs${NC}"
echo -e "  Review:      ${YELLOW}http://localhost:${REVIEW_PORT}${NC}"
echo -e "  Review Docs: ${YELLOW}http://localhost:${REVIEW_PORT}/docs${NC}"

if [ "$NGROK_AVAILABLE" = true ] && [ -n "$NGROK_URL" ]; then
    echo -e "  Ngrok:       ${YELLOW}$NGROK_URL${NC}"
    if [ "$NGROK_STARTED" = true ]; then
        echo -e "  Ngrok Admin: ${YELLOW}http://localhost:4040${NC}"
    fi
    echo -e "  Webhook:     ${YELLOW}${NGROK_URL%/}/api/v1/webhook/onComment${NC}"
fi

echo -e "\n${BLUE}View Logs:${NC}"
echo -e "  API:         ${YELLOW}tail -f logs/api.log${NC}"
echo -e "  Frontend:    ${YELLOW}tail -f logs/frontend.log${NC}"
if [ "$NGROK_STARTED" = true ]; then
    echo -e "  Ngrok:       ${YELLOW}tail -f logs/ngrok.log${NC}"
fi
echo -e "  All:         ${YELLOW}tail -f logs/*.log${NC}"

echo -e "\n${BLUE}Process IDs:${NC}"
echo -e "  API:         ${YELLOW}$API_PID${NC}"
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

    for pid in "$API_PID" "$REVIEW_PID"; do
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
        echo -e "\n${YELLOW}⚠ Ngrok tunnel exited (PID: $NGROK_PID). Webhook URL will not be available.${NC}"
        NGROK_STARTED=false
        NGROK_PID=""
    fi
done
