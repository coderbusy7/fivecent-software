from __future__ import annotations

from typing import Callable, Optional

import httpx

from fivecent.adapters.alerts.telegram import TelegramBrokerAlerts
from fivecent.config import Settings
from fivecent.services.plugin_yachting.alerts import CompositeBrokerAlerts, LogBrokerAlerts
from fivecent.services.plugin_yachting.ports import BrokerAlertPort


def build_broker_alerts(
    settings: Optional[Settings] = None,
    closers: Optional[list[Callable]] = None,
) -> BrokerAlertPort:
    channels: list = [LogBrokerAlerts()]
    token = (settings.telegram_bot_token if settings else "") or ""
    if token:
        client = httpx.AsyncClient(timeout=15.0)
        telegram = TelegramBrokerAlerts(bot_token=token, client=client)
        channels.append(telegram)
        if closers is not None:
            closers.append(telegram.aclose)
    return CompositeBrokerAlerts(channels)
