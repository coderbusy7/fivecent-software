from e2e.checks import MinLength, StatusIn, none_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="other_port",
    title="Other departure port (desk follow-up)",
    description="Guest asks for Monaco instead of home port — should not hard-refuse.",
    tags=("handoff", "chat"),
    turns=(
        Turn(
            user="Hi — we want a day charter leaving from Monaco, not Antibes",
            checks=(
                MinLength(20),
                StatusIn(("ok",)),
                none_of("out of scope", "cannot help with that"),
            ),
        ),
    ),
)
