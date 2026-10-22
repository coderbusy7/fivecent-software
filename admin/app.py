"""Internal Streamlit admin. Talks only to the FastAPI /internal API.

    # Configure ADMIN_USERNAME, ADMIN_PASSWORD_HASH and ADMIN_API_TOKEN first.
    uvicorn fivecent.api.app:app --host 127.0.0.1 --port 8000
    streamlit run admin/app.py
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import streamlit as st

from fivecent.admin_auth import AdminSettings, LoginLimiter, session_valid

FLAG_LABELS = [
    ("enable_instant_quote", "Instant quotes"),
    ("enable_soft_hold", "24h soft holds"),
    ("enable_after_hours_only", "After hours only (19:00–08:00 Paris)"),
    ("enable_tandem_charter_upsell", "Tandem upsell when pax > 12"),
    ("enable_perk_negotiation", "Offer perks when below floor"),
    ("require_email_for_quote", "Require email before quoting"),
]


def _client() -> httpx.Client:
    base = admin_settings.api_base.rstrip("/")
    token = admin_settings.api_token
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )


def _error(response: httpx.Response) -> None:
    try:
        body = response.json()
        detail = body.get("detail", response.text) if isinstance(body, dict) else response.text
    except ValueError:
        detail = response.text
    st.error(f"{response.status_code}: {detail}")


def api_request(method: str, path: str, **kwargs: Any) -> httpx.Response | None:
    if not session_valid(st.session_state.get("admin_session"), admin_settings):
        _sign_out("Your session expired. Please sign in again.")
        st.rerun()
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            _sign_out("Admin API access was denied. Ask your operator to check ADMIN_API_TOKEN.")
            st.rerun()
        return response
    except httpx.RequestError:
        st.error(
            "Cannot reach the admin API. Check that the backend is running and "
            "ADMIN_API_BASE is correct, then try again."
        )
        return None


def list_tenants() -> list[dict[str, Any]] | None:
    response = api_request("GET", "/internal/tenants", params={"limit": 200})
    if response is None:
        return None
    if response.status_code != 200:
        _error(response)
        return None
    body = response.json()
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        items = body.get("items")
        return items if isinstance(items, list) else []
    return []


def get_tenant(tenant_id: str) -> dict[str, Any] | None:
    response = api_request("GET", f"/internal/tenants/{tenant_id}")
    if response is None:
        return None
    if response.status_code != 200:
        _error(response)
        return None
    return response.json()


def _sign_out(message: str = "") -> None:
    st.session_state.clear()
    if message:
        st.session_state["login_notice"] = message


@st.cache_resource
def _login_limiter() -> LoginLimiter:
    return LoginLimiter()


def _sign_in() -> None:
    username = st.session_state.pop("login_username", "")
    password = st.session_state.pop("login_password", "")
    accepted, limited = _login_limiter().authenticate(username, password, admin_settings)
    if accepted:
        now = time.monotonic()
        st.session_state["admin_session"] = {
            "fingerprint": admin_settings.fingerprint,
            "started": now,
            "last_seen": now,
        }
        st.session_state.pop("login_notice", None)
    elif limited:
        st.session_state["login_notice"] = "Too many failed attempts. Try again in one minute."
    else:
        st.session_state["login_notice"] = "Invalid username or password."


st.set_page_config(
    page_title="Fivecent | Admin", page_icon=":material/admin_panel_settings:", layout="wide"
)
admin_settings = AdminSettings()
try:
    admin_settings.validate_configuration()
except ValueError as exc:
    st.title("Fivecent admin")
    st.error(f"Admin sign-in is not configured. {exc}")
    st.info("Generate a password hash with: python -m fivecent.admin_auth")
    st.stop()

session = st.session_state.get("admin_session")
if not session_valid(session, admin_settings):
    if session is not None:
        _sign_out("Your session expired. Please sign in again.")
    _, login_column, _ = st.columns([1, 2, 1])
    with login_column:
        st.title("Fivecent")
        st.subheader("Company admin sign in")
        st.caption("Secure operator access to your agencies, brokers and messaging settings.")
        if notice := st.session_state.pop("login_notice", ""):
            st.error(notice)
        with st.form("admin_login"):
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            st.form_submit_button("Sign in", on_click=_sign_in, use_container_width=True)
        st.caption("Restricted to authorized company staff.")
        st.info("Client agency users: open **Client portal** in the sidebar (separate login).")
    st.stop()

st.session_state["admin_session"]["last_seen"] = time.monotonic()

with st.sidebar:
    st.title("Fivecent")
    st.caption("COMPANY ADMIN WORKSPACE")
    st.write(f"Signed in as **{admin_settings.username}**")
    st.button("Sign out", on_click=_sign_out, use_container_width=True)
    st.divider()
    st.subheader("Agencies")
    st.button("Reload agencies", use_container_width=True)
    st.caption("Sessions expire after 30 minutes of inactivity or 8 hours total.")
    st.caption("Client analytics: use the **Client portal** page (separate entry).")

st.title("Agency workspace")
st.caption("Manage agency identity, financial guardrails, AI services and broker connections.")

tenants = list_tenants()
if tenants is None:
    st.stop()

agency_count, active_count, broker_count = st.columns(3)
agency_count.metric("Agencies", len(tenants))
active_count.metric("Active agencies", sum(bool(t.get("is_active")) for t in tenants))
broker_count.metric("Brokers", sum(len(t.get("brokers") or []) for t in tenants))
st.divider()
if not tenants:
    st.info("No agencies yet. Use New agency below to create your first agency.")

labels = {
    t["tenant_id"]: f"{t['agency_name']} ({t['tenant_id']})"
    for t in tenants
}
ids = list(labels.keys())
selected = st.sidebar.selectbox(
    "Agency",
    options=ids,
    format_func=lambda tid: labels.get(tid) or str(tid),
    index=0 if ids else None,
    placeholder="No agencies yet",
)

tenant = get_tenant(selected) if selected else None
if selected and tenant is None:
    st.stop()
if tenant is not None:
    st.subheader(tenant["agency_name"])
    st.caption(
        f"{tenant['tenant_id']} | {tenant['home_port']} | "
        f"{'Active' if tenant.get('is_active') else 'Inactive'}"
    )

(
    create_tab,
    setup_tab,
    inbox_tab,
    identity_tab,
    money_tab,
    fleet_tab,
    ai_service_tab,
    flags_tab,
    telegram_tab,
    brokers_tab,
    users_tab,
    ops_tab,
) = st.tabs(
    [
        "New agency",
        "Setup",
        "Inbox",
        "Identity",
        "Money",
        "Fleet",
        "AI & service",
        "Flags",
        "Telegram",
        "Brokers",
        "Users",
        "Ops",
    ]
)

with create_tab:
    st.subheader("Add agency")
    st.caption("Required fields must be filled before create. Unsaved form values are lost on navigation.")
    with st.form("create_tenant"):
        tenant_id = st.text_input("Tenant id", placeholder="cannes_luxury")
        agency_name = st.text_input("Agency name")
        home_port = st.text_input("Home port", value="Port Vauban")
        phone_id = st.text_input("WhatsApp phone_number_id")
        waba_id = st.text_input("WhatsApp waba_id")
        service_type = st.selectbox(
            "Service type",
            options=["yachting"],
            help="Only registered service modules are listed.",
        )
        llm_provider = st.selectbox(
            "LLM provider",
            options=["openai", "anthropic"],
            help="Must match a configured provider plugin on the API.",
        )
        llm_model = st.text_input(
            "Model",
            value="gpt-4o-mini",
            help="Provider-specific model id used for this agency.",
        )
        submitted = st.form_submit_button("Create")
    if submitted:
        with st.spinner("Creating agency…"):
            response = api_request(
                "POST",
                "/internal/tenants",
                json={
                    "tenant_id": tenant_id.strip(),
                    "agency_name": agency_name.strip(),
                    "home_port": home_port.strip(),
                    "whatsapp_phone_number_id": phone_id.strip(),
                    "whatsapp_waba_id": waba_id.strip(),
                    "service_type": service_type,
                    "llm_provider": llm_provider,
                    "llm_model": llm_model.strip(),
                },
            )
        if response is None:
            st.stop()
        if response.is_success:
            st.session_state["flash_success"] = f"Created {tenant_id.strip()}"
            st.rerun()
        else:
            _error(response)

if flash := st.session_state.pop("flash_success", None):
    st.success(flash)

if tenant is None:
    st.stop()

with inbox_tab:
    st.subheader("Conversation inbox")
    st.caption("Search, open transcripts, take over or resume the bot. Manual replies mute the bot for 24h.")
    q = st.text_input("Search conversations", placeholder="phone, channel, or conversation id")
    inbox_resp = api_request(
        "GET",
        f"/internal/tenants/{selected}/conversations",
        params={"q": q.strip(), "limit": 50},
    )
    if inbox_resp is None:
        st.stop()
    if not inbox_resp.is_success:
        _error(inbox_resp)
    else:
        inbox = inbox_resp.json()
        items = inbox.get("items") or []
        if not items:
            st.info("No conversations yet for this agency.")
        else:
            st.caption(f"{inbox.get('total', len(items))} conversation(s)")
            labels = {
                row["conversation_id"]: (
                    f"{row.get('user_channel_id')} · {row.get('handoff_status')} · "
                    f"{row.get('last_status') or 'n/a'}"
                )
                for row in items
            }
            picked = st.selectbox(
                "Conversation",
                options=list(labels.keys()),
                format_func=lambda cid: labels.get(cid, cid),
            )
            detail_resp = api_request(
                "GET", f"/internal/tenants/{selected}/conversations/{picked}"
            )
            if detail_resp is None:
                st.stop()
            if not detail_resp.is_success:
                _error(detail_resp)
            else:
                detail = detail_resp.json()
                commercial = detail.get("commercial") or {}
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Handoff", detail.get("handoff_status") or "open")
                col_b.metric("Human active", "yes" if detail.get("human_active") else "no")
                lead = commercial.get("lead") or {}
                col_c.metric("Lead", lead.get("state") or "—")
                if commercial.get("quote"):
                    st.write(
                        f"Quote `{commercial['quote'].get('quote_id')}` · "
                        f"{commercial['quote'].get('total')} {commercial['quote'].get('currency')}"
                    )
                if commercial.get("hold"):
                    st.write(
                        f"Hold `{commercial['hold'].get('hold_id')}` expires "
                        f"{commercial['hold'].get('expires_at')}"
                    )
                messages = detail.get("messages") or []
                if not messages:
                    st.info("No messages in this conversation yet.")
                else:
                    for msg in messages[-40:]:
                        st.markdown(
                            f"**{msg.get('role')}** · `{msg.get('processing_status')}`  \n"
                            f"{msg.get('text') or ''}"
                        )
                actions = st.columns(4)
                if actions[0].button("Take over", key=f"takeover_{picked}"):
                    resp = api_request(
                        "POST",
                        f"/internal/tenants/{selected}/conversations/{picked}/takeover",
                    )
                    if resp and resp.is_success:
                        st.session_state["flash_success"] = "Bot muted — you own this thread"
                        st.rerun()
                    elif resp:
                        _error(resp)
                if actions[1].button("Resume bot", key=f"resume_{picked}"):
                    resp = api_request(
                        "POST",
                        f"/internal/tenants/{selected}/conversations/{picked}/resume-bot",
                    )
                    if resp and resp.is_success:
                        st.session_state["flash_success"] = "Bot resumed"
                        st.rerun()
                    elif resp:
                        _error(resp)
                if actions[2].button("Mark resolved", key=f"resolve_{picked}"):
                    resp = api_request(
                        "PATCH",
                        f"/internal/tenants/{selected}/conversations/{picked}",
                        json={"handoff_status": "resolved"},
                    )
                    if resp and resp.is_success:
                        st.session_state["flash_success"] = "Marked resolved"
                        st.rerun()
                    elif resp:
                        _error(resp)
                if actions[3].button("Export JSON", key=f"export_{picked}"):
                    resp = api_request(
                        "POST",
                        f"/internal/tenants/{selected}/conversations/{picked}/export",
                    )
                    if resp and resp.is_success:
                        st.download_button(
                            "Download export",
                            data=str(resp.json()),
                            file_name=f"{picked}.json",
                            mime="application/json",
                        )
                    elif resp:
                        _error(resp)
                with st.form(f"manual_reply_{picked}"):
                    reply_text = st.text_area("Manual reply (mutes bot)")
                    send_reply = st.form_submit_button("Send reply")
                if send_reply:
                    if not reply_text.strip():
                        st.error("Reply text is required.")
                    else:
                        resp = api_request(
                            "POST",
                            f"/internal/tenants/{selected}/conversations/{picked}/reply",
                            json={"text": reply_text.strip()},
                        )
                        if resp is None:
                            st.stop()
                        if resp.is_success:
                            st.session_state["flash_success"] = "Reply sent"
                            st.rerun()
                        else:
                            _error(resp)

with setup_tab:
    st.subheader("Onboarding checklist")
    st.caption("Connection checks never display secret token values.")
    status_resp = api_request("GET", f"/internal/tenants/{selected}/setup-status")
    if status_resp is None:
        st.stop()
    if status_resp.is_success:
        body = status_resp.json()
        badge = body.get("status", "draft")
        if badge == "ready":
            st.success("Agency status: ready")
        elif badge == "misconfigured":
            st.warning("Agency status: misconfigured")
        else:
            st.info("Agency status: draft")
        checks = body.get("checks") or {}
        for key, value in checks.items():
            st.write(f"- `{key}`: {'yes' if value else 'no'}" if isinstance(value, bool) else f"- `{key}`: {value}")
    else:
        _error(status_resp)

with identity_tab:
    st.subheader(tenant["agency_name"])
    with st.form("identity"):
        agency_name = st.text_input("Agency name", value=tenant["agency_name"])
        home_port = st.text_input("Home port", value=tenant["home_port"])
        phone_id = st.text_input("WhatsApp phone_number_id", value=tenant["whatsapp_phone_number_id"])
        waba_id = st.text_input("WhatsApp waba_id", value=tenant.get("whatsapp_waba_id") or "")
        routing = st.selectbox(
            "Broker routing",
            options=["broadcast", "round_robin"],
            index=0 if tenant.get("routing_mode") != "round_robin" else 1,
        )
        is_active = st.checkbox("Agency active", value=bool(tenant.get("is_active", True)))
        save = st.form_submit_button("Save identity")
    if save:
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}",
            json={
                "agency_name": agency_name,
                "home_port": home_port,
                "whatsapp_phone_number_id": phone_id,
                "whatsapp_waba_id": waba_id,
                "routing_mode": routing,
                "is_active": is_active,
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Saved identity")
            st.rerun()
        else:
            _error(response)

with fleet_tab:
    st.subheader("Fleet inventory")
    st.caption(
        "Boat ids are namespaced per agency (`{tenant}__{boat}`) so two agencies "
        "can both operate a Lagoon 450 without colliding."
    )
    fleet_resp = api_request("GET", f"/internal/tenants/{selected}/fleet")
    if fleet_resp is None:
        st.stop()
    if fleet_resp.is_success:
        boats = fleet_resp.json()
        if not isinstance(boats, list):
            boats = []
        if not boats:
            st.info("No boats yet. Seed a starter fleet or add one below.")
        else:
            for boat in boats:
                if not isinstance(boat, dict):
                    continue
                state = "active" if boat.get("active") else "inactive"
                st.write(
                    f"- `{boat.get('boat_id')}` · {boat.get('name')} · "
                    f"{boat.get('capacity')} pax · low {boat.get('low_season_daily_rate')} · {state}"
                )
        cols = st.columns(2)
        with cols[0]:
            if st.button("Seed starter fleet (Lagoon + Sunseeker)"):
                seeded = api_request(
                    "POST", f"/internal/tenants/{selected}/fleet/seed-starter"
                )
                if seeded is None:
                    st.stop()
                if seeded.is_success:
                    st.success("Starter fleet ready")
                    st.rerun()
                else:
                    _error(seeded)
        with cols[1]:
            st.caption("Deactivate a boat instead of deleting historical holds.")
    else:
        _error(fleet_resp)

    with st.form("fleet_upsert"):
        boat_id = st.text_input("Boat id", placeholder="lagoon-450")
        boat_name = st.text_input("Name", placeholder="Lagoon 450")
        length_m = st.number_input("Length (m)", min_value=1.0, value=14.0, step=0.1)
        capacity = st.number_input("Capacity", min_value=1, max_value=12, value=12, step=1)
        low = st.number_input("Low season daily rate", min_value=0.0, value=1800.0, step=50.0)
        high = st.number_input("High season daily rate", min_value=0.0, value=2200.0, step=50.0)
        skipper = st.number_input("Skipper daily rate", min_value=0.0, value=350.0, step=10.0)
        active = st.checkbox("Active", value=True)
        save_boat = st.form_submit_button("Save boat")
    if save_boat:
        response = api_request(
            "POST",
            f"/internal/tenants/{selected}/fleet",
            json={
                "boat_id": boat_id.strip(),
                "name": boat_name.strip(),
                "length_m": str(length_m),
                "capacity": int(capacity),
                "low_season_daily_rate": str(low),
                "high_season_daily_rate": str(high),
                "skipper_daily_rate": str(skipper),
                "active": active,
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success(f"Saved {response.json().get('boat_id')}")
            st.rerun()
        else:
            _error(response)

    with st.form("fleet_deactivate"):
        deactivate_id = st.text_input("Boat id to deactivate/reactivate")
        make_active = st.checkbox("Set active", value=False)
        toggle = st.form_submit_button("Update active flag")
    if toggle and deactivate_id.strip():
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}/fleet/{deactivate_id.strip()}",
            json={"active": make_active},
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Updated boat active flag")
            st.rerun()
        else:
            _error(response)

with money_tab:
    st.subheader("Financial guardrails")
    with st.form("money"):
        vat = st.number_input("VAT rate", min_value=0.0, max_value=1.0, value=float(tenant["vat_rate"]), step=0.01)
        apa = st.number_input("APA rate", min_value=0.0, max_value=1.0, value=float(tenant["apa_rate"]), step=0.01)
        discount = st.number_input(
            "Max base concession",
            min_value=0.0,
            max_value=0.5,
            value=float(tenant["max_discount_pct"]),
            step=0.01,
        )
        currency = st.text_input("Currency", value=tenant.get("currency") or "EUR")
        save_money = st.form_submit_button("Save money rules")
    if save_money:
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}",
            json={
                "vat_rate": str(vat),
                "apa_rate": str(apa),
                "max_discount_pct": str(discount),
                "currency": currency,
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Saved money rules")
            st.rerun()
        else:
            _error(response)

with ai_service_tab:
    st.subheader("Service and reasoning provider")
    with st.form("ai_service"):
        service_type = st.selectbox(
            "Service type",
            options=["yachting"],
            index=0,
            help="Only registered service modules can be selected.",
        )
        llm_provider = st.selectbox(
            "LLM provider",
            options=["openai", "anthropic"],
            index=0 if tenant["llm_provider"] == "openai" else 1,
        )
        llm_model = st.text_input(
            "Model",
            value=str(tenant.get("llm_model") or "gpt-4o-mini"),
        ) or ""
        save_ai_service = st.form_submit_button("Save AI and service")
    if save_ai_service:
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}",
            json={
                "service_type": service_type,
                "llm_provider": llm_provider,
                "llm_model": llm_model.strip(),
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Saved AI and service settings")
            st.rerun()
        else:
            _error(response)

with flags_tab:
    st.subheader("Feature flags")
    flags = dict(tenant.get("feature_flags") or {})
    with st.form("flags"):
        edited = {}
        for key, label in FLAG_LABELS:
            edited[key] = st.checkbox(
                label, value=bool(flags.get(key, False)), key=f"flag_{selected}_{key}"
            )
        start = st.text_input("After-hours start (HH:MM)", value=str(flags.get("after_hours_start", "19:00")))
        end = st.text_input("After-hours end (HH:MM)", value=str(flags.get("after_hours_end", "08:00")))
        save_flags = st.form_submit_button("Save flags")
    if save_flags:
        edited["after_hours_start"] = start
        edited["after_hours_end"] = end
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}",
            json={"feature_flags": edited},
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Saved flags")
            st.rerun()
        else:
            _error(response)

with telegram_tab:
    st.subheader("Dispatch bot onboarding")
    st.caption(
        "One central bot for all agencies. Generate a single-use invite, send it to the "
        "broker — they tap Start and Fivecent stores their chat_id. Invites expire in 24h."
    )
    invite = tenant.get("telegram_invite_link") or ""
    desk = tenant.get("telegram_chat_id") or ""
    if st.button("Generate desk invite", key="gen_tg_invite"):
        minted = api_request(
            "POST",
            f"/internal/tenants/{selected}/telegram-invite",
        )
        if minted is None:
            st.stop()
        if minted.is_success:
            st.success("Invite created")
            st.rerun()
        else:
            _error(minted)
    if invite:
        st.code(invite, language=None)
        st.link_button("Open invite in Telegram", invite, use_container_width=False)
    else:
        st.info("No open invite — click Generate desk invite, then send the link to the broker.")

    if desk:
        st.success(f"Desk connected · telegram_chat_id = `{desk}`")
    else:
        st.info("Desk not linked yet. Broker must open the invite link and tap Start.")

    with st.form("telegram_desk"):
        desk_input = st.text_input(
            "Desk telegram_chat_id (manual override)",
            value=desk,
            help="Usually filled by /start deep-link. Paste a chat id only if you already know it.",
        )
        save_desk = st.form_submit_button("Save desk chat id")
    if save_desk:
        response = api_request(
            "PATCH",
            f"/internal/tenants/{selected}",
            json={"telegram_chat_id": desk_input.strip() or None},
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Saved desk Telegram chat id")
            st.rerun()
        else:
            _error(response)

    st.markdown("**Mute command format**")
    st.code(f"/mute_<guest_digits>_{selected}", language=None)
    st.caption("Shown on each lead card. Sets a 24h human-active circuit breaker for that WhatsApp guest.")

    st.divider()
    st.subheader("WhatsApp test send")
    st.caption(
        "Sends a free-form Cloud API message from this agency’s phone number. "
        "Recipient must be allowed in Meta (test numbers) and ideally inside a 24h session."
    )
    with st.form("whatsapp_test_send"):
        test_phone = st.text_input(
            "Recipient phone",
            value="0033621611871",
            help="International digits; 00 or + prefix is fine.",
        )
        test_message = st.text_area(
            "Message",
            value="Fivecent test: Elena is online. Reply here to continue.",
            height=80,
        )
        send_test = st.form_submit_button("Send test WhatsApp", use_container_width=True)
    if send_test:
        response = api_request(
            "POST",
            "/internal/whatsapp/test-send",
            json={
                "phone": test_phone.strip(),
                "message": test_message.strip(),
                "tenant_id": selected,
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            body = response.json()
            st.success(f"Sent to {body.get('to')} · outbound `{body.get('outbound_id')}`")
        else:
            _error(response)

with users_tab:
    st.subheader("Named operators")
    st.caption(
        "Company staff API tokens and client portal invites. Disable revokes access immediately."
    )
    users_resp = api_request("GET", "/internal/users")
    if users_resp is None:
        st.stop()
    if users_resp.status_code == 403:
        st.info("Your role cannot manage users.")
    elif not users_resp.is_success:
        _error(users_resp)
    else:
        named = users_resp.json() or []
        if not named:
            st.info("No named users yet.")
        else:
            st.dataframe(named, hide_index=True, use_container_width=True)
            for row in named:
                if not row.get("is_active"):
                    continue
                label = f"Revoke {row.get('email')}"
                if st.button(label, key=f"revoke_{row['user_id']}"):
                    revoked = api_request(
                        "POST", f"/internal/users/{row['user_id']}/disable"
                    )
                    if revoked and revoked.is_success:
                        st.session_state["flash_success"] = f"Revoked {row.get('email')}"
                        st.rerun()
                    elif revoked:
                        _error(revoked)

        st.markdown("**Invite client portal user**")
        st.caption(
            "Creates a read-only client for this agency. Send them the invite token; "
            "they set a password on the Client portal page."
        )
        with st.form("invite_client"):
            client_email = st.text_input("Client email")
            client_name = st.text_input("Display name")
            invite_client = st.form_submit_button("Invite client")
        if invite_client:
            if not client_email.strip() or not client_name.strip():
                st.error("Email and display name are required.")
            else:
                resp = api_request(
                    "POST",
                    "/internal/portal/invite",
                    json={
                        "email": client_email.strip(),
                        "display_name": client_name.strip(),
                        "tenant_id": selected,
                    },
                )
                if resp is None:
                    st.stop()
                if resp.is_success:
                    body = resp.json()
                    st.success("Invite created — copy the token now; it is shown once.")
                    st.code(body.get("invite_token") or "", language=None)
                    st.caption(f"Expires {body.get('invite_expires_at')}")
                else:
                    _error(resp)

        with st.form("create_user"):
            st.markdown("**Create company staff user**")
            email = st.text_input("Email", key="staff_email")
            display_name = st.text_input("Display name", key="staff_name")
            role = st.selectbox(
                "Role", options=["platform_admin", "agency_admin", "broker"]
            )
            user_tenant = st.text_input(
                "Tenant id",
                value=selected if role != "platform_admin" else "",
                help="Required for agency_admin and broker.",
            )
            create_user = st.form_submit_button("Create staff user")
        if create_user:
            if not email.strip() or not display_name.strip():
                st.error("Email and display name are required.")
            else:
                resp = api_request(
                    "POST",
                    "/internal/users",
                    json={
                        "email": email.strip(),
                        "display_name": display_name.strip(),
                        "role": role,
                        "tenant_id": user_tenant.strip() or None,
                    },
                )
                if resp is None:
                    st.stop()
                if resp.is_success:
                    body = resp.json()
                    token = body.get("api_token")
                    st.success("User created — copy the API token now; it is shown once.")
                    if token:
                        st.code(token, language=None)
                    st.rerun()
                else:
                    _error(resp)
        audit_resp = api_request("GET", "/internal/audit", params={"limit": 50})
        if audit_resp and audit_resp.is_success:
            st.subheader("Recent audit")
            st.dataframe(audit_resp.json() or [], hide_index=True, use_container_width=True)

with ops_tab:
    st.subheader("Operational health")
    st.caption("Queue/failure counters and AI spend. Configuration presence ≠ successful delivery.")
    metrics_resp = api_request("GET", "/internal/metrics")
    if metrics_resp is None:
        st.stop()
    if metrics_resp.status_code == 403:
        st.info("Your role cannot view ops metrics.")
    elif not metrics_resp.is_success:
        _error(metrics_resp)
    else:
        metrics = metrics_resp.json()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Failed inbound", metrics.get("failed_inbound", 0))
        m2.metric("Failed messages", metrics.get("failed_messages", 0))
        m3.metric("In-flight", metrics.get("in_flight_messages", 0))
        m4.metric("Oldest failed", metrics.get("oldest_failed_inbound_at") or "—")
        st.json(metrics.get("load_target") or {})
    usage_resp = api_request("GET", f"/internal/tenants/{selected}/usage", params={"days": 14})
    if usage_resp and usage_resp.is_success:
        st.subheader("AI usage (14d)")
        usage_items = (usage_resp.json() or {}).get("items") or []
        if not usage_items:
            st.info("No usage recorded yet.")
        else:
            st.dataframe(usage_items, hide_index=True, use_container_width=True)
    failed_resp = api_request("GET", "/internal/inbound/failed")
    if failed_resp and failed_resp.is_success:
        failed = failed_resp.json() or []
        st.subheader("Failed inbound")
        if not failed:
            st.success("No failed inbound payloads.")
        else:
            st.dataframe(failed, hide_index=True, use_container_width=True)
            replay_id = st.text_input("Payload id to replay")
            if st.button("Replay inbound") and replay_id.strip():
                replayed = api_request("POST", f"/internal/inbound/{replay_id.strip()}/replay")
                if replayed and replayed.is_success:
                    st.session_state["flash_success"] = "Replay queued"
                    st.rerun()
                elif replayed:
                    _error(replayed)

with brokers_tab:
    st.subheader("Brokers")
    rows = tenant.get("brokers") or []
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
        for broker in rows:
            col_a, col_b, col_c = st.columns([3, 2, 1])
            col_a.write(f"{broker['name']} · {broker['phone_number']} · {broker['role']}")
            with col_b.form(f"broker_tg_{broker['broker_id']}"):
                tg = st.text_input(
                    "Telegram chat id",
                    value=broker.get("telegram_chat_id") or "",
                    key=f"tg_input_{broker['broker_id']}",
                    label_visibility="collapsed",
                    placeholder="Telegram chat id",
                )
                if st.form_submit_button("Save TG"):
                    response = api_request(
                        "PATCH",
                        f"/internal/brokers/{broker['broker_id']}",
                        json={"telegram_chat_id": tg.strip() or None},
                    )
                    if response is None:
                        st.stop()
                    if response.is_success:
                        st.rerun()
                    else:
                        _error(response)
            label = "Deactivate" if broker.get("is_active") else "Reactivate"
            if col_c.button(label, key=f"toggle_{broker['broker_id']}"):
                response = api_request(
                    "PATCH",
                    f"/internal/brokers/{broker['broker_id']}",
                    json={"is_active": not broker.get("is_active")},
                )
                if response is None:
                    st.stop()
                if response.is_success:
                    st.rerun()
                else:
                    _error(response)
    else:
        st.write("No brokers yet.")

    with st.form("add_broker"):
        st.markdown("**Add broker**")
        name = st.text_input("Name")
        phone = st.text_input("Personal WhatsApp", placeholder="+33612345678")
        telegram = st.text_input(
            "Telegram chat id (optional fallback)",
            help="Used only when the agency desk chat id is empty.",
        )
        role = st.selectbox("Role", options=["broker", "owner", "senior_broker", "assistant"])
        add = st.form_submit_button("Add broker")
    if add:
        response = api_request(
            "POST",
            f"/internal/tenants/{selected}/brokers",
            json={
                "name": name.strip(),
                "phone_number": phone.strip(),
                "telegram_chat_id": telegram.strip() or None,
                "role": role,
            },
        )
        if response is None:
            st.stop()
        if response.is_success:
            st.success("Broker added")
            st.rerun()
        else:
            _error(response)
