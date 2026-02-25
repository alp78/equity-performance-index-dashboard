#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  dev.sh — Start both backend and frontend for local development
# ═══════════════════════════════════════════════════════════════════════════
#  Usage:  ./dev.sh           (starts both)
#          ./dev.sh backend   (backend only)
#          ./dev.sh frontend  (frontend only)
# ═══════════════════════════════════════════════════════════════════════════

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

start_backend() {
    echo "Starting backend (FastAPI on :8000)..."
    cd "$ROOT/backend"
    if [ ! -d ".venv" ]; then
        echo "  Creating Python virtual environment..."
        python -m venv .venv
        source .venv/bin/activate
        pip install -q -r requirements.txt
    else
        source .venv/bin/activate
    fi
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
    BACKEND_PID=$!
    echo "  Backend PID: $BACKEND_PID"
    cd "$ROOT"
}

start_frontend() {
    echo "Starting frontend (SvelteKit on :5173)..."
    cd "$ROOT/frontend"
    if [ ! -d "node_modules" ]; then
        echo "  Installing npm dependencies..."
        npm install
    fi
    npm run dev &
    FRONTEND_PID=$!
    echo "  Frontend PID: $FRONTEND_PID"
    cd "$ROOT"
}

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait 2>/dev/null
    echo "Done."
}

trap cleanup EXIT INT TERM

case "${1:-all}" in
    backend)  start_backend; wait ;;
    frontend) start_frontend; wait ;;
    all|*)
        start_backend
        start_frontend
        echo ""
        echo "Backend:  http://localhost:8000"
        echo "Frontend: http://localhost:5173"
        echo "Press Ctrl+C to stop both."
        wait
        ;;
esac
