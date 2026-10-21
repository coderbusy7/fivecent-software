from fivecent.domain.events import InboundMessageEvent
from fivecent.messaging.graph import GraphWebhookAdapter, TenantLookup


def normalize_instagram_payload(payload: dict, tenant_id: str) -> list[InboundMessageEvent]:
    raise NotImplementedError("Instagram adapter is not implemented in this slice.")


class InstagramWebhookAdapter(GraphWebhookAdapter):
    channel = "instagram"

    def parse(
        self, payload: dict, tenant_for_account_id: TenantLookup
    ) -> list[InboundMessageEvent]:
        raise NotImplementedError("Instagram adapter is not implemented in this slice.")
