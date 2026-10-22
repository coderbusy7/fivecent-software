"""Operator-facing fleet CRUD for agency onboarding (ED-10)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.services.plugin_yachting.persistence.models import FleetInventory
from fivecent.services.plugin_yachting.persistence.seed import FLEET, seed_fleet

_BOAT_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class FleetValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def scoped_boat_id(tenant_id: str, boat_key: str) -> str:
    key = (boat_key or "").strip().lower()
    if not key:
        raise FleetValidationError("boat_id is required.", field="boat_id")
    prefix = f"{tenant_id}__"
    if key.startswith(prefix):
        suffix = key[len(prefix) :]
    else:
        suffix = key
    if not _BOAT_KEY.match(suffix):
        raise FleetValidationError(
            "boat_id must be lowercase letters, digits, and hyphens "
            "(e.g. lagoon-450).",
            field="boat_id",
        )
    # Seed tenant keeps classic short ids for backwards compatibility.
    if tenant_id == "port-vauban" and not key.startswith(prefix):
        return suffix
    return f"{prefix}{suffix}"


def _money(value: object, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise FleetValidationError(f"{field} must be a number.", field=field) from exc
    if amount < 0:
        raise FleetValidationError(f"{field} cannot be negative.", field=field)
    return amount.quantize(Decimal("0.01"))


def _int(value: object, *, field: str, minimum: int = 1) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise FleetValidationError(f"{field} must be an integer.", field=field) from exc
    if number < minimum:
        raise FleetValidationError(f"{field} must be >= {minimum}.", field=field)
    return number


@dataclass(frozen=True)
class BoatRow:
    boat_id: str
    tenant_id: str
    name: str
    length_m: Decimal
    capacity: int
    low_season_daily_rate: Decimal
    high_season_daily_rate: Decimal
    skipper_daily_rate: Decimal
    active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "boat_id": self.boat_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "length_m": str(self.length_m),
            "capacity": self.capacity,
            "low_season_daily_rate": str(self.low_season_daily_rate),
            "high_season_daily_rate": str(self.high_season_daily_rate),
            "skipper_daily_rate": str(self.skipper_daily_rate),
            "active": self.active,
        }


def _row(model: FleetInventory) -> BoatRow:
    return BoatRow(
        boat_id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        length_m=Decimal(model.length_m),
        capacity=model.capacity,
        low_season_daily_rate=Decimal(model.low_season_daily_rate),
        high_season_daily_rate=Decimal(model.high_season_daily_rate),
        skipper_daily_rate=Decimal(model.skipper_daily_rate),
        active=bool(model.active),
    )


class SqlFleetAdminRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_boats(self, tenant_id: str) -> Sequence[BoatRow]:
        async with self._sessions() as session:
            result = await session.execute(
                select(FleetInventory)
                .where(FleetInventory.tenant_id == tenant_id)
                .order_by(FleetInventory.name.asc())
            )
            return [_row(item) for item in result.scalars().all()]

    async def get_boat(self, tenant_id: str, boat_id: str) -> Optional[BoatRow]:
        async with self._sessions() as session:
            result = await session.execute(
                select(FleetInventory).where(
                    FleetInventory.tenant_id == tenant_id,
                    FleetInventory.id == boat_id,
                )
            )
            model = result.scalar_one_or_none()
            return _row(model) if model else None

    async def upsert_boat(
        self,
        tenant_id: str,
        *,
        boat_id: str,
        name: str,
        length_m: object,
        capacity: object,
        low_season_daily_rate: object,
        high_season_daily_rate: object,
        skipper_daily_rate: object,
        active: bool = True,
    ) -> BoatRow:
        resolved_id = scoped_boat_id(tenant_id, boat_id)
        clean_name = (name or "").strip()
        if not clean_name:
            raise FleetValidationError("name is required.", field="name")
        length = _money(length_m, field="length_m")
        if length <= 0:
            raise FleetValidationError("length_m must be positive.", field="length_m")
        caps = _int(capacity, field="capacity", minimum=1)
        if caps > 12:
            raise FleetValidationError(
                "capacity cannot exceed 12 for day-charter inventory.",
                field="capacity",
            )
        low = _money(low_season_daily_rate, field="low_season_daily_rate")
        high = _money(high_season_daily_rate, field="high_season_daily_rate")
        skipper = _money(skipper_daily_rate, field="skipper_daily_rate")
        async with self._sessions() as session:
            # Global PK is boat id today — reject collisions owned by another tenant.
            existing_any = await session.get(FleetInventory, resolved_id)
            if existing_any is not None and existing_any.tenant_id != tenant_id:
                raise FleetValidationError(
                    f"boat_id '{resolved_id}' is already used by another agency. "
                    "Choose a different id.",
                    field="boat_id",
                )
            row = existing_any
            if row is None:
                row = FleetInventory(
                    id=resolved_id,
                    tenant_id=tenant_id,
                    name=clean_name,
                    length_m=length,
                    capacity=caps,
                    low_season_daily_rate=low,
                    high_season_daily_rate=high,
                    skipper_daily_rate=skipper,
                    active=active,
                )
                session.add(row)
            else:
                row.name = clean_name
                row.length_m = length
                row.capacity = caps
                row.low_season_daily_rate = low
                row.high_season_daily_rate = high
                row.skipper_daily_rate = skipper
                row.active = active
            await session.commit()
            await session.refresh(row)
            return _row(row)

    async def set_active(self, tenant_id: str, boat_id: str, active: bool) -> Optional[BoatRow]:
        async with self._sessions() as session:
            result = await session.execute(
                select(FleetInventory).where(
                    FleetInventory.tenant_id == tenant_id,
                    FleetInventory.id == boat_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.active = active
            await session.commit()
            await session.refresh(row)
            return _row(row)

    async def import_boats(
        self, tenant_id: str, rows: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        created = 0
        updated = 0
        errors: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                errors.append({"row": index, "error": "Each row must be an object."})
                continue
            try:
                boat_id = str(raw.get("boat_id") or raw.get("id") or "")
                before = await self.get_boat(tenant_id, scoped_boat_id(tenant_id, boat_id))
                await self.upsert_boat(
                    tenant_id,
                    boat_id=boat_id,
                    name=str(raw.get("name") or ""),
                    length_m=raw.get("length_m"),
                    capacity=raw.get("capacity"),
                    low_season_daily_rate=raw.get("low_season_daily_rate"),
                    high_season_daily_rate=raw.get("high_season_daily_rate"),
                    skipper_daily_rate=raw.get("skipper_daily_rate"),
                    active=bool(raw.get("active", True)),
                )
                if before is None:
                    created += 1
                else:
                    updated += 1
            except FleetValidationError as exc:
                errors.append(
                    {"row": index, "field": exc.field, "error": str(exc), "boat_id": raw.get("boat_id")}
                )
        return {"created": created, "updated": updated, "errors": errors}

    async def seed_starter_fleet(self, tenant_id: str) -> Sequence[BoatRow]:
        async with self._sessions() as session:
            await seed_fleet(session, tenant_id, namespace=tenant_id != "port-vauban")
            await session.commit()
        return await self.list_boats(tenant_id)

    async def active_count(self, tenant_id: str) -> int:
        boats = await self.list_boats(tenant_id)
        return sum(1 for boat in boats if boat.active)


def starter_template() -> list[dict[str, Any]]:
    return [
        {
            "boat_id": boat["id"],
            "name": boat["name"],
            "length_m": str(boat["length_m"]),
            "capacity": boat["capacity"],
            "low_season_daily_rate": str(boat["low_season_daily_rate"]),
            "high_season_daily_rate": str(boat["high_season_daily_rate"]),
            "skipper_daily_rate": str(boat["skipper_daily_rate"]),
            "active": True,
        }
        for boat in FLEET
    ]
