#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_pid=""
frontend_pid=""

cleanup() {
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$project_root/backend"
  exec ../.venv/bin/python -m uvicorn video_fission.main:app \
    --host "${VF_HOST:-127.0.0.1}" \
    --port "${VF_PORT:-8000}"
) &
backend_pid=$!

(
  cd "$project_root/frontend"
  exec npm run dev
) &
frontend_pid=$!

wait

