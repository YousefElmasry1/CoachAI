"""
CoachAI – Analytics Page
===========================

Displays every metric computed by AnalyticsEngine / AnalyticsFormatter
(analytics.py, unmodified). Iterates over list-shaped results generically
so newly-added patterns, insights, correlations, heatmaps, or duration
buckets show up automatically without a frontend code change.
"""

from __future__ import annotations

import streamlit as st

from config import CHART_COLORS
from layout import page_setup, page_title, empty_state
from helpers import (
    confidence_badge_html,
    format_percentage,
    format_score,
    get_burnout_color,
    get_burnout_label,
    get_trend_color,
    get_trend_icon,
)
from services import get_current_user_id, load_analytics_profile, clear_all_caches
from charts import (
    create_gauge,
    create_radar,
    create_donut,
    create_bar,
    create_grouped_bar,
    create_area_chart,
    create_heatmap,
)


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Analytics", icon="📊")
page_title("📊", "Analytics", "Every metric your AI coach sees, in one place.")

top = st.columns([2, 1, 1])
with top[0]:
    window = st.select_slider(
        "Analysis window",
        options=[7, 14, 30, 60, 90],
        value=st.session_state.analytics_window,
        format_func=lambda d: f"{d} days",
    )
    st.session_state.analytics_window = window
with top[2]:
    st.markdown("<div style='height:1.8rem;'></div>", unsafe_allow_html=True)
    if st.button("🔄 Refresh", use_container_width=True):
        clear_all_caches()
        st.rerun()

profile = load_analytics_profile(user_id=get_current_user_id(), window_days=window)

if profile.sample_size == 0:
    empty_state(
        icon="📈",
        title="Not enough data yet",
        message="Complete or fail a few tasks and this page will fill up with real insights.",
    )
    st.stop()


# ─────────────────────────────────────────────────────────────
# Confidence Banner
# ─────────────────────────────────────────────────────────────

conf = profile.overall_confidence
st.markdown(
    f"""
    <div class="insight-card">
        <p>📐 Based on <strong>{profile.sample_size}</strong> tasks over
        <strong>{conf.observation_days}</strong> observed days
        ({conf.observation_days} of {profile.window_days} window days).
        {confidence_badge_html(conf.level)}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)

tabs = st.tabs([
    "🎯 Overview",
    "🏷️ Priority & Category",
    "📐 Planning & Trend",
    "🔍 Patterns & Insights",
    "🔥 Habits & Burnout",
    "🗓️ Heatmaps & Correlations",
    "⏳ Duration & Weekday/Weekend",
])


# ═════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═════════════════════════════════════════════════════════════
with tabs[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Productivity Score", format_score(profile.productivity.score))
    c2.metric("Completion Rate", format_percentage(profile.completion_rate))
    c3.metric("Failure Rate", format_percentage(profile.failure_rate))
    c4.metric("Avg Delay", f"{profile.avg_delay_minutes:.0f} min")

    gcol1, gcol2 = st.columns(2)
    with gcol1:
        st.markdown("#### Productivity Composite")
        st.plotly_chart(
            create_gauge(profile.productivity.score, title="Overall Score", height=240),
            use_container_width=True, key="an_gauge_prod",
        )
    with gcol2:
        st.markdown("#### Productivity Components")
        comps = profile.productivity.components
        if comps:
            fig = create_bar(
                x=list(comps.keys()),
                y=[round(v * 100, 1) for v in comps.values()],
                colors=CHART_COLORS[: len(comps)],
                y_title="Score (0–100)",
                height=280,
            )
            st.plotly_chart(fig, use_container_width=True, key="an_prod_components")
        else:
            st.caption("No component breakdown available.")

    st.markdown("#### Core Statistics")
    stat_cols = st.columns(5)
    stat_cols[0].metric("Total Tasks", profile.total_tasks)
    stat_cols[1].metric("Completed", profile.total_completed)
    stat_cols[2].metric("Failed", profile.total_failed)
    stat_cols[3].metric("Analytics Version", profile.analytics_version)
    stat_cols[4].metric("Profile Version", profile.profile_version)


# ═════════════════════════════════════════════════════════════
# TAB 2 — PRIORITY & CATEGORY
# ═════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("#### Priority Breakdown")
    pr = profile.priority
    pcol1, pcol2 = st.columns(2)
    pcol1.metric("Highest-Completed Priority", pr.highest_completed_priority or "—")
    pcol2.metric("Priority Trend", f"{get_trend_icon(pr.priority_trend)} {pr.priority_trend.title()}")

    active_priorities = [p for p in pr.per_priority if p.total > 0]
    if active_priorities:
        fig = create_grouped_bar(
            x=[f"P{p.priority}" for p in active_priorities],
            datasets=[
                {"name": "Completion Rate", "values": [round(p.completion_rate * 100, 1) for p in active_priorities], "color": "#10b981"},
                {"name": "Failure Rate", "values": [round(p.failure_rate * 100, 1) for p in active_priorities], "color": "#ef4444"},
            ],
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True, key="an_priority_bar")

        with st.expander("Full priority table"):
            st.dataframe(
                [
                    {
                        "Priority": p.priority, "Total": p.total, "Completed": p.completed,
                        "Failed": p.failed, "Completion %": round(p.completion_rate * 100, 1),
                        "Avg Delay": round(p.avg_delay, 1), "Risk Score": round(p.risk_score, 2),
                    }
                    for p in active_priorities
                ],
                use_container_width=True, hide_index=True,
            )
    else:
        st.caption("No priority data yet.")

    st.markdown("---")
    st.markdown("#### Category Breakdown")
    cat = profile.categories
    ccol1, ccol2, ccol3 = st.columns(3)
    ccol1.metric("Favorite Category", cat.favorite_category or "—")
    ccol2.metric("Strongest Category", cat.strongest_category or "—")
    ccol3.metric("Weakest Category", cat.weakest_category or "—")

    if cat.per_category:
        fig = create_bar(
            x=[c.category for c in cat.per_category],
            y=[round(c.completion_rate * 100, 1) for c in cat.per_category],
            colors=CHART_COLORS[: len(cat.per_category)],
            y_title="Completion Rate (%)",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True, key="an_category_bar")

        with st.expander("Full category table"):
            st.dataframe(
                [
                    {
                        "Category": c.category, "Total": c.total, "Completed": c.completed,
                        "Failed": c.failed, "Completion %": round(c.completion_rate * 100, 1),
                        "Trend": c.trend, "Habit Score": round(c.habit_score, 1),
                    }
                    for c in cat.per_category
                ],
                use_container_width=True, hide_index=True,
            )
    else:
        st.caption("No category data yet.")


# ═════════════════════════════════════════════════════════════
# TAB 3 — PLANNING & TREND
# ═════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("#### Planning Accuracy & Bias")
    pl = profile.planning
    pl_cols = st.columns(4)
    pl_cols[0].metric("Planning Accuracy", format_percentage(pl.planning_accuracy))
    pl_cols[1].metric("Bias Direction", pl.bias_direction.title())
    pl_cols[2].metric("Overestimation Rate", format_percentage(pl.overestimation_rate))
    pl_cols[3].metric("Underestimation Rate", format_percentage(pl.underestimation_rate))

    fig = create_bar(
        x=["Overestimation", "Underestimation", "Bias Severity"],
        y=[
            round(pl.overestimation_rate * 100, 1),
            round(pl.underestimation_rate * 100, 1),
            round(pl.bias_severity * 100, 1),
        ],
        colors=["#3b82f6", "#f59e0b", "#ef4444"],
        y_title="%",
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True, key="an_planning_bar")

    st.markdown("---")
    st.markdown("#### Trend Over Time")
    tr = profile.trend
    tcol1, tcol2 = st.columns(2)
    tcol1.metric("Direction", f"{get_trend_icon(tr.trend_direction)} {tr.trend_direction.title()}")
    tcol2.metric("Trend Score", f"{tr.trend_score:.2f}")

    if tr.daily_completion_rates:
        days_sorted = sorted(tr.daily_completion_rates.keys())
        fig = create_area_chart(
            x=days_sorted,
            y=[round(tr.daily_completion_rates[d] * 100, 1) for d in days_sorted],
            color=get_trend_color(tr.trend_direction),
            y_title="Completion Rate (%)",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True, key="an_trend_area")
    else:
        st.caption("Not enough daily data yet to chart a trend.")

    st.markdown("---")
    st.markdown("#### Best Productivity Hour")
    bh = profile.best_hour
    if bh.best_hour is not None:
        bcol1, bcol2 = st.columns(2)
        bcol1.metric("Best Hour", f"{bh.best_hour}:00")
        bcol2.metric("Completion Rate at Best Hour", format_percentage(bh.completion_rate_at_best))
        st.markdown(confidence_badge_html(bh.confidence.level), unsafe_allow_html=True)
    else:
        st.caption("Not enough data yet to determine your best hour.")


# ═════════════════════════════════════════════════════════════
# TAB 4 — PATTERNS & INSIGHTS
# ═════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("#### 🔍 Detected Patterns")
    if profile.patterns.patterns:
        for p in profile.patterns.patterns:
            with st.expander(f"{p.pattern_name}  {confidence_badge_html(p.confidence)}", expanded=False):
                st.markdown(f"**Observation:** {p.observation}")
                st.caption(f"Evidence: {p.evidence}")
                if p.affected_categories:
                    st.caption("Affected categories: " + ", ".join(p.affected_categories))
                if p.supporting_metrics:
                    st.json(p.supporting_metrics)
    else:
        st.caption("No recurring patterns detected yet — keep logging tasks.")

    st.markdown("---")
    st.markdown("#### 💡 Insights")
    if profile.insights.insights:
        for ins in profile.insights.insights:
            st.markdown(
                f"""
                <div class="insight-card">
                    <p>💡 {ins.observation} {confidence_badge_html(ins.confidence)}</p>
                    <p class="evidence">{ins.evidence}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.caption("No insights generated yet.")

    st.markdown("---")
    st.markdown("#### ❌ Failure Reasons")
    fa = profile.failure_analysis
    if fa.failure_reason_counts:
        fcol1, fcol2 = st.columns([1, 1])
        with fcol1:
            st.metric("Main Failure Reason", fa.main_failure_reason or "—")
            st.metric("Total Failed", fa.total_failed)
        with fcol2:
            fig = create_donut(
                labels=list(fa.failure_reason_counts.keys()),
                values=list(fa.failure_reason_counts.values()),
                height=260,
            )
            st.plotly_chart(fig, use_container_width=True, key="an_failure_donut")
    else:
        st.caption("No failures recorded — great job! 🎉")


# ═════════════════════════════════════════════════════════════
# TAB 5 — HABITS & BURNOUT
# ═════════════════════════════════════════════════════════════
with tabs[4]:
    hcol1, hcol2 = st.columns(2)

    with hcol1:
        st.markdown("#### 🌱 Habit Scores")
        hb = profile.habits
        st.metric("Overall Habit Score", format_score(hb.overall_habit_score))
        habit_map = {
            "Study": hb.study_habit, "Workout": hb.workout_habit,
            "Reading": hb.reading_habit, "Morning": hb.morning_habit,
            "Evening": hb.evening_habit,
        }
        if any(habit_map.values()):
            fig = create_radar(
                categories=list(habit_map.keys()),
                values=[v / 100 if v > 1 else v for v in habit_map.values()],
                height=320,
            )
            st.plotly_chart(fig, use_container_width=True, key="an_habit_radar")
        if hb.category_habit_scores:
            with st.expander("Per-category habit scores"):
                st.dataframe(
                    [{"Category": k, "Score": round(v, 1)} for k, v in hb.category_habit_scores.items()],
                    use_container_width=True, hide_index=True,
                )

    with hcol2:
        st.markdown("#### 🛡️ Burnout Assessment")
        bo = profile.burnout
        st.plotly_chart(
            create_gauge(
                bo.burnout_risk, title="Burnout Risk", height=240,
                ranges=[
                    {"range": [0, 30], "color": "rgba(16,185,129,0.15)"},
                    {"range": [30, 55], "color": "rgba(245,158,11,0.15)"},
                    {"range": [55, 75], "color": "rgba(249,115,22,0.15)"},
                    {"range": [75, 100], "color": "rgba(239,68,68,0.15)"},
                ],
            ),
            use_container_width=True, key="an_burnout_gauge",
        )
        st.markdown(
            f"<p style='text-align:center; color:{get_burnout_color(bo.burnout_risk)}; font-weight:700;'>"
            f"{get_burnout_label(bo.burnout_risk)}</p>",
            unsafe_allow_html=True,
        )
        bcols = st.columns(2)
        bcols[0].metric("Schedule Density", f"{bo.schedule_density:.0f} min/day")
        bcols[1].metric("Recovery Time", f"{bo.recovery_time:.0f} min")
        bcols2 = st.columns(2)
        bcols2[0].metric("Deep Work Score", format_percentage(bo.deep_work_score))
        bcols2[1].metric("Context Switching", f"{bo.context_switching_score:.1f}/day")
        bcols3 = st.columns(2)
        bcols3[0].metric("Time Fragmentation", format_percentage(bo.time_fragmentation))
        bcols3[1].metric("Habit Stability", format_percentage(bo.habit_stability))


# ═════════════════════════════════════════════════════════════
# TAB 6 — HEATMAPS & CORRELATIONS
# ═════════════════════════════════════════════════════════════
with tabs[5]:
    st.markdown("#### 🗓️ Heatmaps")
    if profile.heatmaps.heatmaps:
        for hm in profile.heatmaps.heatmaps:
            st.markdown(f"**{hm.name}**")
            x_vals = sorted({c.x for c in hm.cells})
            y_vals = sorted({str(c.y) for c in hm.cells})
            grid = [[0.0 for _ in x_vals] for _ in y_vals]
            for c in hm.cells:
                xi = x_vals.index(c.x)
                yi = y_vals.index(str(c.y))
                grid[yi][xi] = c.value
            fig = create_heatmap(
                z=grid,
                x_labels=[str(x) for x in x_vals],
                y_labels=y_vals,
                height=320,
            )
            st.plotly_chart(fig, use_container_width=True, key=f"an_heatmap_{hm.name}")
    else:
        st.caption("No heatmap data available yet.")

    st.markdown("---")
    st.markdown("#### 🔗 Correlations")
    if profile.correlations.correlations:
        for corr in profile.correlations.correlations:
            direction = "🟢 Positive" if corr.value >= 0 else "🔴 Negative"
            st.markdown(
                f"""
                <div class="insight-card">
                    <p>{direction} · <strong>{corr.name}</strong> = {corr.value:.2f}</p>
                    <p class="evidence">{corr.description}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.caption("No correlations computed yet.")


# ═════════════════════════════════════════════════════════════
# TAB 7 — DURATION & WEEKDAY/WEEKEND
# ═════════════════════════════════════════════════════════════
with tabs[6]:
    st.markdown("#### ⏳ Duration Buckets")
    du = profile.duration_analysis
    active_buckets = [b for b in du.buckets if b.total > 0]
    if active_buckets:
        dcol1, dcol2 = st.columns(2)
        dcol1.metric("Best Duration Bucket", du.best_duration or "—")
        dcol2.metric("Worst Duration Bucket", du.worst_duration or "—")

        fig = create_grouped_bar(
            x=[b.bucket for b in active_buckets],
            datasets=[
                {"name": "Completion Rate", "values": [round(b.completion_rate * 100, 1) for b in active_buckets], "color": "#10b981"},
                {"name": "Focus Quality", "values": [round(b.focus_quality * 100, 1) for b in active_buckets], "color": "#6c63ff"},
            ],
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True, key="an_duration_bar")
    else:
        st.caption("No duration-bucket data yet.")

    st.markdown("---")
    st.markdown("#### 📆 Weekday vs Weekend")
    ww = profile.weekday_weekend
    st.metric("Stronger Period", ww.stronger_period.title())

    fig = create_grouped_bar(
        x=["Completion", "Failure", "Focus", "Productivity"],
        datasets=[
            {
                "name": f"Weekday ({ww.weekday_count})",
                "values": [
                    round(ww.weekday_completion_rate * 100, 1),
                    round(ww.weekday_failure_rate * 100, 1),
                    round(ww.weekday_focus * 100, 1),
                    round(ww.weekday_productivity * 100, 1),
                ],
                "color": "#3b82f6",
            },
            {
                "name": f"Weekend ({ww.weekend_count})",
                "values": [
                    round(ww.weekend_completion_rate * 100, 1),
                    round(ww.weekend_failure_rate * 100, 1),
                    round(ww.weekend_focus * 100, 1),
                    round(ww.weekend_productivity * 100, 1),
                ],
                "color": "#a78bfa",
            },
        ],
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True, key="an_weekday_weekend")