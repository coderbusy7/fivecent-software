from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fivecent.services.yachting.persistence.models import FleetInventory

FLEET = (
    {
        "id": "lagoon-450",
        "name": "Lagoon 450",
        "length_m": Decimal("13.96"),
        "capacity": 12,
        "low_season_daily_rate": Decimal("1800.00"),
        "high_season_daily_rate": Decimal("2200.00"),
        "skipper_daily_rate": Decimal("350.00"),
    },
    {
        "id": "sunseeker-60",
        "name": "Sunseeker 60",
        "length_m": Decimal("18.90"),
        "capacity": 12,
        "low_season_daily_rate": Decimal("3800.00"),
        "high_season_daily_rate": Decimal("4800.00"),
        "skipper_daily_rate": Decimal("450.00"),
    },
)


async def seed_fleet(session: AsyncSession, tenant_id: str) -> None:
    for boat in FLEET:
        found = await session.execute(
            select(FleetInventory).where(
                FleetInventory.id == boat["id"],
                FleetInventory.tenant_id == tenant_id,
            )
        )
        if found.scalar_one_or_none() is None:
            session.add(FleetInventory(tenant_id=tenant_id, active=True, **boat))
