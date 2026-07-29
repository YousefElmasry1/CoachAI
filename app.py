"""
CoachAI – Main Application & Dashboard
========================================

The primary entry point for the Streamlit application.
This file renders the 🏠 Dashboard (home page) and configures
multipage navigation.
"""

from __future__ import annotations

import streamlit as st

from layout import page_setup

# ─────────────────────────────────────────────────────────────
# Page Setup (MUST happen before any other Streamlit call) —
# handles set_page_config, session-state defaults, theme, sidebar.
# ─────────────────────────────────────────────────────────────

page_setup(title="Dashboard", icon="🏠")

# ─────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────

from config import (
    CHART_COLORS,
    PRIORITY_COLORS,
)
from helpers import (
    badge_html,
    confidence_badge_html,
    format_date_long,
    format_duration,
    format_percentage,
    format_score,
    format_time_12h,
    get_burnout_color,
    get_burnout_label,
    get_daily_quote,
    get_greeting,
    get_priority_icon,
    get_status_icon,
    get_today_display,
    get_trend_color,
    get_trend_icon,
    kpi_card_html,
    progress_bar_html,
    status_badge_html,
)
from services import (
    get_current_user_id,
    get_database_status,
    get_system_info,
    load_analytics_profile,
    load_today_plan,
    load_today_tasks,
    load_user,
)
from charts import create_gauge


# ─────────────────────────────────────────────────────────────
# Dashboard Content
# ─────────────────────────────────────────────────────────────

def render_dashboard() -> None:
    """Render the main executive dashboard."""

    # ── Load Data ────────────────────────────────────────
    user_id = get_current_user_id()
    with st.spinner(""):
        user = load_user(user_id=user_id)
        plan = load_today_plan(user_id=user_id)
        tasks = load_today_tasks(user_id=user_id)
        profile = load_analytics_profile(
            user_id=user_id,
            window_days=st.session_state.analytics_window,
        )

    user_name = st.session_state.get("user_display_name") or user.get("display_name", "there")

    # ── Hero Header ──────────────────────────────────────
    st.markdown(
        f"""
        <div class="hero-gradient animate-in">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:1rem;">
                <div>
                    <p style="font-size:0.85rem; opacity:0.8; margin-bottom:0.3rem;">
                        📅 {get_today_display()}
                    </p>
                    <h1>{get_greeting()}, {user_name}</h1>
                    <p>Here's your productivity overview for today.</p>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:2.5rem; font-weight:800; line-height:1;">
                        {format_score(profile.productivity.score)}
                    </div>
                    <div style="font-size:0.82rem; opacity:0.8;">Productivity Score</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Smart Banners ────────────────────────────────────
    _render_smart_banners(profile, tasks)

    # ── KPI Row 1: Core Metrics ──────────────────────────
    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            kpi_card_html(
                label="Completion Rate",
                value=format_percentage(profile.completion_rate),
                subtitle=f"{profile.total_completed}/{profile.total_tasks} tasks",
                accent="green",
                icon="✅",
            ),
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            kpi_card_html(
                label="Failure Rate",
                value=format_percentage(profile.failure_rate),
                subtitle=f"{profile.total_failed} failed",
                accent="red",
                icon="❌",
            ),
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            kpi_card_html(
                label="Planning Accuracy",
                value=format_percentage(profile.planning.planning_accuracy),
                subtitle=f"Bias: {profile.planning.bias_direction}",
                accent="blue",
                icon="🎯",
            ),
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            kpi_card_html(
                label="Consistency",
                value=format_percentage(profile.consistency.consistency_score),
                subtitle=f"{profile.consistency.active_days} active days",
                accent="purple",
                icon="📊",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)

    # ── KPI Row 2: Streaks & Risk ────────────────────────
    cols2 = st.columns(4)
    with cols2[0]:
        st.markdown(
            kpi_card_html(
                label="Current Streak",
                value=f"{profile.consistency.current_streak}",
                subtitle="consecutive days",
                accent="orange",
                icon="🔥",
            ),
            unsafe_allow_html=True,
        )
    with cols2[1]:
        st.markdown(
            kpi_card_html(
                label="Longest Streak",
                value=f"{profile.consistency.longest_streak}",
                subtitle="personal best",
                accent="teal",
                icon="🏆",
            ),
            unsafe_allow_html=True,
        )
    with cols2[2]:
        burnout_color_name = "green"
        if profile.burnout.burnout_risk > 55:
            burnout_color_name = "red"
        elif profile.burnout.burnout_risk > 30:
            burnout_color_name = "orange"
        st.markdown(
            kpi_card_html(
                label="Burnout Risk",
                value=f"{profile.burnout.burnout_risk:.0f}%",
                subtitle=get_burnout_label(profile.burnout.burnout_risk),
                accent=burnout_color_name,
                icon="🛡️",
            ),
            unsafe_allow_html=True,
        )
    with cols2[3]:
        best_hour_text = f"{profile.best_hour.best_hour}:00" if profile.best_hour.best_hour is not None else "—"
        st.markdown(
            kpi_card_html(
                label="Best Hour",
                value=best_hour_text,
                subtitle=(
                    f"{format_percentage(profile.best_hour.completion_rate_at_best)} completion"
                    if profile.best_hour.best_hour is not None
                    else "Insufficient data"
                ),
                accent="pink",
                icon="⏰",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)

    # ── Main Content: Two Columns ────────────────────────
    left_col, right_col = st.columns([3, 2])

    with left_col:
        # Productivity Score Gauge
        st.markdown("#### Productivity Overview")
        gauge_fig = create_gauge(
            value=profile.productivity.score,
            title="Overall Score",
            max_val=100,
            height=220,
        )
        st.plotly_chart(gauge_fig, use_container_width=True, key="dash_gauge")

        # Trend & Confidence Row
        trend_cols = st.columns(3)
        with trend_cols[0]:
            trend_dir = profile.trend.trend_direction
            st.metric(
                label="Trend",
                value=f"{get_trend_icon(trend_dir)} {trend_dir.title()}",
                delta=f"Score: {profile.trend.trend_score:.2f}",
            )
        with trend_cols[1]:
            st.metric(
                label="Confidence",
                value=profile.overall_confidence.level.title(),
                delta=f"Sample: {profile.sample_size}",
            )
        with trend_cols[2]:
            st.metric(
                label="Analysis Window",
                value=f"{profile.window_days} days",
                delta=f"{profile.consistency.total_observation_days} observed",
            )

        # Today's Schedule Mini
        st.markdown("#### Today's Schedule")
        if tasks:
            for task in tasks[:6]:  # Show first 6 tasks
                priority = task.get("priority", 3)
                status = task.get("status", "pending")
                p_color = PRIORITY_COLORS.get(priority, "#6b7280")
                start = format_time_12h(task.get("scheduled_start"))
                end = format_time_12h(task.get("scheduled_end"))
                duration = format_duration(task.get("estimated_minutes"))
                s_icon = get_status_icon(status)

                st.markdown(
                    f"""
                    <div class="task-card">
                        <div class="priority-dot" style="background:{p_color};"></div>
                        <div class="task-info">
                            <div class="task-title">{s_icon} {task.get('title', 'Untitled')}</div>
                            <div class="task-meta">
                                <span>{get_priority_icon(priority)} {priority}</span>
                                <span>⏱️ {duration}</span>
                            </div>
                        </div>
                        <div class="task-time">{start} – {end}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            if len(tasks) > 6:
                st.caption(f"+ {len(tasks) - 6} more tasks — see Schedule page")
        else:
            st.info("📋 No plan for today. Create a plan to get started!")

    with right_col:
        # Quick Insights
        st.markdown("#### Quick Insights")

        insights = profile.insights.insights
        if insights:
            for insight in insights[:3]:
                st.markdown(
                    f"""
                    <div class="insight-card">
                        <p>💡 {insight.observation}</p>
                        <p class="evidence">{insight.evidence}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Insights will appear as you complete more tasks.")

        # Quick Patterns
        patterns = profile.patterns.patterns
        if patterns:
            st.markdown("#### Detected Patterns")
            for pattern in patterns[:3]:
                conf = pattern.confidence
                st.markdown(
                    f"""
                    <div class="coach-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <strong style="color:var(--text-primary); font-size:0.88rem;">
                                🔍 {pattern.pattern_name}
                            </strong>
                            {confidence_badge_html(conf)}
                        </div>
                        <p style="color:var(--text-secondary); font-size:0.83rem; margin:0;">
                            {pattern.observation}
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Motivational Quote
        st.markdown("#### Daily Motivation")
        quote = get_daily_quote()
        st.markdown(
            f"""
            <div class="quote-card">
                <div class="quote-text">"{quote['text']}"</div>
                <div class="quote-author">— {quote['author']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # System Status Footer
        st.markdown("#### System Status")
        sys_info = get_system_info()
        db_st = get_database_status()

        status_items = [
            ("Database", "🟢 Online" if db_st["connected"] else "🔴 Offline"),
            ("AI Model", sys_info.get("ai_model", "—")),
            ("Analytics", f"v{sys_info.get('analytics_version', '?')}"),
            ("Profile", f"v{sys_info.get('profile_version', '?')}"),
            ("Plan Today", "✅ Active" if plan else "❌ No Plan"),
        ]

        for label, value in status_items:
            st.markdown(
                f"""
                <div style="display:flex; justify-content:space-between; padding:6px 0;
                            border-bottom: 1px solid var(--border); font-size:0.82rem;">
                    <span style="color:var(--text-secondary);">{label}</span>
                    <span style="color:var(--text-primary); font-weight:500;">{value}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_smart_banners(profile, tasks: list) -> None:
    """Render contextual smart banners based on current state."""

    # Burnout Warning
    if profile.burnout.burnout_risk > 60:
        st.warning(
            f"🛡️ **Burnout Risk is {get_burnout_label(profile.burnout.burnout_risk)}** "
            f"({profile.burnout.burnout_risk:.0f}/100). "
            "Consider reducing your schedule density and taking more breaks.",
            icon="⚠️",
        )

    # Excellent Productivity
    if profile.productivity.score >= 80:
        st.success(
            f"🎉 **Outstanding productivity!** Your score of {profile.productivity.score:.1f}/100 "
            "is excellent. Keep up the great work!",
            icon="🌟",
        )

    # Streak Celebration
    if profile.consistency.current_streak >= 5:
        st.success(
            f"🔥 **{profile.consistency.current_streak}-day streak!** "
            "You're building a strong habit of consistency.",
            icon="🔥",
        )

    # Low Confidence Warning
    if profile.overall_confidence.level in ("insufficient", "low"):
        st.info(
            f"📊 **Analytics confidence is {profile.overall_confidence.level}**. "
            f"Only {profile.sample_size} tasks analyzed over {profile.overall_confidence.observation_days} days. "
            "Keep completing tasks to improve accuracy.",
            icon="ℹ️",
        )

    # No Tasks Today
    if not tasks:
        st.info(
            "📋 **No plan for today.** Create a daily plan to unlock scheduling, "
            "analytics, and AI coaching.",
            icon="💡",
        )


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────

render_dashboard()