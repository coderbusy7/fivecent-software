from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.application.ports import CachePort
from fivecent.services.plugin_yachting.alerts import LogBrokerAlerts
from fivecent.services.plugin_yachting.persistence import (
    SqlFleetRepo,
    SqlHoldRepo,
    SqlLeadRepo,
    SqlQuoteRepo,
)
from fivecent.services.plugin_yachting.service import YachtingService


def build_yachting_plugin(
    sessions: async_sessionmaker[AsyncSession],
    cache: CachePort,
) -> YachtingService:
    return YachtingService(
        fleet=SqlFleetRepo(sessions),
        holds=SqlHoldRepo(sessions),
        quotes=SqlQuoteRepo(sessions),
        leads=SqlLeadRepo(sessions),
        cache=cache,
        alerts=LogBrokerAlerts(),
    )
