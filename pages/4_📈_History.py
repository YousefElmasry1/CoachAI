"""
CoachAI – History Page
==========================

Browses past plans (Database.get_recent_plans, unmodified), shows
per-plan completion stats and a day-over-day comparison chart, and lets
the user pull an on-demand AI coaching report for any past plan via the
existing RecommendationService.
"""

from __future__ import annotations

import streamlit as st

from layout import page_setup, page_title, empty_state
from helpers import format_date_long, format_duration, get_status_icon
from services import load_recent_plans, load_recommendations_for_plan
from charts import create_area_chart


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="History", icon="📈")
page_title("📈", "History", "Every past plan, how it went, and how it compares.")

if "history_recs" not in st.session_state:
    st.session_state.history_recs = {}


# ─────────────────────────────────────────────────────────────
# Range Selector
# ─────────────────────────────────────────────────────────────

days_back = st.select_slider(
    "Show plans from the last...",
    options=[7, 14, 30, 60, 90, 180],
    value=30,
    format_func=lambda d: f"{d} days",
)

plans = load_recent_plans(days=days_back)

if not plans:
    empty_state(
        icon="🗂️",
        title="No plan history yet",
        message="Once you've created and completed a few plans, they'll show up here.",
    )
    st.stop()

# Sort newest first (defensive — service already orders this way)
plans = sorted(plans, key=lambda p: p["plan_date"], reverse=True)


# ─────────────────────────────────────────────────────────────
# Comparison Chart
# ─────────────────────────────────────────────────────────────

st.markdown("#### 📊 Completion Rate Over Time")

chrono = list(reversed(plans))
dates, rates = [], []
for p in chrono:
    tasks = p.get("tasks", [])
    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    dates.append(p["plan_date"])
    rates.append(round((completed / total) * 100, 1) if total else 0.0)

fig = create_area_chart(x=dates, y=rates, color="#6c63ff", y_title="Completion Rate (%)", height=260)
st.plotly_chart(fig, use_container_width=True, key="history_trend")

st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Plan Cards
# ─────────────────────────────────────────────────────────────

st.markdown(f"#### 🗂️ {len(plans)} Plan(s)")

for plan in plans:
    tasks = plan.get("tasks", [])
    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    pending = total - completed - failed
    total_minutes = sum(t.get("estimated_minutes", 0) for t in tasks)
    completion_pct = (completed / total * 100) if total else 0.0

    header = (
        f"📅 {format_date_long(plan['plan_date'])}  ·  "
        f"{total} tasks  ·  {completion_pct:.0f}% completed  ·  "
        f"{plan.get('status', 'active').title()}"
    )

    with st.expander(header, expanded=False):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Tasks", total)
        m2.metric("Completed", completed)
        m3.metric("Failed", failed)
        m4.metric("Total Time Planned", format_duration(total_minutes))

        if plan.get("ai_summary"):
            st.markdown(
                f'<div class="insight-card"><p>🤖 {plan["ai_summary"]}</p></div>',
                unsafe_allow_html=True,
            )

        if tasks:
            st.dataframe(
                [
                    {
                        "Status": f"{get_status_icon(t.get('status', 'pending'))} {t.get('status', 'pending').title()}",
                        "Task": t.get("title", "Untitled"),
                        "Priority": t.get("priority", 3),
                        "Est. Min": t.get("estimated_minutes", 0),
                        "Actual Min": t.get("actual_minutes") or "—",
                        "Failure Reason": t.get("failure_reason") or "—",
                    }
                    for t in tasks
                ],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("This plan has no tasks.")

        st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
        plan_id = plan["plan_id"]
        if plan_id in st.session_state.history_recs:
            rec = st.session_state.history_recs[plan_id]
            st.markdown("**🤖 Coaching summary for this plan:**")
            st.markdown(f'<div class="rec-card">{rec.summary}</div>', unsafe_allow_html=True)
            with st.expander("Full report"):
                st.markdown("**Strengths**")
                for s in rec.strengths:
                    st.markdown(f'<div class="strength-card">💪 {s}</div>', unsafe_allow_html=True)
                st.markdown("**Weaknesses**")
                for w in rec.weaknesses:
                    st.markdown(f'<div class="weakness-card">🎯 {w}</div>', unsafe_allow_html=True)
                st.markdown("**Recommendations**")
                for r in rec.recommendations:
                    st.markdown(f'<div class="rec-card">💡 {r}</div>', unsafe_allow_html=True)
        else:
            if st.button("✨ Get AI Coaching for This Plan", key=f"hist_coach_{plan_id}"):
                with st.spinner("Analysing this plan..."):
                    try:
                        rec = load_recommendations_for_plan(plan_id)
                        st.session_state.history_recs[plan_id] = rec
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't generate a report for this plan: {e}")
