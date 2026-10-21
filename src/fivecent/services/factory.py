from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.application.ports import CachePort
from fivecent.application.registry import PluginRegistry
from fivecent.application.tools import ServiceModule
from fivecent.services.plugin_yachting import build_yachting_plugin


def build_service_plugins(
    sessions: async_sessionmaker[AsyncSession],
    cache: CachePort,
) -> PluginRegistry[ServiceModule]:
    return PluginRegistry({
        "yachting": build_yachting_plugin(sessions, cache),
    })
