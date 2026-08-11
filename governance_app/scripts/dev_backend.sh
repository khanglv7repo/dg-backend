#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# Import only launcher-specific values from .env. Application settings keep
# loading their own configuration through the normal Backend settings layer.
if [[ -f .env ]]; then
  eval "$(python - <<'PY'
from dotenv import dotenv_values
import shlex

values = dotenv_values(".env")
for key in (
    "CELERY_BROKER_URL",
    "MCP_HOST",
    "MCP_PORT",
    "MCP_PATH",
):
    value = values.get(key)
    if value:
        print(f"export {key}={shlex.quote(str(value))}")
PY
)"
fi

export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://127.0.0.1:6379/0}"
export MCP_ENABLED=true

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-4}"
CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"
DEV_START_CELERY_BEAT="${DEV_START_CELERY_BEAT:-0}"

pids=()

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  if ((${#pids[@]})); then
    echo
    echo "[dev] stopping backend processes..."
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
  exit "${status}"
}

trap cleanup INT TERM EXIT

start_process() {
  local name="$1"
  shift
  echo "[dev] starting ${name}: $*"
  "$@" &
  pids+=("$!")
}

start_process "FastAPI" \
  python -m uvicorn app.main:app \
  --reload \
  --host "${API_HOST}" \
  --port "${API_PORT}"

start_process "Backend MCP" \
  python -m app.mcp.backend_mcp_server

start_process "Celery worker (default)" \
  python -m celery -A app.celery_app worker \
  -Q default \
  -c "${CELERY_CONCURRENCY}" \
  --loglevel="${CELERY_LOG_LEVEL}" \
  -n 'backend@%h'

if [[ "${DEV_START_CELERY_BEAT}" == "1" ]]; then
  start_process "Celery Beat" \
    python -m celery -A app.celery_app beat \
    --loglevel="${CELERY_LOG_LEVEL}"
fi

cat <<EOF2

[dev] backend stack started
[dev] API:         http://${API_HOST}:${API_PORT}
[dev] MCP:         http://${MCP_HOST:-127.0.0.1}:${MCP_PORT:-8001}${MCP_PATH:-/mcp}
[dev] Celery:      queue=default broker=${CELERY_BROKER_URL}
[dev] Celery Beat: $([[ "${DEV_START_CELERY_BEAT}" == "1" ]] && echo enabled || echo disabled)
[dev] Ctrl+C stops the whole local stack.

EOF2

wait -n "${pids[@]}"
echo "[dev] a backend process exited; shutting down the remaining processes." >&2
exit 1
