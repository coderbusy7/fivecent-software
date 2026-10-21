from datetime import date, datetime, timezone

from fivecent.services.plugin_yachting.domain.fleet import ActiveHold
from fivecent.services.plugin_yachting.domain.holds import SoftHoldApproval, SoftHoldRequest, escalate_and_soft_hold
from fivecent.services.plugin_yachting.domain.quoting import DomainError

NOW = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def test_requires_name_and_email() -> None:
    result = escalate_and_soft_hold(
        SoftHoldRequest("", "a@b.com", "lagoon-450", date(2026, 6, 6), 1),
        [],
        now=NOW,
    )
    assert isinstance(result, DomainError)
    assert result.code == "missing_name"
    result = escalate_and_soft_hold(
        SoftHoldRequest("Ada", "not-an-email", "lagoon-450", date(2026, 6, 6), 1),
        [],
        now=NOW,
    )
    assert isinstance(result, DomainError)
    assert result.code == "missing_email"


def test_conflict_and_success() -> None:
    existing = ActiveHold(
        boat_id="lagoon-450",
        start_date=date(2026, 6, 6),
        days=2,
        expires_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    conflict = escalate_and_soft_hold(
        SoftHoldRequest("Ada Lovelace", "ada@example.com", "lagoon-450", date(2026, 6, 7), 1),
        [existing],
        now=NOW,
    )
    assert isinstance(conflict, DomainError)
    assert conflict.code == "hold_conflict"

    ok = escalate_and_soft_hold(
        SoftHoldRequest("Ada Lovelace", "ada@example.com", "lagoon-450", date(2026, 6, 6), 1),
        [],
        now=NOW,
    )
    assert isinstance(ok, SoftHoldApproval)
    assert ok.expires_at.year == 2026
    assert (ok.expires_at - NOW).total_seconds() == 24 * 3600
