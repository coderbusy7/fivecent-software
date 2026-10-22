from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from time import time

WINDOW_TTL_SECONDS = 48 * 3600
WINDOW_LIMIT = 15
DEBOUNCE_TTL_SECONDS = 60


class MemoryCache:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._human: dict[tuple[str, str, str], float] = {}
        self._window: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
        self._outbound: dict[tuple[str, str], float] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self._debounce_buf: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
        self._debounce_gen: dict[tuple[str, str, str], tuple[float, int]] = {}
        self._debounce_mu = asyncio.Lock()

    @asynccontextmanager
    async def lock(self, tenant_id: str, channel: str, user_channel_id: str):
        async with self._locks[(tenant_id, channel, user_channel_id)]:
            yield

    async def is_human_active(self, tenant_id: str, channel: str, user_channel_id: str) -> bool:
        expires = self._human.get((tenant_id, channel, user_channel_id))
        return bool(expires and expires > time())

    async def set_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str, ttl_seconds: int = 86400
    ) -> None:
        self._human[(tenant_id, channel, user_channel_id)] = time() + ttl_seconds

    async def clear_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> None:
        self._human.pop((tenant_id, channel, user_channel_id), None)

    async def check_tenant_rate_limit(
        self, tenant_id: str, *, limit_per_minute: int = 60
    ) -> bool:
        key = f"rate:tenant:{tenant_id}:minute"
        bucket = int(time() // 60)
        counter_key = f"{key}:{bucket}"
        # Drop prior minute buckets so counters stay bounded.
        stale = [k for k in self._counters if k.startswith(f"{key}:") and k != counter_key]
        for k in stale:
            self._counters.pop(k, None)
        self._counters[counter_key] += 1
        return self._counters[counter_key] <= limit_per_minute

    def purge_expired(self) -> int:
        """Drop expired human/window/outbound entries (ED-12 bounded cleanup)."""
        now = time()
        removed = 0
        for store in (self._human, self._outbound):
            stale = [key for key, expires in store.items() if expires < now]
            for key in stale:
                store.pop(key, None)
                removed += 1
        stale_windows = [key for key, packed in self._window.items() if packed[0] < now]
        for key in stale_windows:
            self._window.pop(key, None)
            removed += 1
        return removed

    async def get_window(self, tenant_id: str, channel: str, user_channel_id: str) -> list[dict]:
        packed = self._window.get((tenant_id, channel, user_channel_id))
        if not packed or packed[0] < time():
            return []
        return list(packed[1])

    async def replace_window(
        self, tenant_id: str, channel: str, user_channel_id: str, turns: list[dict]
    ) -> None:
        self._window[(tenant_id, channel, user_channel_id)] = (
            time() + WINDOW_TTL_SECONDS,
            turns[-WINDOW_LIMIT:],
        )

    async def remember_outbound(self, tenant_id: str, provider_message_id: str) -> None:
        self._outbound[(tenant_id, provider_message_id)] = time() + 2 * 86400

    async def was_sent_by_bot(self, tenant_id: str, provider_message_id: str) -> bool:
        expires = self._outbound.get((tenant_id, provider_message_id))
        return bool(expires and expires > time())

    async def incr(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]

    def _debounce_key(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> tuple[str, str, str]:
        return (tenant_id, channel, user_channel_id)

    async def append_debounce_fragment(
        self,
        tenant_id: str,
        channel: str,
        user_channel_id: str,
        *,
        text: str,
        provider_message_id: str,
    ) -> None:
        key = self._debounce_key(tenant_id, channel, user_channel_id)
        async with self._debounce_mu:
            expires, items = self._debounce_buf.get(key, (0.0, []))
            if expires < time():
                items = []
            items = list(items)
            items.append({"text": text, "provider_message_id": provider_message_id})
            self._debounce_buf[key] = (time() + DEBOUNCE_TTL_SECONDS, items)

    async def bump_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int:
        key = self._debounce_key(tenant_id, channel, user_channel_id)
        async with self._debounce_mu:
            expires, gen = self._debounce_gen.get(key, (0.0, 0))
            if expires < time():
                gen = 0
            gen += 1
            self._debounce_gen[key] = (time() + DEBOUNCE_TTL_SECONDS, gen)
            return gen

    async def get_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int:
        key = self._debounce_key(tenant_id, channel, user_channel_id)
        async with self._debounce_mu:
            expires, gen = self._debounce_gen.get(key, (0.0, 0))
            if expires < time():
                return 0
            return gen

    async def drain_debounce_fragments(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> list[dict]:
        key = self._debounce_key(tenant_id, channel, user_channel_id)
        async with self._debounce_mu:
            packed = self._debounce_buf.pop(key, None)
            if not packed or packed[0] < time():
                return []
            seen: set[str] = set()
            out: list[dict] = []
            for item in packed[1]:
                mid = str(item.get("provider_message_id") or "")
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                out.append(item)
            return out
