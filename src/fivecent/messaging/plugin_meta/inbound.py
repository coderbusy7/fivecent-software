from fivecent.domain.events import InboundMessageEvent
from fivecent.messaging.graph import GraphWebhookAdapter, TenantLookup


def normalize_messenger_payload(payload: dict, tenant_id: str) -> list[InboundMessageEvent]:
    raise NotImplementedError("Messenger adapter is not implemented in this slice.")


class MetaMessengerWebhookAdapter(GraphWebhookAdapter):
    """Meta Messenger. Registry key is messenger."""

    channel = "messenger"

    def parse(
        self, payload: dict, tenant_for_account_id: TenantLookup
    ) -> list[InboundMessageEvent]:
        raise NotImplementedError("Messenger adapter is not implemented in this slice.")
