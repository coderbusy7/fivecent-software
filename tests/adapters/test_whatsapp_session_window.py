from __future__ import annotations

import httpx
import pytest

from fivecent.adapters.alerts.telegram import format_telegram_alert
from fivecent.messaging.errors import SessionWindowClosedError, graph_error_code
from fivecent.messaging.plugin_whatsapp.outbound import WhatsAppSender


def test_graph_error_code_extracts_131047() -> None:
    assert graph_error_code({
        "error": {
            "message": "Re-engagement message",
            "type": "OAuthException",
            "code": 131047,
            "error_data": {"details": "Message failed to send because more than 24 hours have passed"},
            "fbtrace_id": "x",
        }
    }) == 131047
    assert graph_error_code({"error": {"code": "131047"}}) == 131047
    assert graph_error_code({"ok": True}) is None
    assert graph_error_code("nope") is None


def test_session_window_telegram_card() -> None:
    text = format_telegram_alert(
        "port-vauban",
        "handoff",
        {
            "reason": "session_window_closed",
            "draft_reply": "Still interested in the Lagoon?",
            "wa_me": "https://wa.me/33612345678",
        },
    )
    assert "131047" in text
    assert "template" in text.lower()
    assert "https://wa.me/33612345678" in text
    assert "Still interested" in text


@pytest.mark.asyncio
async def test_whatsapp_sender_raises_session_window_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Re-engagement message",
                    "code": 131047,
                    "type": "OAuthException",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        sender = WhatsAppSender(
            access_token="token",
            phone_number_id="phone",
            graph_version="v21.0",
            client=client,
        )
        with pytest.raises(SessionWindowClosedError) as exc:
            await sender.send("port-vauban", "33612345678", "Hello again")
    assert exc.value.tenant_id == "port-vauban"
    assert exc.value.user_channel_id == "33612345678"
