# Fivecent E2E chat automation

Simulate guest conversations against a running API, assert Elena’s replies, and optionally fire signed WhatsApp webhooks as the test recipient.

## Modes

| Mode | Transport | Assert LLM text? | Notes |
|------|-----------|------------------|--------|
| `chat` (default) | `POST /chat` | Yes | Best for automation. Uses channel `test`. |
| `webhook` | Signed `POST /webhook` | Status only | Full WhatsApp ingest → Graph outbound to the guest phone. |

Default test recipient: `33621611871` (`E2E_PHONE`, or `0033621611871` normalized).

## Quick start

```bash
# from repo root
./e2e/run.sh list
./e2e/run.sh run greeting
./e2e/run.sh run quote negotiate --phone 33621611871 -v
./e2e/run.sh run pilot_suite --json-out e2e/reports/pilot.json

# WhatsApp webhook path (needs WHATSAPP_APP_SECRET in .env)
./e2e/run.sh run webhook_smoke --mode webhook
```

Or:

```bash
PYTHONPATH=. .venv/bin/python -m e2e run greeting --api-base https://fivecent-software.onrender.com
```

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `E2E_API_BASE` / `API_BASE` | `https://fivecent-software.onrender.com` | Target API |
| `E2E_PHONE` | `33621611871` | Guest id / WhatsApp `from` |
| `E2E_MODE` | `chat` | `chat` or `webhook` |
| `E2E_PAUSE_SECONDS` | `1.5` | Delay between turns |
| `WHATSAPP_APP_SECRET` | from `.env` | Required for `webhook` mode |
| `WHATSAPP_PHONE_NUMBER_ID` | from `.env` | Business phone id in webhook metadata |

Tip: use a unique phone per suite run so HITL mute / repeat-greeting state does not bleed:

```bash
E2E_PHONE="337$(date +%s | tail -c 9)" ./e2e/run.sh run pilot_suite
```

## Scenarios

| Id | What it covers |
|----|----------------|
| `greeting` | Opening reply |
| `quote` | Lagoon quote / money language |
| `negotiate` | Mild discount + floor pushback |
| `other_port` | Monaco / non-home port |
| `handoff` | Broker follow-up |
| `soft_hold` | 24h soft hold with name+email |
| `pilot_suite` | Full multi-turn pilot path |
| `webhook_smoke` | Signed WhatsApp webhook acceptance |

Filter by tag: `./e2e/run.sh run --tag money`

## Adding a scenario

1. Create `e2e/scenarios/my_case.py` with a `SCENARIO = Scenario(...)`.
2. Register it in `e2e/scenarios/registry.py`.
3. Use checks from `e2e/checks.py` (`any_of`, `all_of`, `none_of`, `MinLength`, `Regex`, `StatusIn`).

## Layout

```
e2e/
  client.py       # ChatClient + WhatsAppWebhookClient
  checks.py       # Reply assertions
  config.py       # Env loading
  runner.py       # Scenario execution + reports
  cli.py          # `python -m e2e`
  scenarios/      # Use-case definitions
  run.sh
  README.md
```
