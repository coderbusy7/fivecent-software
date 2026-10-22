# Admin and client dashboards (ED-17)

## Workspaces

| Workspace | Entry | Auth |
| --- | --- | --- |
| Company admin | Streamlit `admin/app.py` | `ADMIN_USERNAME` / password hash → Bearer `ADMIN_API_TOKEN` (or named company staff token) |
| Client executive portal | Streamlit `admin/pages/Client_portal.py` | Portal email/password → short-lived API token |

Portal routes never accept a client-supplied `tenant_id`; scope always comes from the authenticated actor.

## Role / permission matrix

| Capability | `client_user` | `agency_ops` | `broker` | `agency_admin` | `platform_admin` |
| --- | --- | --- | --- | --- | --- |
| Portal funnel / overview | read | read | — (company UI) | — | — |
| Portal active holds (vessel/dates) | read | read | company inbox | company | company |
| Guest contact on holds | **deny** | allow | allow (inbox) | allow | allow |
| Portal fleet availability | read | read | company fleet | company | company |
| Fleet maintenance (`active` toggle) | **deny** | allow (portal API) | deny | allow (company) | allow |
| Company inbox / takeover / mute | deny | deny | allow | allow | allow |
| Money / WhatsApp setup / replay | deny | deny | deny | allow | allow |
| Create named company users | deny | deny | deny | deny | allow |
| Portal invite / provision | deny | deny | deny | allow | allow |

Create portal accounts with `POST /internal/portal/invite` or `POST /internal/portal/provision` (`role`: `client_user` \| `agency_ops`). Do not use `POST /internal/users` for portal roles.

## Portal API contracts

| Method | Path | Who | Notes |
| --- | --- | --- | --- |
| `GET` | `/internal/portal/overview` | portal roles | Agency identity + capability flags |
| `GET` | `/internal/portal/analytics` | portal roles | Funnel metrics; optional `date_from` / `date_to` |
| `GET` | `/internal/portal/holds` | portal roles | Active soft holds; contact redacted for `client_user` |
| `GET` | `/internal/portal/fleet` | portal roles | Inventory + `availability_status` (`available` / `on_hold` / `maintenance`) |
| `PATCH` | `/internal/portal/fleet/{boat_id}` | `agency_ops` | Body `{ "active": bool }` — `false` = maintenance/offline; audited |

## Durable inbox mute

Takeover, manual reply, WhatsApp business echo, and Telegram `/mute_` persist `conversations.bot_muted_until` (default 24h) and still set the Redis HITL key as an accelerator. Resume-bot clears both. Turn processing skips when durable mute **or** cache mute is active.

## Financial guardrails

Admin Money tab edits only persisted tenant fields: VAT, APA, `max_discount_pct`. Port-specific margin floors are **not** modeled; do not add UI-only controls.

## Secrets

WhatsApp access tokens / app secrets remain process settings. Setup and `/health` expose presence/booleans only — never plaintext secrets. Audit JSON redacts known secret keys.

## Verification

```bash
poetry run pytest tests/api/test_client_portal.py tests/api/test_inbox_and_roles.py -q
```

Local: run API + `streamlit run admin/app.py`, open **Client portal**, provision via Admin → Users.
