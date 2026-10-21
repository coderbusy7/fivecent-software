from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class LogBrokerAlerts:
    async def notify_hold(self, tenant_id: str, payload: dict) -> None:
        logger.info("broker hold alert tenant=%s payload=%s", tenant_id, json.dumps(payload))
