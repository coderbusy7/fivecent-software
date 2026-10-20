from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

POLICY_VERSION = "2026-vat20-apa30-v1"
VAT_RATE = Decimal("0.20")
APA_RATE = Decimal("0.30")
MONEY_QUANTUM = Decimal("0.01")
HIGH_SEASON_MONTHS = frozenset({7, 8})
Currency = Literal["EUR"]
Season = Literal["high", "low"]


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Boat:
    id: str
    name: str
    length_m: Decimal
    capacity: int
    low_season_daily_rate: Decimal
    high_season_daily_rate: Decimal
    skipper_daily_rate: Decimal
    active: bool = True


@dataclass(frozen=True)
class QuoteBreakdown:
    boat_id: str
    boat_name: str
    start_date: date
    days: int
    season: Season
    daily_rate: Decimal
    skipper_included: bool
    base: Decimal
    skipper: Decimal
    vat: Decimal
    apa: Decimal
    total: Decimal
    currency: Currency = "EUR"
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True)
class DomainError:
    code: str
    message: str


def season_for(day: date) -> Season:
    return "high" if day.month in HIGH_SEASON_MONTHS else "low"


def season_for_stay(start_date: date, days: int) -> Season:
    if days < 1:
        return "low"
    for offset in range(days):
        if season_for(start_date + timedelta(days=offset)) == "high":
            return "high"
    return "low"


def daily_rate(boat: Boat, season: Season) -> Decimal:
    if season == "high":
        return Decimal(boat.high_season_daily_rate)
    return Decimal(boat.low_season_daily_rate)


def calculate_itemized_quote(
    boat: Boat,
    start_date: date,
    days: int,
    skipper: bool = True,
    *,
    vat_rate: Decimal = VAT_RATE,
    apa_rate: Decimal = APA_RATE,
    currency: Currency = "EUR",
) -> QuoteBreakdown | DomainError:
    if not boat.active:
        return DomainError("boat_inactive", f"{boat.name} is not available.")
    if type(days) is not int or days < 1:
        return DomainError("invalid_days", "days must be a positive integer.")

    season = season_for_stay(start_date, days)
    rate = daily_rate(boat, season)
    base = money(rate * days)
    skipper_total = money(Decimal(boat.skipper_daily_rate) * days) if skipper else money(Decimal("0"))
    taxable = base + skipper_total
    vat = money(taxable * vat_rate)
    apa = money(base * apa_rate)
    total = money(taxable + vat + apa)
    return QuoteBreakdown(
        boat_id=boat.id,
        boat_name=boat.name,
        start_date=start_date,
        days=days,
        season=season,
        daily_rate=money(rate),
        skipper_included=skipper,
        base=base,
        skipper=skipper_total,
        vat=vat,
        apa=apa,
        total=total,
        currency=currency,
        policy_version=f"{POLICY_VERSION}-vat{vat_rate}-apa{apa_rate}",
    )


def quote_to_dict(quote: QuoteBreakdown, quote_id: Optional[str] = None) -> dict:
    payload = {
        "boat_id": quote.boat_id,
        "boat_name": quote.boat_name,
        "start_date": quote.start_date.isoformat(),
        "days": quote.days,
        "season": quote.season,
        "daily_rate": str(quote.daily_rate),
        "skipper_included": quote.skipper_included,
        "base": str(quote.base),
        "skipper": str(quote.skipper),
        "vat": str(quote.vat),
        "apa": str(quote.apa),
        "total": str(quote.total),
        "currency": quote.currency,
        "policy_version": quote.policy_version,
    }
    if quote_id:
        payload["quote_id"] = quote_id
    return payload
