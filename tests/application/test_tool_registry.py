import json
from decimal import Decimal

from fivecent.application.tools import Tool, ToolCall, ToolContext, ToolRegistry, ToolSpec
from fivecent.domain.tenancy import FeatureFlags, TenantContext


def _context() -> ToolContext:
    tenant = TenantContext(
        id="test",
        agency_name="Test",
        home_port="Test port",
        currency="EUR",
        whatsapp_phone_number_id="",
        whatsapp_waba_id="",
        vat_rate=Decimal("0.20"),
        apa_rate=Decimal("0.30"),
        max_discount_pct=Decimal("0.08"),
        routing_mode="broadcast",
        feature_flags=FeatureFlags(),
    )
    return ToolContext(tenant=tenant, conversation_id="conversation")


async def test_tool_registry_returns_error_for_malformed_arguments() -> None:
    async def handler(_arguments: dict, _context: ToolContext) -> str:
        return json.dumps({"ok": True})

    registry = ToolRegistry([
        Tool(ToolSpec("example", "Example", {"type": "object"}), handler)
    ])
    result = json.loads(await registry.execute(
        ToolCall("call-1", "example", "[1, 2]"),
        _context(),
    ))

    assert result["ok"] is False
    assert "JSON object" in result["error"]


async def test_tool_registry_returns_error_for_unknown_tool() -> None:
    registry = ToolRegistry([])
    result = json.loads(await registry.execute(
        ToolCall("call-1", "missing", "{}"),
        _context(),
    ))

    assert result == {
        "ok": False,
        "code": "unknown_tool",
        "error": "Unknown tool: missing",
    }
