from __future__ import annotations

import json
import logging
from typing import Sequence

from fivecent.services.plugin_yachting.ports import BrokerAlertChannel, BrokerAlertPort

logger = logging.getLogger(__name__)


class LogBrokerAlerts:
    """Always-on observability sink."""

    async def deliver(self, tenant_id: str, kind: str, payload: dict) -> None:
        logger.info(
            "broker %s alert tenant=%s payload=%s",
            kind,
            tenant_id,
            json.dumps(payload),
        )


class CompositeBrokerAlerts(BrokerAlertPort):
    """Fan-out dispatcher. One channel failure does not block the others."""

    def __init__(self, channels: Sequence[BrokerAlertChannel]) -> None:
        self._channels = list(channels)

    async def notify_hold(self, tenant_id: str, payload: dict) -> bool:
        return await self._deliver(tenant_id, "hold", payload)

    async def notify_handoff(self, tenant_id: str, payload: dict) -> bool:
        return await self._deliver(tenant_id, "handoff", payload)

    async def notify_followup(self, tenant_id: str, payload: dict) -> bool:
        return await self._deliver(tenant_id, "followup", payload)

    async def _deliver(self, tenant_id: str, kind: str, payload: dict) -> bool:
        ok = True
        for channel in self._channels:
            try:
                await channel.deliver(tenant_id, kind, payload)
            except Exception:
                ok = False
                logger.exception(
                    "broker alert channel failed kind=%s tenant=%s channel=%s",
                    kind,
                    tenant_id,
                    type(channel).__name__,
                )
        return ok
