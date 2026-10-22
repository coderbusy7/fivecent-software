from e2e.checks import MinLength, StatusIn, any_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="handoff",
    title="Broker handoff / follow-up",
    description="Guest asks for a broker WhatsApp follow-up.",
    tags=("handoff", "chat"),
    turns=(
        Turn(
            user=(
                "Please have a broker WhatsApp me later about Monaco repositioning. "
                "My name is Alex Test."
            ),
            checks=(
                MinLength(15),
                StatusIn(("ok",)),
                any_of(
                    "broker",
                    "whatsapp",
                    "call",
                    "desk",
                    "follow",
                    "reach",
                    "later",
                    "noted",
                    "pass",
                    "team",
                ),
            ),
        ),
    ),
)
