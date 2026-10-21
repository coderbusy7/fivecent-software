#!/usr/bin/env bash
# Render web process: migrate, then bind $PORT.
set -euo pipefail
cd "$(dirname "$0")"
python -m fivecent.adapters.persistence.migrate
exec python -m uvicorn fivecent.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
