"""Internal Streamlit admin. Talks only to the FastAPI /internal API.

    export ADMIN_API_TOKEN=...
    uvicorn fivecent.api.app:app --host 127.0.0.1 --port 8000
    streamlit run admin/app.py
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

FLAG_LABELS = [
    ("enable_instant_quote", "Instant quotes"),
    ("enable_soft_hold", "24h soft holds"),
    ("enable_after_hours_only", "After hours only (19:00–08:00 Paris)"),
    ("enable_tandem_charter_upsell", "Tandem upsell when pax > 12"),
    ("enable_perk_negotiation", "Offer perks when below floor"),
    ("require_email_for_quote", "Require email before quoting"),
]


def _client() -> httpx.Client:
    base = st.session_state.get("api_base", "http://127.0.0.1:8000").rstrip("/")
    token = st.session_state.get("admin_token", "")
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )


def _error(response: httpx.Response) -> None:
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    st.error(f"{response.status_code}: {detail}")


def api_request(method: str, path: str, **kwargs: Any) -> httpx.Response | None:
    try:
        with _client() as client:
            return client.request(method, path, **kwargs)
    except httpx.ConnectError:
        st.error(
            "Cannot reach the API (connection refused). "
            "Wait until uvicorn is up, check the API base URL, then click Reload agencies."
        )
        return None


def list_tenants() -> list[dict[str, Any]]:
    response = api_request("GET", "/internal/tenants")
    if response is None:
        return []
    if response.status_code != 200:
        _error(response)
        return []
    return response.json()


def get_tenant(tenant_id: str) -> dict[str, Any] | None:
    response = api_request("GET", f"/internal/tenants/{tenant_id}")
    if response is None:
        return None
    if response.status_code != 200:
        _error(response)
        return None
    return response.json()


st.set_page_config(page_title="Fivecent internal admin", layout="wide")
st.title("Fivecent internal admin")
st.caption("Operator-only. Agencies cannot see this. Changes go through the FastAPI admin API.")

with st.sidebar:
    st.subheader("Connection")
    st.text_input("API base URL", value=os.environ.get("ADMIN_API_BASE", "http://127.0.0.1:8000"), key="api_base")
    st.text_input(
        "Admin token",
        value=os.environ.get("ADMIN_API_TOKEN", ""),
        type="password",
        key="admin_token",
        help="Same value as ADMIN_API_TOKEN on the API process.",
    )
    if st.button("Reload agencies"):
        st.session_state.pop("tenants", None)

if not st.session_state.get("admin_token"):
    st.info("Set ADMIN_API_TOKEN in the sidebar (and on uvicorn).")
    st.stop()

tenants = list_tenants()
if not tenants:
    st.warning("No agencies returned. Create one below, or check the token and that uvicorn is running.")

labels = {
    t["tenant_id"]: f"{t['agency_name']} ({t['tenant_id']})"
    for t in tenants
}
ids = list(labels.keys())
selected = st.selectbox(
    "Agency",
    options=ids,
    format_func=lambda tid: labels.get(tid) or str(tid),
    index=0 if ids else None,
    placeholder="No agencies yet",
)

create_tab, identity_tab, money_tab, ai_service_tab, flags_tab, brokers_tab = st.tabs(
    ["New agency", "Identity", "Money", "AI & service", "Flags", "Brokers"]
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

if not selected:
    st.stop()

tenant = get_tenant(selected)
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
            edited[key] = st.checkbox(label, value=bool(flags.get(key, False)), key=f"flag_{key}")
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

with brokers_tab:
    st.subheader("Brokers")
    rows = tenant.get("brokers") or []
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
        for broker in rows:
            col_a, col_b = st.columns([3, 1])
            col_a.write(f"{broker['name']} · {broker['phone_number']} · {broker['role']}")
            label = "Deactivate" if broker.get("is_active") else "Reactivate"
            if col_b.button(label, key=f"toggle_{broker['broker_id']}"):
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
        telegram = st.text_input("Telegram chat id")
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
