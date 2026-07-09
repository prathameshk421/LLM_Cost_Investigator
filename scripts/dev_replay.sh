#!/usr/bin/env bash
# Start replay API + frontend for local demo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d data/replay ]] || [[ -z "$(ls -A data/replay/*.json 2>/dev/null || true)" ]]; then
  echo "Exporting replay catalog..."
  python3 scripts/export_replay_catalog.py
fi

export PYTHONPATH="$ROOT/web:$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Backend
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "Installing web deps..."
  python3 -m pip install -q -e ".[web]"
fi

echo "Starting API on :8000..."
(
  cd "$ROOT"
  PYTHONPATH="$ROOT/web:$ROOT" python3 -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8000
) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
sleep 1

# Frontend
cd "$ROOT/web/frontend"
if [[ ! -d node_modules ]]; then
  echo "Installing frontend deps..."
  npm install
fi
echo "Starting frontend on :5173..."
npm run dev
