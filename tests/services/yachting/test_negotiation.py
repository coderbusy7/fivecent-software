from datetime import date
from decimal import Decimal

from fivecent.services.yachting.domain.negotiation import evaluate_negotiation_floor
from fivecent.services.yachting.domain.quoting import Boat, calculate_itemized_quote

LAGOON = Boat(
    id="lagoon-450",
    name="Lagoon 450",
    length_m=Decimal("13.96"),
    capacity=12,
    low_season_daily_rate=Decimal("1800"),
    high_season_daily_rate=Decimal("2200"),
    skipper_daily_rate=Decimal("350"),
)


def _quote():
    result = calculate_itemized_quote(LAGOON, date(2026, 6, 6), days=2, skipper=True)
    assert hasattr(result, "total")
    return result


def test_vat_and_apa_frozen_eight_percent_base_only() -> None:
    quote = _quote()
    # floor_base = 3600 * 0.92 = 3312; floor_total = 3312 + 700 + 860 + 1080 = 5952
    accepted = evaluate_negotiation_floor(quote, Decimal("6000.00"))
    assert accepted.ok is True
    assert accepted.accepted_total == Decimal("6000.00")
    assert accepted.floor_total == Decimal("5952.00")

    rejected = evaluate_negotiation_floor(quote, Decimal("5000.00"))
    assert rejected.ok is False
    assert rejected.error is not None
    assert rejected.error.code == "below_floor"


def test_at_or_above_list_keeps_original_total() -> None:
    quote = _quote()
    result = evaluate_negotiation_floor(quote, Decimal("10000"))
    assert result.ok is True
    assert result.accepted_total == quote.total
    assert result.concession_on_base == Decimal("0.00")
