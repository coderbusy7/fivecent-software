from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fivecent.services.plugin_yachting.persistence.models import FleetInventory

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


async def seed_fleet(
    session: AsyncSession, tenant_id: str, *, namespace: bool = False
) -> None:
    """Insert starter boats. Non-seed tenants namespace ids as `{tenant}__{boat}`."""
    for boat in FLEET:
        boat_id = f"{tenant_id}__{boat['id']}" if namespace else boat["id"]
        found = await session.execute(
            select(FleetInventory).where(
                FleetInventory.id == boat_id,
                FleetInventory.tenant_id == tenant_id,
            )
        )
        if found.scalar_one_or_none() is None:
            session.add(
                FleetInventory(
                    id=boat_id,
                    tenant_id=tenant_id,
                    name=boat["name"],
                    length_m=boat["length_m"],
                    capacity=boat["capacity"],
                    low_season_daily_rate=boat["low_season_daily_rate"],
                    high_season_daily_rate=boat["high_season_daily_rate"],
                    skipper_daily_rate=boat["skipper_daily_rate"],
                    active=True,
                )
            )
