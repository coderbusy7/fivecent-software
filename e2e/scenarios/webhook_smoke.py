from e2e.checks import StatusIn
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="webhook_smoke",
    title="WhatsApp webhook smoke",
    description=(
        "POST a signed Meta webhook as the test recipient. "
        "Asserts HTTP acceptance only — LLM text is delivered to WhatsApp, not the HTTP body. "
        "Use mode=webhook."
    ),
    tags=("webhook", "smoke"),
    turns=(
        Turn(
            user="Hi Elena — e2e webhook smoke test",
            require_reply=False,
            checks=(StatusIn(("ok",)),),
        ),
    ),
)
