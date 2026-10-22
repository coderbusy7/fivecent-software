"""ASGI entrypoint.

Python 3.10+:
    python -m pip install -e ".[dev]"
    export OPENAI_API_KEY="your-openai-key"
    uvicorn fivecent.api.app:app --host 127.0.0.1 --port 8000
    # With Redis (production-like webhook path):
    arq fivecent.adapters.queue.arq_jobs.WorkerSettings

Local /chat works without Redis (inline queue + SQLite).
WhatsApp /webhook requires WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN,
and Redis if REDIS_URL is set so ACK only enqueues.

Internal admin (configure ADMIN_API_TOKEN, ADMIN_USERNAME, ADMIN_PASSWORD_HASH first):
    python -m pip install -e ".[admin]"
    python -m fivecent.admin_auth
    ./start.sh
"""

from fivecent.api.app import app

__all__ = ["app"]
