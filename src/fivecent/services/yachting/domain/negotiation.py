from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from fivecent.services.yachting.domain.quoting import DomainError, QuoteBreakdown, money

MAX_BASE_CONCESSION = Decimal("0.08")


@dataclass(frozen=True)
class NegotiationResult:
    ok: bool
    accepted_total: Optional[Decimal] = None
    concession_on_base: Optional[Decimal] = None
    floor_total: Optional[Decimal] = None
    error: Optional[DomainError] = None


def evaluate_negotiation_floor(
    quote: QuoteBreakdown,
    proposed_budget: Decimal,
    *,
    max_discount_pct: Decimal = MAX_BASE_CONCESSION,
    offer_perks: bool = True,
) -> NegotiationResult:
    if proposed_budget <= 0:
        return NegotiationResult(
            ok=False,
            error=DomainError("invalid_budget", "Proposed budget must be positive."),
        )

    floor_base = money(quote.base * (Decimal("1") - max_discount_pct))
    # VAT and APA stay frozen at the original quote; skipper is not concessionable.
    floor_total = money(floor_base + quote.skipper + quote.vat + quote.apa)
    pct_label = f"{(max_discount_pct * 100).quantize(Decimal('1'))}%"

    if proposed_budget >= quote.total:
        return NegotiationResult(
            ok=True,
            accepted_total=quote.total,
            concession_on_base=money(Decimal("0")),
            floor_total=floor_total,
        )
    if proposed_budget >= floor_total:
        concession = money(quote.total - proposed_budget)
        return NegotiationResult(
            ok=True,
            accepted_total=money(proposed_budget),
            concession_on_base=concession,
            floor_total=floor_total,
        )
    perk = " Escalate or offer a non-price perk." if offer_perks else " Escalate to the broker."
    return NegotiationResult(
        ok=False,
        floor_total=floor_total,
        error=DomainError(
            "below_floor",
            (
                f"Offer is below the maximum {pct_label} base concession. "
                f"VAT and APA are not negotiable.{perk}"
            ),
        ),
    )
