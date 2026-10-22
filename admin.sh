#!/usr/bin/env bash
# Start only the Streamlit admin UI (API must already be running).
# Usage:
#   ADMIN_API_BASE=https://fivecent-software.onrender.com ADMIN_API_TOKEN=... ./admin.sh
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

export ADMIN_API_BASE="${ADMIN_API_BASE:-http://127.0.0.1:8000}"
ADMIN_HOST="${ADMIN_HOST:-0.0.0.0}"
ADMIN_PORT="${PORT:-${ADMIN_PORT:-8501}}"

"$PYTHON" -c "from fivecent.admin_auth import AdminSettings; AdminSettings().validate_configuration()"

echo "Admin UI  http://${ADMIN_HOST}:${ADMIN_PORT}"
echo "API base  ${ADMIN_API_BASE}"

exec "$PYTHON" -m streamlit run "$ROOT/admin/app.py" \
  --server.address "$ADMIN_HOST" \
  --server.port "$ADMIN_PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
