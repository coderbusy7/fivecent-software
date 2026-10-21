from datetime import date, datetime, timezone
from decimal import Decimal

from fivecent.services.plugin_yachting.domain.fleet import (
    ActiveHold,
    MAX_DAY_CHARTER_PAX,
    check_fleet_availability,
)
from fivecent.services.plugin_yachting.domain.quoting import Boat

LAGOON = Boat(
    id="lagoon-450",
    name="Lagoon 450",
    length_m=Decimal("13.96"),
    capacity=12,
    low_season_daily_rate=Decimal("1800"),
    high_season_daily_rate=Decimal("2200"),
    skipper_daily_rate=Decimal("350"),
)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_pax_over_12_hard_reject() -> None:
    result = check_fleet_availability(
        [LAGOON], [], pax=MAX_DAY_CHARTER_PAX + 1, start_date=date(2026, 6, 6), now=NOW
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "pax_cap"


def test_hold_hides_boat() -> None:
    hold = ActiveHold(
        boat_id="lagoon-450",
        start_date=date(2026, 6, 6),
        days=1,
        expires_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )
    result = check_fleet_availability(
        [LAGOON], [hold], pax=8, start_date=date(2026, 6, 6), now=NOW
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "no_availability"


def test_expired_hold_ignored() -> None:
    hold = ActiveHold(
        boat_id="lagoon-450",
        start_date=date(2026, 6, 6),
        days=1,
        expires_at=NOW,
    )
    result = check_fleet_availability(
        [LAGOON], [hold], pax=8, start_date=date(2026, 6, 6), now=NOW
    )
    assert result.ok is True
    assert result.boats[0].boat.id == "lagoon-450"


def test_length_filter() -> None:
    result = check_fleet_availability(
        [LAGOON], [], pax=4, start_date=date(2026, 6, 6), length_m=Decimal("20"), now=NOW
    )
    assert result.ok is False
