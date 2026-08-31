"""
CoachAI – Settings Page
==========================

App-level preferences (theme, analytics window, debug mode), live
backend/system status, and lightweight category/badge management via
existing Database methods only.
"""
from __future__ import annotations

import streamlit as st

from config import DEFAULT_ANALYTICS_WINDOW, TIMEZONE_CHOICES
from layout import page_setup, page_title
from services import (
    get_current_user_id,
    get_database_status,
    get_system_info,
    load_categories,
    create_category,
    load_all_badges,
    clear_all_caches,
    is_google_calendar_connected,
    get_google_auth_url,
    connect_google_calendar,
    disconnect_google_calendar,
    fetch_google_calendars,
    save_selected_calendars,
    get_selected_calendars,
    sync_google_calendar,
    get_last_sync_time,
    load_user,
    set_user_timezone,
)


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Settings", icon="⚙️")
page_title("⚙️", "Settings", "Preferences, backend status, and workspace management.")

user_id = get_current_user_id()

tabs = st.tabs(["🎨 Appearance", "⚡ Data & Performance", "🩺 System Status", "🏷️ Categories", "🏆 Badges", "📅 Google Calendar"])


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

    st.markdown("#### Timezone")
    st.caption(
        "Used to show the right time of day in your greeting and in "
        "analytics like the Focus Pattern Matrix. Temporary manual "
        "picker — the mobile app will set this automatically."
    )
    current_timezone = load_user(user_id=user_id).get("timezone", "UTC")
    tz_labels = [label for label, _ in TIMEZONE_CHOICES]
    tz_values = [value for _, value in TIMEZONE_CHOICES]
    try:
        current_index = tz_values.index(current_timezone)
    except ValueError:
        # User's stored timezone isn't in the curated shortlist (e.g. an
        # IANA name set directly in the DB) — show it as a trailing
        # extra option instead of silently overwriting it on save.
        tz_labels = tz_labels + [current_timezone]
        tz_values = tz_values + [current_timezone]
        current_index = len(tz_values) - 1

    chosen_label = st.selectbox(
        "Your timezone",
        options=tz_labels,
        index=current_index,
        key="timezone_settings_select",
    )
    chosen_timezone = tz_values[tz_labels.index(chosen_label)]

    if chosen_timezone != current_timezone:
        if st.button("💾 Save Timezone", type="primary"):
            set_user_timezone(user_id, chosen_timezone)
            st.toast(f"Timezone set to {chosen_label}.", icon="🌍")
            st.rerun()


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

    def _sync_debug_mode() -> None:
        st.session_state.debug_mode = st.session_state["_debug_mode_toggle"]

    st.toggle(
        "🐞 Show raw errors and tracebacks",
        value=st.session_state.get("debug_mode", False),
        key="_debug_mode_toggle",
        on_change=_sync_debug_mode,
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


# ═════════════════════════════════════════════════════════════
# GOOGLE CALENDAR
# ═════════════════════════════════════════════════════════════
with tabs[5]:
    # Handle OAuth callback (code in query params)
    query_params = st.query_params
    auth_code = query_params.get("code")
    if auth_code and not is_google_calendar_connected(user_id):
        with st.spinner("Connecting to Google Calendar..."):
            if connect_google_calendar(auth_code, user_id):
                st.query_params.clear()
                st.toast("Google Calendar connected!", icon="✅")
                st.rerun()
            else:
                st.error(
                    "Failed to connect. Please try again."
                )
                st.query_params.clear()

    connected = is_google_calendar_connected(user_id)

    if not connected:
        # ── State A: Not Connected ──
        st.markdown("#### 📅 Google Calendar")
        st.markdown(
            "Connect your Google Calendar to see your events as "
            "fixed time blocks in your daily plan."
        )
        st.caption(
            "Only events from calendars you select will be synced. "
            "All-day events are ignored."
        )
        st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
        try:
            auth_url = get_google_auth_url()
            st.link_button(
                "🔗 Connect Google Calendar",
                url=auth_url,
                type="primary",
                use_container_width=True,
            )
        except Exception as e:
            st.error(
                "Could not generate Google sign-in link. "
                "Make sure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                "are set in your .env file."
            )
            if st.session_state.get("debug_mode"):
                st.exception(e)
    else:
        selected = get_selected_calendars(user_id)
        picking = st.session_state.get("_gcal_picking", not selected)

        if picking:
            # ── State B: Calendar Picker ──
            st.markdown("#### 📅 Select Calendars")
            st.caption(
                "Choose which Google Calendars CoachAI should use for planning. "
                "Only selected calendars will be synced and treated as fixed time blocks."
            )
            st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)

            available = fetch_google_calendars(user_id)
            if not available:
                st.warning(
                    "Could not load your calendars from Google. "
                    "Try again later.",
                    icon="⚠️",
                )
            else:
                # Pre-select: previously selected or just primary on first run
                prev_ids = {c["calendar_id"] for c in selected}
                selections = {}
                for cal in available:
                    default = (
                        cal["calendar_id"] in prev_ids
                        if prev_ids
                        else cal.get("is_primary", False)
                    )
                    label = cal["name"]
                    if cal.get("is_primary"):
                        label += " (Primary)"
                    selections[cal["calendar_id"]] = st.checkbox(
                        label,
                        value=default,
                        key=f"_gcal_sel_{cal['calendar_id']}",
                    )

                st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
                sc1, sc2 = st.columns(2)
                with sc1:
                    if st.button(
                        "💾 Save Selection",
                        type="primary",
                        use_container_width=True,
                    ):
                        chosen = [
                            {
                                "calendar_id": cal["calendar_id"],
                                "calendar_name": cal["name"],
                                "color": cal.get("color", "#4285F4"),
                                "is_primary": cal.get("is_primary", False),
                            }
                            for cal in available
                            if selections.get(cal["calendar_id"])
                        ]
                        if not chosen:
                            st.error("Please select at least one calendar.")
                        else:
                            save_selected_calendars(user_id, chosen)
                            st.session_state["_gcal_picking"] = False
                            st.toast(
                                f"{len(chosen)} calendar(s) selected!",
                                icon="✅",
                            )
                            st.rerun()
                with sc2:
                    if selected and st.button(
                        "✖️ Cancel",
                        use_container_width=True,
                    ):
                        st.session_state["_gcal_picking"] = False
                        st.rerun()
        else:
            # ── State C: Connected with Calendars Selected ──
            st.markdown("#### ✅ Google Calendar Connected")

            last_sync = get_last_sync_time(user_id)
            if last_sync:
                from datetime import datetime, timedelta
                try:
                    # last_sync is stored as SQLite CURRENT_TIMESTAMP, which
                    # is always UTC — convert to local time before comparing
                    # against datetime.now() or displaying it, or both the
                    # "X minutes ago" math and the clock label come out
                    # hours off (matches the local UTC+3 offset).
                    sync_dt_utc = datetime.fromisoformat(last_sync)
                    sync_dt = sync_dt_utc + timedelta(hours=3)
                    diff = datetime.now() - sync_dt
                    mins_ago = int(diff.total_seconds() / 60)
                    if mins_ago < 1:
                        sync_label = "just now"
                    elif mins_ago < 60:
                        sync_label = f"{mins_ago} minute{'s' if mins_ago != 1 else ''} ago"
                    else:
                        sync_label = sync_dt.strftime("%I:%M %p")
                    st.caption(f"Last synced: {sync_label}")
                except (ValueError, TypeError):
                    st.caption(f"Last synced: {last_sync}")
            else:
                st.caption("Not yet synced.")

            st.markdown("**Selected calendars:**")
            for cal in selected:
                color = cal.get("color", "#4285F4")
                name = cal.get("calendar_name", cal["calendar_id"])
                st.markdown(
                    f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:4px;'>"
                    f"<span style='width:12px; height:12px; border-radius:50%; "
                    f"background:{color}; display:inline-block;'></span>"
                    f"<span>✅ {name}</span></div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)
            gc1, gc2, gc3 = st.columns(3)
            with gc1:
                if st.button(
                    "✏️ Change Calendars",
                    use_container_width=True,
                ):
                    st.session_state["_gcal_picking"] = True
                    st.rerun()
            with gc2:
                if st.button(
                    "🔄 Sync Now",
                    use_container_width=True,
                ):
                    with st.spinner("Syncing..."):
                        result = sync_google_calendar(user_id)
                    if result.get("error"):
                        st.warning(
                            f"Sync issue: {result['error']}",
                            icon="⚠️",
                        )
                    else:
                        st.toast(
                            f"Synced {result.get('synced_count', 0)} event(s) "
                            f"from {result.get('calendars_synced', 0)} calendar(s)!",
                            icon="🔄",
                        )
                        st.rerun()
            with gc3:
                if st.button(
                    "❌ Disconnect",
                    type="secondary",
                    use_container_width=True,
                ):
                    st.session_state["_gcal_confirm_disconnect"] = True
                    st.rerun()

            if st.session_state.get("_gcal_confirm_disconnect"):
                st.warning(
                    "This will remove your Google Calendar connection, "
                    "all selected calendars, and all synced events from CoachAI.",
                    icon="⚠️",
                )
                cd1, cd2 = st.columns(2)
                with cd1:
                    if st.button(
                        "Yes, Disconnect",
                        type="primary",
                        use_container_width=True,
                    ):
                        disconnect_google_calendar(user_id)
                        st.session_state.pop("_gcal_confirm_disconnect", None)
                        st.session_state.pop("_gcal_picking", None)
                        st.toast("Google Calendar disconnected.", icon="✅")
                        st.rerun()
                with cd2:
                    if st.button(
                        "Cancel",
                        use_container_width=True,
                    ):
                        st.session_state.pop("_gcal_confirm_disconnect", None)
                        st.rerun()