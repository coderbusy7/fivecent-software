from __future__ import annotations


class SessionWindowClosedError(Exception):
    """Meta WhatsApp error 131047: free-form session messaging window is closed."""

    code = 131047

    def __init__(
        self,
        *,
        tenant_id: str,
        user_channel_id: str,
        detail: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.user_channel_id = user_channel_id
        self.detail = detail
        super().__init__(
            f"WhatsApp session window closed (131047) tenant={tenant_id} user={user_channel_id}"
        )


def graph_error_code(payload: object) -> int | None:
    """Extract Graph API error.code from a parsed JSON body."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    try:
        return int(code)
    except (TypeError, ValueError):
        return None
