from datetime import date
from decimal import Decimal

from fivecent.domain.quoting import Boat, DomainError, QuoteBreakdown, calculate_itemized_quote, season_for_stay

LAGOON = Boat(
    id="lagoon-450",
    name="Lagoon 450",
    length_m=Decimal("13.96"),
    capacity=12,
    low_season_daily_rate=Decimal("1800"),
    high_season_daily_rate=Decimal("2200"),
    skipper_daily_rate=Decimal("350"),
)


def test_low_season_quote_vat_on_base_and_skipper_apa_on_base_only() -> None:
    quote = calculate_itemized_quote(LAGOON, date(2026, 6, 6), days=2, skipper=True)
    assert isinstance(quote, QuoteBreakdown)
    assert quote.season == "low"
    assert quote.base == Decimal("3600.00")
    assert quote.skipper == Decimal("700.00")
    assert quote.vat == Decimal("860.00")
    assert quote.apa == Decimal("1080.00")
    assert quote.total == Decimal("6240.00")


def test_high_season_if_any_day_in_july_or_august() -> None:
    assert season_for_stay(date(2026, 6, 30), 3) == "high"
    quote = calculate_itemized_quote(LAGOON, date(2026, 7, 1), days=1, skipper=False)
    assert isinstance(quote, QuoteBreakdown)
    assert quote.season == "high"
    assert quote.base == Decimal("2200.00")
    assert quote.skipper == Decimal("0.00")
    assert quote.vat == Decimal("440.00")
    assert quote.apa == Decimal("660.00")
    assert quote.total == Decimal("3300.00")


def test_inactive_boat_rejected() -> None:
    boat = Boat(
        id="x",
        name="X",
        length_m=Decimal("10"),
        capacity=8,
        low_season_daily_rate=Decimal("1"),
        high_season_daily_rate=Decimal("2"),
        skipper_daily_rate=Decimal("1"),
        active=False,
    )
    result = calculate_itemized_quote(boat, date(2026, 6, 1), 1)
    assert isinstance(result, DomainError)
    assert result.code == "boat_inactive"
