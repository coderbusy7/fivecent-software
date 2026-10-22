# Production operations (ED-8)

## Topology

| Piece | Render service | Role |
| --- | --- | --- |
| Ingestion API | `fivecent-api` | WhatsApp/Telegram webhooks, `/internal`, `/live`, `/ready` |
| Worker | `fivecent-worker` | ARQ `process_inbound` + `flush_broker_alerts` cron |
| Admin | `fivecent-admin` | Streamlit operator UI (Bearer to API) |
| Postgres | `fivecent-db` | Durable state |
| Redis | `fivecent-redis` | Locks, debounce, ARQ queue (`maxmemory-policy=noeviction`) |

Blueprint: `render.yaml`.

## Release

1. Set secrets (`ADMIN_API_TOKEN`, WhatsApp, Telegram, LLM keys).
2. Deploy; API `preDeployCommand` runs `python -m fivecent.adapters.persistence.migrate`.
3. Confirm `/live` 200 and `/ready` 200 before pointing Meta/Telegram webhooks.
4. Smoke: signed WhatsApp test webhook → worker processes → reply; admin login; soft-hold alert.

Rollback: redeploy the previous commit. Only `alembic downgrade` when the prior release expects the older schema.

## Probes

- `/live` — process up (restart loops).
- `/ready` — Postgres (+ Redis in `APP_ENV=production`) reachable.
- `/health` — configuration snapshot (not a readiness gate).

Production startup rejects sqlite, missing Redis, non-`arq` queue, seed phone id, and missing webhook secrets.

## Backups / recovery

| Objective | Target |
| --- | --- |
| RPO | ≤ 24h (Render Postgres daily backup; prefer PITR on paid plans) |
| RTO | ≤ 2h to restore DB + redeploy API/worker/admin |

Restore drill:

1. Create a fresh Postgres instance from the latest backup/snapshot.
2. Point a staging API/worker at the restored `DATABASE_URL` and shared Redis.
3. Run migrate (should be no-op at head), hit `/ready`, replay one failed inbound if needed (`POST /internal/inbound/{id}/replay`).
4. Record the drill date in the runbook after a successful restore.

Accepted webhook work survives API restart because payloads are stored before enqueue and workers claim with retryable processing status (ED-1/ED-5).

## Capacity & coordination (ED-12)

| Target | Value |
| --- | --- |
| Concurrent conversations (representative) | 50 |
| Webhook ACK SLO | ≤ 2s |
| Queue age SLO | ≤ 30s before worker start |
| Worker `max_jobs` | `WORKER_MAX_JOBS` (default 10) |
| Per-tenant turn rate | `TENANT_RATE_LIMIT_PER_MINUTE` (default 60) |
| Daily token budget | `TENANT_DAILY_TOKEN_BUDGET` (default 500000) |

Redis conversation locks renew while held (`LOCK_TTL_SECONDS=120`, renew every 60s). Memory cache purges expired human/window/outbound entries on the nightly retention cron.

## Observability & retention (ED-13)

- `GET /internal/metrics` — failed inbound/messages, in-flight work, load targets (config presence ≠ delivery success).
- `GET /internal/tenants/{id}/usage` — daily token/cost estimates.
- Retention cron (`purge_cache_and_retention` at 03:15 UTC) and `POST /internal/ops/retention/run`:
  - messages: `RETENTION_MESSAGES_DAYS` (default 365)
  - inbound payloads: `RETENTION_INBOUND_PAYLOADS_DAYS` (default 90)
  - audit log: `RETENTION_AUDIT_DAYS` (default 730)
- Conversation export/delete under agency-admin authorization with audit records.

## Operator inbox & roles (ED-6 / ED-7 / ED-17)

- Inbox: `/internal/tenants/{id}/conversations` + takeover / resume-bot / manual reply.
- HITL mute is durable (`conversations.bot_muted_until`) plus Redis accelerator; resume clears both.
- Named users: `POST /internal/users` (platform admin, company roles only); bearer tokens hashed at rest; disable clears the hash.
- Portal roles (`client_user`, `agency_ops`): invite/provision under `/internal/portal/*` — see `docs/dashboards.md`.
- Env `ADMIN_API_TOKEN` remains a platform-admin escape hatch for the pilot.
- Admin Ops tab surfaces `/live`, `/ready`, `/health`, failed inbound replay (audited), and alert outbox.
