#!/usr/bin/env bash
# Start the detector backend.
#
# Works on Windows (Git Bash), macOS and Linux. Finds the platform's venv
# interpreter automatically, then execs uvicorn (so this script is the process
# and signals/`docker stop` reach uvicorn directly).
#
# Usage:
#   ./scripts/start-backend.sh                 # dev server on 127.0.0.1:8000
#   PORT=9000 ./scripts/start-backend.sh       # custom port
#   RELOAD=1 ./scripts/start-backend.sh        # auto-reload on code changes
#   ENVIRONMENT=production ./scripts/start-backend.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"

# --- pick the venv interpreter ------------------------------------------------
if [ -x "$BACKEND/.venv/Scripts/python.exe" ]; then
  PY="$BACKEND/.venv/Scripts/python.exe"          # Windows
elif [ -x "$BACKEND/.venv/bin/python" ]; then
  PY="$BACKEND/.venv/bin/python"                  # macOS / Linux
else
  PY="$(command -v python3 || command -v python || true)"
  if [ -z "$PY" ]; then
    echo "error: no Python found and backend/.venv is missing." >&2
    echo "       run: cd backend && python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt" >&2
    exit 1
  fi
  echo "warning: backend/.venv not found; using $PY" >&2
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-0}"
WORKERS="${WORKERS:-1}"

cd "$BACKEND"
export PYTHONIOENCODING=utf-8

ARGS=(-m uvicorn app.main:app --host "$HOST" --port "$PORT")
if [ "$RELOAD" = "1" ]; then
  ARGS+=(--reload)
elif [ "$WORKERS" != "1" ]; then
  ARGS+=(--workers "$WORKERS")
fi

echo "Starting backend on http://$HOST:$PORT  (docs: http://$HOST:$PORT/docs)"
exec "$PY" "${ARGS[@]}"
