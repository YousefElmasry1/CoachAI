"""
CoachAI – Settings Page
==========================

App-level preferences (theme, analytics window, debug mode), live
backend/system status, and lightweight category/badge management via
existing Database methods only.
"""
from __future__ import annotations

import streamlit as st

from config import DEFAULT_ANALYTICS_WINDOW
from layout import page_setup, page_title
from services import (
    get_database_status,
    get_system_info,
    load_categories,
    create_category,
    load_all_badges,
    clear_all_caches,
)


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Settings", icon="⚙️")
page_title("⚙️", "Settings", "Preferences, backend status, and workspace management.")

tabs = st.tabs(["🎨 Appearance", "⚡ Data & Performance", "🩺 System Status", "🏷️ Categories", "🏆 Badges"])


# ═════════════════════════════════════════════════════════════
# APPEARANCE
# ═════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown("#### Theme")
    new_dark_mode = st.toggle(
        "🌙 Dark Mode",
        value=st.session_state.get("dark_mode", True),
        key="dark_mode_settings_toggle",
        help="Applies instantly across every page.",
    )
    if new_dark_mode != st.session_state.get("dark_mode", True):
        st.session_state.dark_mode = new_dark_mode
        st.rerun()
    st.caption("Tip: this same toggle is always available in the sidebar.")


# ═════════════════════════════════════════════════════════════
# DATA & PERFORMANCE
# ═════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("#### Analytics Window")
    st.select_slider(
        "Default lookback window for Dashboard & Analytics",
        options=[7, 14, 30, 60, 90],
        value=st.session_state.get("analytics_window", DEFAULT_ANALYTICS_WINDOW),
        key="analytics_window",
        format_func=lambda d: f"{d} days",
    )

    st.markdown("#### Debug Mode")
    st.toggle(
        "🐞 Show raw errors and tracebacks",
        value=st.session_state.get("debug_mode", False),
        key="debug_mode",
        help="Useful when presenting or debugging — shows full exception details on the AI Coach page.",
    )

    st.markdown("#### Cache")
    st.caption("Analytics profiles are cached for 5 minutes for performance. Force a refresh after changing task data.")
    if st.button("🔄 Reload Analytics Now", type="primary"):
        clear_all_caches()
        st.toast("Caches cleared — analytics will recompute on next view.", icon="✅")


# ═════════════════════════════════════════════════════════════
# SYSTEM STATUS
# ═════════════════════════════════════════════════════════════
with tabs[2]:
    db_status = get_database_status()
    sys_info = get_system_info()

    st.markdown("#### Backend Status")
    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("Database", "🟢 Online" if db_status["connected"] else "🔴 Offline")
    with s2:
        st.metric("AI Model", sys_info.get("ai_model", "—"))
    with s3:
        st.metric("Database Size", db_status.get("size_display", "—"))

    if not db_status["connected"]:
        st.error(f"Database error: {db_status.get('error', 'unknown')}")

    st.markdown("#### Versions")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("App Version", sys_info.get("app_version", "—"))
    v2.metric("Analytics", f"v{sys_info.get('analytics_version', '?')}")
    v3.metric("Profile Schema", f"v{sys_info.get('profile_version', '?')}")
    v4.metric("Statistics", f"v{sys_info.get('statistics_version', '?')}")

    st.markdown("#### Environment")
    e1, e2, e3 = st.columns(3)
    e1.metric("Python", sys_info.get("python_version", "—"))
    e2.metric("Streamlit", sys_info.get("streamlit_version", "—"))
    e3.metric("SQLite", sys_info.get("sqlite_version", "—"))

    with st.expander("📄 Database file path"):
        st.code(db_status.get("path", "—"))


# ═════════════════════════════════════════════════════════════
# CATEGORIES
# ═════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("#### Your Categories")
    categories = load_categories()
    if categories:
        cols = st.columns(3)
        for i, c in enumerate(categories):
            with cols[i % 3]:
                st.markdown(
                    f"""
                    <div class="kpi-card" style="padding:0.9rem 1.1rem;">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="width:12px; height:12px; border-radius:50%; background:{c['color']}; display:inline-block;"></span>
                            <strong style="color:var(--text-primary);">{c['name']}</strong>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.caption("No categories yet — add your first one below.")

    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
    with st.form("add_category_form", clear_on_submit=True):
        st.markdown("##### ➕ Add a Category")
        c1, c2 = st.columns([2, 1])
        with c1:
            name = st.text_input("Name")
        with c2:
            color = st.color_picker("Color", value="#6C63FF")
        if st.form_submit_button("Add Category", type="primary"):
            if not name.strip():
                st.error("Please enter a category name.")
            else:
                cat_id = create_category(user_id=1, name=name.strip(), color=color)
                if cat_id:
                    st.toast(f"Category '{name}' added!", icon="🏷️")
                    st.rerun()
                else:
                    st.error("Couldn't add category — the name may already exist.")


# ═════════════════════════════════════════════════════════════
# BADGES
# ═════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown("#### Achievement Badges")
    badges = load_all_badges()
    if not badges:
        st.info(
            "No badges have been seeded yet. Badges are earned automatically based on "
            "streaks, task counts, or completion rates once definitions exist in the database.",
            icon="🏆",
        )
    else:
        cols = st.columns(3)
        for i, b in enumerate(badges):
            with cols[i % 3]:
                st.markdown(
                    f"""
                    <div class="kpi-card" style="text-align:center; padding:1.2rem;">
                        <div style="font-size:2rem;">{b.get('icon') or '🏆'}</div>
                        <div style="font-weight:700; color:var(--text-primary); margin-top:4px;">{b['name']}</div>
                        <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:2px;">{b['description']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )