from fivecent.domain.events import InboundMessageEvent


def normalize_messenger_payload(payload: dict, tenant_id: str) -> list[InboundMessageEvent]:
    raise NotImplementedError("Messenger adapter is not implemented in this slice.")
