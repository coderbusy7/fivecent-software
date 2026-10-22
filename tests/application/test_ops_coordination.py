"""ED-12 / ED-13: rate limits, usage metering, retention, renewable lock helpers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fivecent.adapters.cache.memory import MemoryCache
from fivecent.adapters.persistence.ops import SqlOpsRepo
from fivecent.api.app import create_app
from fivecent.config import Settings


@pytest.mark.asyncio
async def test_memory_rate_limit_and_purge() -> None:
    cache = MemoryCache()
    for _ in range(3):
        assert await cache.check_tenant_rate_limit("t1", limit_per_minute=3)
    assert not await cache.check_tenant_rate_limit("t1", limit_per_minute=3)

    await cache.set_human_active("t1", "whatsapp", "+1", ttl_seconds=-1)
    assert cache.purge_expired() >= 1


@pytest.mark.asyncio
async def test_usage_metrics_retention_and_budget(container, settings: Settings) -> None:
    settings.admin_api_token = "ops-secret"
    sessions = container.conversations._sessions
    ops = SqlOpsRepo(sessions)
    await ops.record_turn_usage("port-vauban", tokens_in=1000, tokens_out=500, latency_ms=12)
    usage = await ops.usage_for_tenant("port-vauban", days=7)
    assert usage
    assert usage[0]["tokens_in"] >= 1000
    assert await ops.today_tokens("port-vauban") >= 1500

    snap = await ops.metrics_snapshot()
    assert "failed_inbound" in snap

    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer ops-secret"}
        metrics = client.get("/internal/metrics", headers=headers)
        assert metrics.status_code == 200
        assert "load_target" in metrics.json()
        usage_http = client.get(
            "/internal/tenants/port-vauban/usage", headers=headers
        )
        assert usage_http.status_code == 200
        assert usage_http.json()["items"]

        retention = client.post("/internal/ops/retention/run", headers=headers)
        assert retention.status_code == 200
        assert "messages_deleted" in retention.json()
