"""
CoachAI – Shared Page Layout
==============================

Common session-state defaults, sidebar chrome, and theme injection shared
by every page in the multipage app. Keeps app.py and every page in
pages/ free of duplicated boilerplate.

Never imports backend logic directly — only via services.py.
"""

from __future__ import annotations

import streamlit as st

from config import APP_NAME, APP_TAGLINE, APP_VERSION, DEFAULT_ANALYTICS_WINDOW
from styles import inject_styles
from services import (
    close_out_stale_tasks,
    create_guest_user,
    get_current_user_id,
    get_database_status,
    get_system_info,
    load_today_plan,
    log_in,
    sign_up,
)


# ─────────────────────────────────────────────────────────────
# Session State
# ─────────────────────────────────────────────────────────────

def init_session_state() -> None:
    """Ensure every page-independent session key exists before use."""
    defaults = {
        "dark_mode": True,
        "analytics_window": DEFAULT_ANALYTICS_WINDOW,
        "debug_mode": False,
        "last_recommendation": None,
        "last_recommendation_plan_id": None,
        "user_display_name": None,
        "user_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_theme() -> None:
    """Inject the CSS theme for the current dark/light mode setting."""
    inject_styles(dark_mode=st.session_state.get("dark_mode", True))


# ─────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    """Render the shared sidebar: brand header + live system status."""
    with st.sidebar:
        st.markdown(
            f"""
            <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
                <div style="font-size:2rem;">🧠</div>
                <div style="font-size:1.2rem; font-weight:800; color:var(--text-primary);
                            letter-spacing:-0.02em; margin-top:4px;">
                    {APP_NAME}
                </div>
                <div style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;">
                    {APP_TAGLINE}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")

        db_status = get_database_status()
        dot_color = "#10b981" if db_status["connected"] else "#ef4444"
        plan = load_today_plan(user_id=get_current_user_id())
        sys_info = get_system_info()

        st.markdown(
            f"""
            <div style="font-size:0.78rem; color:var(--text-muted); padding: 0 0.5rem; line-height:1.9;">
                <div><span class="status-dot" style="background:{dot_color};"></span>
                    Database {'Online' if db_status["connected"] else 'Offline'}</div>
                <div><span class="status-dot" style="background:{'#10b981' if plan else '#f59e0b'};"></span>
                    {'Plan Active Today' if plan else 'No Plan Today'}</div>
                <div style="margin-top:6px; font-size:0.7rem;">
                    v{APP_VERSION} · AI: {sys_info.get('ai_model', '—')}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")

        st.toggle(
            "🌙 Dark Mode",
            value=st.session_state.get("dark_mode", True),
            key="dark_mode",
            help="Switch between dark and light themes.",
        )

        st.caption("Use the navigation above to switch pages.")


# ─────────────────────────────────────────────────────────────
# Name Gate (creates a real, isolated guest account per session)
# ─────────────────────────────────────────────────────────────

def ensure_authenticated() -> None:
    """
    Gate every page behind a small entry screen, once per browser
    session, then stop rendering the rest of the current page until
    the person has picked one of three ways in:

      - Try as Guest: instant, isolated, throwaway account (today's
        create_guest_user behavior).
      - Log In: real email + password, returns to an existing
        account and its data.
      - Sign Up: real email + password, creates an account that can
        be logged back into later from any device/session.

    Either way, every visitor ends up with their own user_id, so
    plans/tasks/categories/analytics never mix between people.
    """
    if st.session_state.get("user_id"):
        return

    st.markdown(
        """
        <div style="max-width:440px; margin: 3rem auto 0 auto; text-align:center;">
            <div style="font-size:2.6rem;">🧠</div>
            <h2 style="margin-bottom:0.2rem; color:var(--text-primary);">Welcome to CoachAI</h2>
            <p style="color:var(--text-secondary); margin-bottom:1.2rem;">
                Try it instantly, or sign up to keep your data for next time.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, center_col, _ = st.columns([1, 2, 1])
    with center_col:
        tab_guest, tab_login, tab_signup = st.tabs(["⚡ Try as Guest", "🔑 Log In", "✨ Sign Up"])

        with tab_guest:
            st.caption("No account needed — instant, private workspace just for this session.")
            with st.form("guest_gate_form"):
                name = st.text_input("Your name", placeholder="e.g. Yousef")
                submitted = st.form_submit_button(
                    "Continue as Guest →", type="primary", use_container_width=True
                )
            if submitted:
                cleaned = name.strip()
                if not cleaned:
                    st.error("Please enter a name to continue.")
                else:
                    new_user_id = create_guest_user(cleaned)
                    if new_user_id is None:
                        st.error("Something went wrong starting your session. Please try again.")
                    else:
                        st.session_state.user_display_name = cleaned
                        st.session_state.user_id = new_user_id
                        st.rerun()
            st.caption("⚠️ Guest data can't be recovered later — sign up if you want to keep it.")

        with tab_login:
            with st.form("login_gate_form"):
                email = st.text_input("Email", key="_login_email")
                password = st.text_input("Password", type="password", key="_login_password")
                submitted = st.form_submit_button(
                    "Log In →", type="primary", use_container_width=True
                )
            if submitted:
                user_id, result = log_in(email, password)
                if user_id is None:
                    st.error(result)
                else:
                    st.session_state.user_display_name = result
                    st.session_state.user_id = user_id
                    st.rerun()

        with tab_signup:
            with st.form("signup_gate_form"):
                su_name = st.text_input("Your name", key="_signup_name")
                su_email = st.text_input("Email", key="_signup_email")
                su_password = st.text_input(
                    "Password", type="password", key="_signup_password",
                    help="At least 6 characters.",
                )
                submitted = st.form_submit_button(
                    "Create Account →", type="primary", use_container_width=True
                )
            if submitted:
                new_user_id, error = sign_up(su_email, su_password, su_name)
                if error:
                    st.error(error)
                else:
                    st.session_state.user_display_name = su_name.strip()
                    st.session_state.user_id = new_user_id
                    st.rerun()

    st.stop()


# ─────────────────────────────────────────────────────────────
# Reusable Page Chrome
# ─────────────────────────────────────────────────────────────

def maybe_close_out_stale_tasks() -> None:
    """
    Once per Streamlit session (and at most once per calendar day),
    auto-fail any task left pending/in_progress from a past day so it
    never sits unresolved forever. Cheap to skip on every rerun via a
    session_state guard instead of hitting the database every time.
    """
    from datetime import date

    today_str = date.today().isoformat()
    if st.session_state.get("_stale_tasks_closed_date") == today_str:
        return
    close_out_stale_tasks(user_id=get_current_user_id())
    st.session_state["_stale_tasks_closed_date"] = today_str


def page_setup(title: str, icon: str) -> None:
    """
    Standard boilerplate every page needs, in the right order:
    set_page_config → session defaults → theme → name gate → stale
    task cleanup → sidebar.

    Must be the first Streamlit-related call on the page.
    """
    st.set_page_config(
        page_title=f"CoachAI — {title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_session_state()
    apply_theme()
    ensure_authenticated()
    maybe_close_out_stale_tasks()
    render_sidebar()


def page_title(icon: str, title: str, subtitle: str = "") -> None:
    """Render a consistent gradient-free page header used on every page."""
    subtitle_html = (
        f'<p style="color:var(--text-secondary); margin-top:0.2rem;">{subtitle}</p>'
        if subtitle else ""
    )
    st.markdown(
        f"""
        <div class="animate-in" style="margin-bottom:1.2rem;">
            <h1 style="margin-bottom:0;">{icon} {title}</h1>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(icon: str, title: str, message: str, cta: str = "") -> None:
    """Friendly empty/onboarding state for pages with no data yet."""
    cta_html = f'<p style="margin-top:0.8rem; color:var(--accent); font-weight:600;">{cta}</p>' if cta else ""
    st.markdown(
        f"""
        <div class="kpi-card" style="text-align:center; padding:3rem 2rem;">
            <div style="font-size:3rem; margin-bottom:0.8rem;">{icon}</div>
            <div style="font-size:1.1rem; font-weight:700; color:var(--text-primary);">{title}</div>
            <p style="color:var(--text-secondary); max-width:420px; margin:0.6rem auto 0 auto;">{message}</p>
            {cta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )