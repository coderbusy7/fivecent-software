"""Access control helpers for the public test chat endpoint."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from fivecent.config import Settings

CHAT_WINDOW_SECONDS = 60
CHAT_MAX_REQUESTS = 30


class ChatRateLimiter:
    """Process-wide sliding window by client IP."""

    def __init__(
        self, *, window_seconds: int = CHAT_WINDOW_SECONDS, max_requests: int = CHAT_MAX_REQUESTS
    ) -> None:
        self._window = window_seconds
        self._max = max_requests
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            bucket = self._hits[client_key]
            while bucket and now - bucket[0] >= self._window:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True


_limiter = ChatRateLimiter()


def enforce_chat_access(
    settings: Settings,
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    if not settings.chat_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    if settings.is_production:
        expected = (settings.admin_api_token or "").strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="ADMIN_API_TOKEN is required when test chat is enabled in production.",
            )
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or token.strip() != expected:
            raise HTTPException(status_code=401, detail="Unauthorized.")
        client = request.client.host if request.client else "unknown"
        if not _limiter.allow(client):
            raise HTTPException(status_code=429, detail="Rate limit exceeded.")
