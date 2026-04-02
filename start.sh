#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get absolute path to project root
PROJECT_ROOT="/Users/skmabudalam/Documents/BugViper"
cd "$PROJECT_ROOT"

# Create logs directory if it doesn't exist
mkdir -p logs

# PID file to track running processes (use absolute path)
PID_FILE="$PROJECT_ROOT/logs/pids.txt"
> "$PID_FILE"

echo -e "${BLUE}Starting BugViper...${NC}\n"

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

# Start API
echo -e "${BLUE}[1/4] Starting API server...${NC}"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload > "$PROJECT_ROOT/logs/api.log" 2>&1 &
API_PID=$!
echo $API_PID >> "$PID_FILE"
echo -e "${GREEN}✓ API started (PID: $API_PID)${NC}"
echo -e "  Log file: logs/api.log"

# Start Ingestion Service
echo -e "\n${BLUE}[2/4] Starting Ingestion Service...${NC}"
cd "$PROJECT_ROOT"

source .venv/bin/activate && uvicorn ingestion_service.app:app --host 0.0.0.0 --port 8080 --reload > "$PROJECT_ROOT/logs/ingestion.log" 2>&1 &
INGESTION_PID=$!
echo $INGESTION_PID >> "$PID_FILE"
echo -e "${GREEN}✓ Ingestion Service started (PID: $INGESTION_PID)${NC}"
echo -e "  Log file: logs/ingestion.log"

# Start Lint Service (Docker)
echo -e "\n${BLUE}[3/5] Starting Lint Service (Docker)...${NC}"
if command -v docker &> /dev/null && docker info &> /dev/null 2>&1; then
    # Stop any existing container
    docker stop bugviper-lint 2>/dev/null
    docker rm bugviper-lint 2>/dev/null

    # Build the image if it doesn't exist
    if ! docker image inspect bugviper-lint-service &>/dev/null; then
        echo -e "  Building lint service image (first time, ~2 min)..."
        docker build -q -t bugviper-lint-service -f Dockerfile.lint . > "$PROJECT_ROOT/logs/lint-build.log" 2>&1
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
        docker run -d --name bugviper-lint -p 8090:8080 bugviper-lint-service > /dev/null 2>&1
        echo -e "${GREEN}✓ Lint Service started (http://localhost:8090)${NC}"
        echo -e "  ${YELLOW}Add LINT_SERVICE_URL=http://localhost:8090 to .env to enable linting${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠ Docker not running — lint service skipped${NC}"
    echo -e "  ${YELLOW}  Start Docker Desktop and re-run to enable linting${NC}"
fi

# Start Frontend
echo -e "\n${BLUE}[4/5] Starting Frontend...${NC}"
cd "$PROJECT_ROOT/frontend"
npm run dev > "$PROJECT_ROOT/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID >> "$PID_FILE"
echo -e "${GREEN}✓ Frontend started (PID: $FRONTEND_PID)${NC}"
echo -e "  Log file: logs/frontend.log"

# Wait for API to be ready
echo -e "\n${BLUE}Waiting for API to be ready...${NC}"
cd "$PROJECT_ROOT"
for i in {1..30}; do
    if curl -s http://localhost:8000/docs > /dev/null 2>&1; then
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
    echo -e "\n${BLUE}[5/5] Starting ngrok tunnel...${NC}"
    # Use static domain for consistent webhook URLs
    ngrok http 8000 --domain=aileen-ferny-uncoquettishly.ngrok-free.dev > "$PROJECT_ROOT/logs/ngrok.log" 2>&1 &
    NGROK_PID=$!
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
    echo -e "\n${YELLOW}[5/5] Skipping ngrok (not installed)${NC}"
fi

# Display summary
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}        BugViper is now running!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo -e "${BLUE}URLs:${NC}"
echo -e "  Frontend:    ${YELLOW}http://localhost:3000${NC}"
echo -e "  API:         ${YELLOW}http://localhost:8000${NC}"
echo -e "  API Docs:    ${YELLOW}http://localhost:8000/docs${NC}"
echo -e "  Ingestion:   ${YELLOW}http://localhost:8080${NC}"
echo -e "  Ingest Docs: ${YELLOW}http://localhost:8080/docs${NC}"
echo -e "  Lint:        ${YELLOW}http://localhost:8090${NC}"

if [ "$NGROK_AVAILABLE" = true ] && [ -n "$NGROK_URL" ]; then
    echo -e "  Ngrok:       ${YELLOW}$NGROK_URL${NC}"
    echo -e "  Ngrok Admin: ${YELLOW}http://localhost:4040${NC}"
fi

echo -e "\n${BLUE}View Logs:${NC}"
echo -e "  API:         ${YELLOW}tail -f logs/api.log${NC}"
echo -e "  Ingestion:   ${YELLOW}tail -f logs/ingestion.log${NC}"
echo -e "  Frontend:    ${YELLOW}tail -f logs/frontend.log${NC}"
if [ "$NGROK_AVAILABLE" = true ]; then
    echo -e "  Ngrok:       ${YELLOW}tail -f logs/ngrok.log${NC}"
fi
echo -e "  All:         ${YELLOW}tail -f logs/*.log${NC}"

echo -e "\n${BLUE}Process IDs:${NC}"
echo -e "  API:         ${YELLOW}$API_PID${NC}"
echo -e "  Ingestion:   ${YELLOW}$INGESTION_PID${NC}"
echo -e "  Frontend:    ${YELLOW}$FRONTEND_PID${NC}"
if [ "$NGROK_AVAILABLE" = true ]; then
    echo -e "  Ngrok:       ${YELLOW}$NGROK_PID${NC}"
fi

echo -e "\n${RED}Press Ctrl+C to stop all services${NC}\n"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# Keep script running
wait
