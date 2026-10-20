#!/usr/bin/env bash
# Start the FastAPI backend and the internal Streamlit admin.
# Usage: ./start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-dev-admin}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
ADMIN_PORT="${ADMIN_PORT:-8501}"
export ADMIN_API_BASE="${ADMIN_API_BASE:-http://${API_HOST}:${API_PORT}}"

API_PID=""
UI_PID=""

cleanup() {
  trap - INT TERM EXIT
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "$UI_PID" ]] && kill -0 "$UI_PID" 2>/dev/null; then
    kill "$UI_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"$PYTHON" -m uvicorn fivecent.api.app:app --host "$API_HOST" --port "$API_PORT" &
API_PID=$!

echo "Waiting for API at ${ADMIN_API_BASE}/health ..."
ready=0
for _ in $(seq 1 50); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API process exited before it was ready. Is port ${API_PORT} already in use?" >&2
    exit 1
  fi
  if curl -sf "${ADMIN_API_BASE}/health" >/dev/null; then
    ready=1
    break
  fi
  sleep 0.2
done
if [[ "$ready" -ne 1 ]]; then
  echo "API did not become ready at ${ADMIN_API_BASE}" >&2
  exit 1
fi

"$PYTHON" -m streamlit run "$ROOT/admin/app.py" \
  --server.port "$ADMIN_PORT" \
  --server.headless true \
  --browser.gatherUsageStats false &
UI_PID=$!

echo "API     http://${API_HOST}:${API_PORT}"
echo "Chat    POST http://${API_HOST}:${API_PORT}/chat"
echo "Admin   http://127.0.0.1:${ADMIN_PORT}"
echo "Token   ADMIN_API_TOKEN is set (${#ADMIN_API_TOKEN} chars). Ctrl-C stops both."

wait
