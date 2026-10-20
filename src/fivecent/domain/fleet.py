from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from fivecent.domain.quoting import Boat, DomainError

MAX_DAY_CHARTER_PAX = 12


@dataclass(frozen=True)
class ActiveHold:
    boat_id: str
    start_date: date
    days: int
    expires_at: datetime


@dataclass(frozen=True)
class AvailableBoat:
    boat: Boat
    reason: str = "available"


@dataclass(frozen=True)
class AvailabilityResult:
    ok: bool
    boats: tuple[AvailableBoat, ...] = ()
    error: Optional[DomainError] = None


def _stay_days(start_date: date, days: int) -> set[date]:
    return {start_date + timedelta(days=offset) for offset in range(days)}


def hold_overlaps(hold: ActiveHold, start_date: date, days: int, now: datetime) -> bool:
    if hold.expires_at <= now:
        return False
    return bool(_stay_days(hold.start_date, hold.days) & _stay_days(start_date, days))


def check_fleet_availability(
    boats: Sequence[Boat],
    holds: Sequence[ActiveHold],
    *,
    pax: int,
    start_date: date,
    days: int = 1,
    length_m: Optional[Decimal] = None,
    now: datetime,
    suggest_tandem: bool = True,
) -> AvailabilityResult:
    if type(pax) is not int or pax < 1:
        return AvailabilityResult(
            ok=False,
            error=DomainError("invalid_pax", "Passenger count must be a positive integer."),
        )
    if pax > MAX_DAY_CHARTER_PAX:
        extra = (
            " Suggest a tandem charter or a commercially coded mini-cruise vessel."
            if suggest_tandem
            else ""
        )
        return AvailabilityResult(
            ok=False,
            error=DomainError(
                "pax_cap",
                (
                    f"French/EU commercial day charters are limited to "
                    f"{MAX_DAY_CHARTER_PAX} cruising passengers.{extra}"
                ),
            ),
        )
    if type(days) is not int or days < 1:
        return AvailabilityResult(
            ok=False,
            error=DomainError("invalid_days", "days must be a positive integer."),
        )

    matches: list[AvailableBoat] = []
    for boat in boats:
        if not boat.active:
            continue
        if boat.capacity < pax:
            continue
        if length_m is not None and boat.length_m < length_m:
            continue
        blocked = any(hold.boat_id == boat.id and hold_overlaps(hold, start_date, days, now) for hold in holds)
        if blocked:
            continue
        matches.append(AvailableBoat(boat=boat))

    if not matches:
        return AvailabilityResult(
            ok=False,
            error=DomainError("no_availability", "No matching boats are free for those dates."),
        )
    return AvailabilityResult(ok=True, boats=tuple(matches))
