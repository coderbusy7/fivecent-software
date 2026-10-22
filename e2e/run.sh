#!/usr/bin/env bash
# Run Fivecent E2E scenarios from the repo root.
# Examples:
#   ./e2e/run.sh list
#   ./e2e/run.sh run greeting quote
#   ./e2e/run.sh run pilot_suite --mode chat --phone 33621611871
#   ./e2e/run.sh run webhook_smoke --mode webhook
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m e2e "$@"
