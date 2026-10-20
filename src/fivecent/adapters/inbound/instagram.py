from fivecent.domain.events import InboundMessageEvent


def normalize_instagram_payload(payload: dict, tenant_id: str) -> list[InboundMessageEvent]:
    raise NotImplementedError("Instagram adapter is not implemented in this slice.")
