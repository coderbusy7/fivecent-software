from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.application.ports import CachePort
from fivecent.application.registry import PluginRegistry
from fivecent.application.tools import ServiceModule
from fivecent.config import Settings
from fivecent.services.plugin_yachting import build_yachting_plugin
from fivecent.services.plugin_yachting.ports import BrokerAlertPort


def build_service_plugins(
    sessions: async_sessionmaker[AsyncSession],
    cache: CachePort,
    *,
    settings: Optional[Settings] = None,
    closers: Optional[list[Callable]] = None,
    alerts: Optional[BrokerAlertPort] = None,
) -> PluginRegistry[ServiceModule]:
    return PluginRegistry({
        "yachting": build_yachting_plugin(
            sessions,
            cache,
            settings=settings,
            closers=closers,
            alerts=alerts,
        ),
    })
