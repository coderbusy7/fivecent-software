from __future__ import annotations

from fivecent.application.ports import CachePort
from fivecent.application.tools import PreparedService, ServiceModule, ToolRegistry
from fivecent.domain.tenancy import TenantContext
from fivecent.services.plugin_yachting.ports import (
    BrokerAlertPort,
    FleetRepo,
    HoldRepo,
    LeadRepo,
    QuoteRepo,
)
from fivecent.services.plugin_yachting.guardrails import YachtingGuardrails
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
    ) -> None:
        self._fleet = fleet
        self._tools = YachtingTools(
            fleet=fleet,
            holds=holds,
            quotes=quotes,
            leads=leads,
            cache=cache,
            alerts=alerts,
        )
        self._guard = YachtingGuardrails(cache=cache, alerts=alerts)

    async def prepare(self, tenant: TenantContext) -> PreparedService:
        boats = await self._fleet.list_active(tenant.id)
        return PreparedService(
            system_prompt=build_system_prompt(tenant, fleet_summary(boats)),
            tools=ToolRegistry(self._tools.registry([boat.id for boat in boats], tenant)),
            guard=self._guard,
        )
