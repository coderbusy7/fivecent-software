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
    response = api_request("GET", "/internal/tenants")
    if response is None:
        return None
    if response.status_code != 200:
        _error(response)
        return None
    return response.json()


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
        st.subheader("Admin sign in")
        st.caption("Secure operator access to your agencies, brokers and messaging settings.")
        if notice := st.session_state.pop("login_notice", ""):
            st.error(notice)
        with st.form("admin_login"):
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            st.form_submit_button("Sign in", on_click=_sign_in, use_container_width=True)
        st.caption("Restricted to authorized operators. Contact your administrator for access.")
    st.stop()

st.session_state["admin_session"]["last_seen"] = time.monotonic()

with st.sidebar:
    st.title("Fivecent")
    st.caption("ADMIN WORKSPACE")
    st.write(f"Signed in as **{admin_settings.username}**")
    st.button("Sign out", on_click=_sign_out, use_container_width=True)
    st.divider()
    st.subheader("Agencies")
    st.button("Reload agencies", use_container_width=True)
    st.caption("Sessions expire after 30 minutes of inactivity or 8 hours total.")

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

create_tab, identity_tab, money_tab, ai_service_tab, flags_tab, telegram_tab, brokers_tab = st.tabs(
    ["New agency", "Identity", "Money", "AI & service", "Flags", "Telegram", "Brokers"]
)

with create_tab:
    st.subheader("Add agency")
    with st.form("create_tenant"):
        tenant_id = st.text_input("Tenant id", placeholder="cannes_luxury")
        agency_name = st.text_input("Agency name")
        home_port = st.text_input("Home port", value="Port Vauban")
        phone_id = st.text_input("WhatsApp phone_number_id")
        waba_id = st.text_input("WhatsApp waba_id")
        service_type = st.selectbox("Service type", options=["yachting"])
        llm_provider = st.selectbox("LLM provider", options=["openai", "anthropic"])
        llm_model = st.text_input("Model", value="gpt-4o-mini")
        submitted = st.form_submit_button("Create")
    if submitted:
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
            st.success(f"Created {tenant_id}")
            st.rerun()
        else:
            _error(response)

if tenant is None:
    st.stop()

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
        "One central bot for all agencies. Send the invite link to the broker — "
        "they tap Start and Fivecent stores their chat_id. No per-agency bot tokens."
    )
    invite = tenant.get("telegram_invite_link") or ""
    desk = tenant.get("telegram_chat_id") or ""
    if invite:
        st.code(invite, language=None)
        st.link_button("Open invite in Telegram", invite, use_container_width=False)
    else:
        st.warning("Invite link unavailable — check TELEGRAM_BOT_USERNAME on the API.")

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
