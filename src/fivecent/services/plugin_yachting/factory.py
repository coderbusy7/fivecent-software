from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.alerts.factory import build_broker_alerts
from fivecent.application.ports import CachePort
from fivecent.config import Settings
from fivecent.services.plugin_yachting.persistence import (
    SqlFleetRepo,
    SqlHoldRepo,
    SqlLeadRepo,
    SqlQuoteRepo,
)
from fivecent.services.plugin_yachting.ports import BrokerAlertPort
from fivecent.services.plugin_yachting.service import YachtingService


def build_yachting_plugin(
    sessions: async_sessionmaker[AsyncSession],
    cache: CachePort,
    *,
    settings: Optional[Settings] = None,
    closers: Optional[list[Callable]] = None,
    alerts: Optional[BrokerAlertPort] = None,
) -> YachtingService:
    if alerts is None:
        alerts = build_broker_alerts(settings, closers)
    return YachtingService(
        fleet=SqlFleetRepo(sessions),
        holds=SqlHoldRepo(sessions),
        quotes=SqlQuoteRepo(sessions),
        leads=SqlLeadRepo(sessions),
        cache=cache,
        alerts=alerts,
    )
