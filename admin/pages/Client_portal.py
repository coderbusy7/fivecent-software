"""Client executive portal (ED-15 / ED-17).

Separate entry from the company admin workspace. Portal users sign in with
email/password provisioned by company operators — tenant is always taken from
the authenticated actor (never from a client-supplied selector).
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx
import streamlit as st
from pydantic_settings import BaseSettings, SettingsConfigDict

SESSION_IDLE_SECONDS = 30 * 60
SESSION_MAX_SECONDS = 8 * 60 * 60


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="ADMIN_", extra="ignore"
    )

    api_base: str = "http://127.0.0.1:8000"


def _api_base() -> str:
    return PortalSettings().api_base.rstrip("/")


def _sign_out(message: str = "") -> None:
    st.session_state.pop("portal_session", None)
    if message:
        st.session_state["portal_notice"] = message


def _session_ok(session: object) -> bool:
    if not isinstance(session, dict):
        return False
    now = time.monotonic()
    started = session.get("started")
    last_seen = session.get("last_seen")
    token = session.get("api_token")
    if not token or not isinstance(started, (int, float)) or not isinstance(last_seen, (int, float)):
        return False
    if now - started > SESSION_MAX_SECONDS:
        return False
    if now - last_seen > SESSION_IDLE_SECONDS:
        return False
    return True


def _portal_request(method: str, path: str, **kwargs: Any) -> httpx.Response | None:
    session = st.session_state.get("portal_session")
    if not _session_ok(session):
        _sign_out("Your session expired. Please sign in again.")
        st.rerun()
    assert isinstance(session, dict)
    try:
        with httpx.Client(
            base_url=_api_base(),
            headers={"Authorization": f"Bearer {session['api_token']}"},
            timeout=15.0,
        ) as client:
            response = client.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            _sign_out("Access was denied or revoked. Sign in again or contact your agency admin.")
            st.rerun()
        return response
    except httpx.RequestError:
        st.error("Cannot reach the API. Try again shortly.")
        return None


def _error(response: httpx.Response) -> None:
    try:
        body = response.json()
        detail = body.get("detail", response.text) if isinstance(body, dict) else response.text
    except ValueError:
        detail = response.text
    st.error(f"{response.status_code}: {detail}")


def _store_session(body: dict[str, Any]) -> None:
    now = time.monotonic()
    st.session_state["portal_session"] = {
        "api_token": body["api_token"],
        "email": body["email"],
        "display_name": body.get("display_name") or body["email"],
        "tenant_id": body["tenant_id"],
        "role": body.get("role") or "client_user",
        "started": now,
        "last_seen": now,
    }


st.set_page_config(
    page_title="Fivecent | Client portal",
    page_icon=":material/analytics:",
    layout="wide",
)

st.title("Fivecent")
st.caption("CLIENT EXECUTIVE DASHBOARD")

portal_session = st.session_state.get("portal_session")
if not _session_ok(portal_session):
    tab_login, tab_invite = st.tabs(["Sign in", "Set password from invite"])
    if notice := st.session_state.pop("portal_notice", ""):
        st.error(notice)

    with tab_login:
        st.subheader("Agency sign in")
        st.caption(
            "Use the email your company operator invited. "
            "Company staff use the Admin page."
        )
        with st.form("portal_login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Sign in", use_container_width=True)
        if submit:
            try:
                with httpx.Client(base_url=_api_base(), timeout=15.0) as client:
                    response = client.post(
                        "/internal/portal/login",
                        json={"email": email.strip(), "password": password},
                    )
            except httpx.RequestError:
                st.error("Cannot reach the API.")
                st.stop()
            if response.status_code != 200:
                _error(response)
            else:
                _store_session(response.json())
                st.rerun()

    with tab_invite:
        st.subheader("Activate invite")
        st.caption(
            "Paste the one-time invite token from your operator, then choose a "
            "password (12+ characters)."
        )
        with st.form("portal_set_password"):
            invite_token = st.text_input("Invite token")
            new_password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            activate = st.form_submit_button("Set password and sign in", use_container_width=True)
        if activate:
            if new_password != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    with httpx.Client(base_url=_api_base(), timeout=15.0) as client:
                        response = client.post(
                            "/internal/portal/set-password",
                            json={
                                "invite_token": invite_token.strip(),
                                "password": new_password,
                            },
                        )
                except httpx.RequestError:
                    st.error("Cannot reach the API.")
                    st.stop()
                if response.status_code != 200:
                    _error(response)
                else:
                    _store_session(response.json())
                    st.success("Password saved — welcome.")
                    st.rerun()
    st.stop()

assert isinstance(portal_session, dict)
st.session_state["portal_session"]["last_seen"] = time.monotonic()

overview = _portal_request("GET", "/internal/portal/overview")
if overview is None:
    st.stop()
if not overview.is_success:
    _error(overview)
    st.stop()

agency = overview.json()
caps = ((agency.get("user") or {}).get("capabilities")) or {}
can_contact = bool(caps.get("view_guest_contact"))
can_mutate = bool(caps.get("mutate_fleet"))
role = (agency.get("user") or {}).get("role") or portal_session.get("role") or "client_user"

with st.sidebar:
    st.write(f"Signed in as **{portal_session.get('display_name')}**")
    st.caption(portal_session.get("email", ""))
    st.caption(f"Role: `{role}`")
    st.button("Sign out", on_click=_sign_out, use_container_width=True)
    st.divider()
    if can_mutate:
        st.caption("Agency operations: fleet maintenance enabled.")
    else:
        st.caption("Read-only analytics. Ask an agency_ops user for maintenance actions.")

st.subheader(agency.get("agency_name") or "Your agency")
st.caption(
    f"{agency.get('home_port') or '—'} · "
    f"{'Active' if agency.get('is_active') else 'Inactive'}"
)

tab_funnel, tab_holds, tab_fleet = st.tabs(
    ["Executive funnel", "Active option holds", "Fleet availability"]
)

with tab_funnel:
    today = date.today()
    default_from = today - timedelta(days=30)
    col_a, col_b = st.columns(2)
    with col_a:
        date_from = st.date_input("From", value=default_from)
    with col_b:
        date_to = st.date_input("To", value=today)

    with st.spinner("Loading funnel metrics…"):
        analytics = _portal_request(
            "GET",
            "/internal/portal/analytics",
            params={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )
    if analytics is None:
        st.stop()
    if not analytics.is_success:
        _error(analytics)
    else:
        body = analytics.json()
        metrics = body.get("metrics") or {}
        if not any(int(metrics.get(key) or 0) for key in metrics):
            st.info("No activity in this date range yet.")
        else:
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Inquiries", metrics.get("inquiries", 0))
            m2.metric("Conversations", metrics.get("active_conversations", 0))
            m3.metric("Leads", metrics.get("leads", 0))
            m4.metric("Quotes", metrics.get("quotes", 0))
            m5.metric("Broker handoffs", metrics.get("broker_handoffs", 0))
        st.caption(body.get("disclaimer") or "")

with tab_holds:
    with st.spinner("Loading active holds…"):
        holds_resp = _portal_request("GET", "/internal/portal/holds")
    if holds_resp is None:
        st.stop()
    if not holds_resp.is_success:
        _error(holds_resp)
    else:
        holds_body = holds_resp.json()
        items = holds_body.get("items") or []
        if not items:
            st.info("No active option holds right now.")
        else:
            rows = []
            for item in items:
                row = {
                    "Boat": item.get("boat_name") or item.get("boat_id"),
                    "Start": item.get("start_date"),
                    "Days": item.get("days"),
                    "Expires": item.get("expires_at"),
                    "Status": item.get("status"),
                }
                contact = item.get("contact")
                if can_contact and isinstance(contact, dict):
                    row["Guest"] = contact.get("customer_name")
                    row["Email"] = contact.get("email")
                rows.append(row)
            st.dataframe(rows, hide_index=True, use_container_width=True)
            if not can_contact:
                st.caption("Guest contact details are hidden for your role.")
        st.caption(holds_body.get("disclaimer") or "")

with tab_fleet:
    with st.spinner("Loading fleet…"):
        fleet_resp = _portal_request("GET", "/internal/portal/fleet")
    if fleet_resp is None:
        st.stop()
    if not fleet_resp.is_success:
        _error(fleet_resp)
    else:
        fleet_body = fleet_resp.json()
        boats = fleet_body.get("items") or []
        if not boats:
            st.info("No fleet inventory for this agency yet.")
        else:
            st.dataframe(
                [
                    {
                        "Boat": b.get("name"),
                        "Status": b.get("availability_status"),
                        "Length (m)": b.get("length_m"),
                        "Capacity": b.get("capacity"),
                        "Active": b.get("active"),
                    }
                    for b in boats
                ],
                hide_index=True,
                use_container_width=True,
            )
            if can_mutate:
                st.markdown("**Maintenance toggle**")
                st.caption(
                    "Setting a vessel offline (`active=false`) excludes it from quoting."
                )
                options = {
                    f"{b.get('name')} ({b.get('boat_id')}) — "
                    f"{'online' if b.get('active') else 'maintenance'}": b
                    for b in boats
                }
                with st.form("fleet_maintenance"):
                    chosen = st.selectbox("Vessel", options=list(options.keys()))
                    set_online = st.radio(
                        "Availability",
                        options=["online", "maintenance"],
                        horizontal=True,
                    )
                    submit_maint = st.form_submit_button("Update availability")
                if submit_maint and chosen:
                    boat = options[chosen]
                    resp = _portal_request(
                        "PATCH",
                        f"/internal/portal/fleet/{boat['boat_id']}",
                        json={"active": set_online == "online"},
                    )
                    if resp is None:
                        st.stop()
                    if resp.is_success:
                        st.success("Fleet availability updated.")
                        st.rerun()
                    else:
                        _error(resp)
            else:
                st.caption("Fleet maintenance requires the agency_ops role.")
        st.caption(fleet_body.get("disclaimer") or "")
