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
