#!/usr/bin/env bash
# Point @fivecent_test_bot at the API webhook.
# Usage:
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... ./scripts/set_telegram_webhook.sh
#   API_BASE=https://fivecent-software.onrender.com ./scripts/set_telegram_webhook.sh
set -euo pipefail
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"
API_BASE="${API_BASE:-https://fivecent-software.onrender.com}"
URL="${API_BASE%/}/telegram/webhook"
ARGS=(-sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" -d "url=${URL}" -d "drop_pending_updates=true")
if [[ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ]]; then
  ARGS+=(-d "secret_token=${TELEGRAM_WEBHOOK_SECRET}")
fi
curl "${ARGS[@]}"
echo
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
echo
