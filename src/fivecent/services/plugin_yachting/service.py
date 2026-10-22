from __future__ import annotations

from fivecent.application.ports import CachePort
from fivecent.application.tools import PreparedService, ServiceModule, ToolRegistry
from fivecent.domain.tenancy import TenantContext
from fivecent.services.plugin_yachting.ports import (
    AlertOutboxRepo,
    BrokerAlertPort,
    FleetRepo,
    HoldRepo,
    LeadRepo,
    QuoteRepo,
)
from fivecent.services.plugin_yachting.prompts import build_system_prompt, fleet_summary
from fivecent.services.plugin_yachting.tools import YachtingTools


class YachtingService(ServiceModule):
    def __init__(
        self,
        *,
        fleet: FleetRepo,
        holds: HoldRepo,
        quotes: QuoteRepo,
        leads: LeadRepo,
        cache: CachePort,
        alerts: BrokerAlertPort,
        alert_outbox: AlertOutboxRepo,
    ) -> None:
        self._fleet = fleet
        self._alert_outbox = alert_outbox
        self._alerts = alerts
        self._tools = YachtingTools(
            fleet=fleet,
            holds=holds,
            quotes=quotes,
            leads=leads,
            cache=cache,
            alerts=alerts,
            alert_outbox=alert_outbox,
        )

    async def flush_broker_alerts(self, *, limit: int = 50) -> dict[str, int]:
        return await self._alert_outbox.flush_pending(self._alerts, limit=limit)

    async def prepare(self, tenant: TenantContext) -> PreparedService:
        boats = await self._fleet.list_active(tenant.id)
        return PreparedService(
            system_prompt=build_system_prompt(tenant, fleet_summary(boats)),
            tools=ToolRegistry(self._tools.registry([boat.id for boat in boats], tenant)),
        )
