from e2e.checks import MinLength, StatusIn, none_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="greeting",
    title="Greeting / first reply",
    description="Guest says hello; Elena should answer with a helpful opening.",
    tags=("smoke", "chat"),
    turns=(
        Turn(
            user="Hi Elena — looking for a day charter this summer",
            checks=(
                MinLength(20),
                StatusIn(("ok",)),
                none_of("out of scope", "cannot help"),
            ),
        ),
    ),
)
