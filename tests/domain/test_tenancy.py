from datetime import datetime, timezone
from decimal import Decimal

from fivecent.domain.tenancy import FeatureFlags, TenantContext, is_after_hours
from fivecent.services.plugin_yachting.domain.quoting import QuoteBreakdown, calculate_itemized_quote
from tests.services.yachting.test_quoting import LAGOON


def _tenant(**flags) -> TenantContext:
    merged = FeatureFlags.from_mapping(flags)
    return TenantContext(
        id="cannes",
        agency_name="Cannes Luxury Marine",
        home_port="Cannes Vieux Port",
        currency="EUR",
        whatsapp_phone_number_id="phone-cannes",
        whatsapp_waba_id="waba-cannes",
        vat_rate=Decimal("0.20"),
        apa_rate=Decimal("0.25"),
        max_discount_pct=Decimal("0.05"),
        routing_mode="broadcast",
        feature_flags=merged,
        brokers=(),
        is_active=True,
    )


def test_sailing_apa_25_percent() -> None:
    tenant = _tenant()
    from datetime import date

    quote = calculate_itemized_quote(
        LAGOON,
        date(2026, 6, 6),
        days=2,
        vat_rate=tenant.vat_rate,
        apa_rate=tenant.apa_rate,
    )
    assert isinstance(quote, QuoteBreakdown)
    assert quote.apa == Decimal("900.00")
    assert quote.total == Decimal("6060.00")


def test_after_hours_overnight_window() -> None:
    evening = datetime(2026, 6, 6, 20, 0, tzinfo=timezone.utc)  # 22:00 Paris in June (CEST)
    afternoon = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)  # 14:00 Paris
    assert is_after_hours(evening) is True
    assert is_after_hours(afternoon) is False


def test_after_hours_only_flag() -> None:
    tenant = _tenant(enable_after_hours_only=True)
    afternoon = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    assert tenant.bot_should_run(afternoon) is False
    evening = datetime(2026, 6, 6, 20, 0, tzinfo=timezone.utc)
    assert tenant.bot_should_run(evening) is True
