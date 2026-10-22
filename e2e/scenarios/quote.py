from e2e.checks import MinLength, Regex, StatusIn, any_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="quote",
    title="Itemized charter quote",
    description="Ask for a Lagoon 450 quote; expect VAT/APA/currency signals.",
    tags=("quote", "money", "chat"),
    turns=(
        Turn(
            user=(
                "Quote the Lagoon 450 for 2 days from 2026-07-18 for 8 people, "
                "leaving from Port Vauban"
            ),
            checks=(
                MinLength(40),
                StatusIn(("ok",)),
                any_of("€", "eur", "vat", "apa", "lagoon", "total", "quote", "6120"),
                Regex(r"\d"),
            ),
        ),
    ),
)
