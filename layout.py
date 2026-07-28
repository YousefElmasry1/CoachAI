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
    get_database_status,
    get_system_info,
    load_today_plan,
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
        "user_display_name": "Yousef",
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
        plan = load_today_plan()
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
    close_out_stale_tasks()
    st.session_state["_stale_tasks_closed_date"] = today_str


def page_setup(title: str, icon: str) -> None:
    """
    Standard boilerplate every page needs, in the right order:
    set_page_config → session defaults → theme → sidebar.

    Must be the first Streamlit-related call on the page.
    """
    st.set_page_config(
        page_title=f"CoachAI — {title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_session_state()
    maybe_close_out_stale_tasks()
    apply_theme()
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