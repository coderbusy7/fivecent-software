from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from fivecent.services.yachting.domain.fleet import ActiveHold, hold_overlaps
from fivecent.services.yachting.domain.quoting import DomainError

HOLD_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class SoftHoldRequest:
    customer_name: str
    email: str
    boat_id: str
    start_date: date
    days: int


@dataclass(frozen=True)
class SoftHoldApproval:
    boat_id: str
    start_date: date
    days: int
    expires_at: datetime
    customer_name: str
    email: str


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1] and " " not in email


def escalate_and_soft_hold(
    request: SoftHoldRequest,
    holds: Sequence[ActiveHold],
    *,
    now: datetime,
) -> SoftHoldApproval | DomainError:
    name = request.customer_name.strip()
    email = request.email.strip()
    if not name:
        return DomainError("missing_name", "A customer name is required to place a 24h option.")
    if not _valid_email(email):
        return DomainError("missing_email", "A valid email is required to place a 24h option.")
    if request.days < 1:
        return DomainError("invalid_days", "days must be a positive integer.")

    conflict = any(
        hold.boat_id == request.boat_id and hold_overlaps(hold, request.start_date, request.days, now)
        for hold in holds
    )
    if conflict:
        return DomainError("hold_conflict", "That boat already has an active option on those dates.")

    return SoftHoldApproval(
        boat_id=request.boat_id,
        start_date=request.start_date,
        days=request.days,
        expires_at=now + HOLD_TTL,
        customer_name=name,
        email=email,
    )
