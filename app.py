"""Single-process prototype: state is lost on restart; use one Uvicorn worker.

Python 3.10+:
    python -m pip install fastapi uvicorn openai
    export OPENAI_API_KEY="your-openai-key"
    export WHATSAPP_VERIFY_TOKEN="your-own-verification-token"
    # Required only for outbound WhatsApp replies:
    export WHATSAPP_ACCESS_TOKEN="your-meta-access-token"
    export WHATSAPP_PHONE_NUMBER_ID="your-meta-phone-number-id"
    export WHATSAPP_GRAPH_VERSION="your-supported-graph-api-version"
    uvicorn app:app --host 127.0.0.1 --port 8000

Before public deployment, add Meta signature verification, authentication for
/chat, durable storage, webhook deduplication, and a reliable task queue.
BackgroundTasks acknowledges before processing but does not guarantee delivery.
"""

import json
import logging
import os
from collections import defaultdict
from decimal import Decimal
from threading import Lock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# === 1. DATA LAYER ===

FLEET = {
    "Lagoon 450": {"capacity": 12, "daily_rate": 1800},
    "Sunseeker 60": {"capacity": 12, "daily_rate": 3800},
}
CONVERSATIONS: dict[str, list[ChatCompletionMessageParam]] = {}
PHONE_LOCKS = defaultdict(Lock)


# === 2. DOMAIN LOGIC / TOOL LAYER ===

def get_quote(boat_name: str, days: int = 1) -> str:
    if not isinstance(boat_name, str) or boat_name not in FLEET:
        logger.warning("Quote rejected: unknown boat")
        return json.dumps({"error": "Unknown boat.", "available_boats": list(FLEET)})
    if type(days) is not int or days < 1:
        logger.warning("Quote rejected: days must be a positive integer")
        return json.dumps({"error": "days must be a positive integer."})

    boat = FLEET[boat_name]
    base = Decimal(boat["daily_rate"]) * days
    vat = base * Decimal("0.20")
    apa = base * Decimal("0.30")
    total = base + vat + apa
    return json.dumps({
        "boat_name": boat_name,
        "capacity": boat["capacity"],
        "days": days,
        "currency": "EUR",
        "daily_rate": boat["daily_rate"],
        "base": float(base),
        "vat": float(vat),
        "apa": float(apa),
        "total": float(total),
    })


TOOLS: list[ChatCompletionToolParam] = [{
    "type": "function",
    "function": {
        "name": "get_quote",
        "description": "Calculate a yacht quote including 20% French VAT and 30% APA.",
        "parameters": {
            "type": "object",
            "properties": {
                "boat_name": {"type": "string", "enum": list(FLEET)},
                "days": {"type": "integer", "minimum": 1, "default": 1},
            },
            "required": ["boat_name"],
            "additionalProperties": False,
        },
    },
}]


# === 3. ORCHESTRATION LAYER ===

client = OpenAI(timeout=30.0, max_retries=1)
SYSTEM_PROMPT = (
    "You are a concise, friendly Yacht Concierge at Port Vauban, France. "
    f"Available fleet: {json.dumps(FLEET)}. "
    "Always use get_quote for prices; never invent rates or calculate totals. "
    "Ask for the boat or duration if unclear. Explain VAT and APA separately. "
    "A quote is not a booking or confirmation of availability."
)


def orchestrate_chat(phone: str, user_message: str) -> str:
    # Serialize turns for each phone; commit only a successfully completed turn.
    with PHONE_LOCKS[phone]:
        history = list(CONVERSATIONS.get(
            phone, [{"role": "system", "content": SYSTEM_PROMPT}]
        ))
        history.append({"role": "user", "content": user_message})
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=history, tools=TOOLS
        )
        message = response.choices[0].message

        if message.tool_calls:
            tool_calls = [
                call for call in message.tool_calls if call.type == "function"
            ]
            if len(tool_calls) != len(message.tool_calls):
                raise RuntimeError("OpenAI returned an unsupported tool call type.")
            history.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            })
            for call in tool_calls:
                try:
                    if call.function.name != "get_quote":
                        raise ValueError("Unknown tool.")
                    arguments = json.loads(call.function.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                    result = get_quote(**arguments)
                except (ValueError, TypeError) as exc:
                    logger.warning("Tool execution rejected: %s", exc)
                    result = json.dumps({"error": str(exc)})
                history.append({
                    "role": "tool", "tool_call_id": call.id, "content": result
                })

            # Disable further tool calls to keep this prototype to two completions.
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=history,
                tools=TOOLS,
                tool_choice="none",
            )
            message = response.choices[0].message

        if not message.content:
            raise RuntimeError("OpenAI returned no text reply.")
        history.append({"role": "assistant", "content": message.content})
        CONVERSATIONS[phone] = history
        return message.content


# === 4. INGESTION / TRANSPORT LAYER ===

app = FastAPI(title="Port Vauban Yacht Concierge")


class ChatInput(BaseModel):
    phone: str = Field(min_length=1, pattern=r"\S")
    message: str = Field(min_length=1, pattern=r"\S")


@app.post("/chat")
def chat(payload: ChatInput) -> dict[str, str]:
    return {"reply": orchestrate_chat(payload.phone, payload.message)}


def reply_on_whatsapp(phone: str, text: str) -> None:
    # This synchronous function runs in BackgroundTasks' thread pool, not the ACK.
    try:
        token = os.environ["WHATSAPP_ACCESS_TOKEN"]
        phone_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        version = os.environ["WHATSAPP_GRAPH_VERSION"]
        reply = orchestrate_chat(phone, text)
        request = Request(
            f"https://graph.facebook.com/{version}/{phone_id}/messages",
            data=json.dumps({
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": reply},
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            response.read()
    except HTTPError as exc:
        # urllib's traceback omits Meta's JSON error response.
        detail = exc.read().decode("utf-8", errors="replace")
        detail = detail.replace(token, "[REDACTED]").replace(phone, "[PHONE]")
        logger.error("WhatsApp API rejected reply (HTTP %s): %s", exc.code, detail)
        raise
    except Exception:
        # The HTTP acknowledgment is already sent; preserve the failure in logs.
        logger.exception("WhatsApp background processing failed")
        raise


@app.post("/webhook")
async def webhook(payload: dict, background_tasks: BackgroundTasks) -> dict[str, str]:
    # Queue text messages only; delivery/read status notifications need no reply.
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if message.get("type") == "text":
                    background_tasks.add_task(
                        reply_on_whatsapp, message["from"], message["text"]["body"]
                    )
    return {"status": "ok"}


@app.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    verify_token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> str:
    expected_token = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if not expected_token:
        raise HTTPException(status_code=503, detail="Verification token not configured.")
    if mode != "subscribe" or verify_token != expected_token:
        raise HTTPException(status_code=403, detail="Verification failed.")
    return challenge


# Ready-to-run tests (after starting Uvicorn with OPENAI_API_KEY):
# curl -sS http://127.0.0.1:8000/chat \
#   -H 'Content-Type: application/json' \
#   -d '{"phone":"+33612345678","message":"Quote a Lagoon 450 for 2 days."}'
# curl -sS http://127.0.0.1:8000/chat \
#   -H 'Content-Type: application/json' \
#   -d '{"phone":"+33612345678","message":"And a Sunseeker 60 for 3 days?"}'
