"""
CoachAI – AI Coach Page
==========================

Calls the existing RecommendationService (unmodified) and presents the
LLM's coaching output — summary, strengths, weaknesses, recommendations
— as premium, readable cards.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from layout import page_setup, page_title, empty_state
from services import (
    get_current_user_id,
    load_today_plan,
    load_today_tasks,
    load_recent_plans,
    load_recommendations_today,
    load_recommendations_for_plan,
    load_user,
)


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="AI Coach", icon="🤖")
page_title("🤖", "AI Coach", "Personalised coaching, generated from your schedule and history.")

user_id = get_current_user_id()


# ─────────────────────────────────────────────────────────────
# Target Plan Selector (Today vs. a Past Plan)
# ─────────────────────────────────────────────────────────────

today_plan = load_today_plan(user_id=user_id)
recent_plans = load_recent_plans(user_id=user_id, days=30)

tab_today, tab_past = st.tabs(["📅 Today's Plan", "🗂️ A Past Plan"])

target_plan_id: int | None = None
target_label: str = ""
use_today_shortcut = False

with tab_today:
    if today_plan is None:
        empty_state(
            icon="📋",
            title="No plan for today yet",
            message="Create today's plan on the Today's Schedule page, then come back here for coaching.",
            cta="Go to 📅 Today's Schedule →",
        )
    else:
        tasks_today = load_today_tasks(user_id=user_id)
        if not tasks_today:
            empty_state(
                icon="🗒️",
                title="No tasks in today's plan yet",
                message="Your plan for today is empty — add at least one task on the "
                        "Today's Schedule page, then come back here for coaching.",
                cta="Go to 📅 Today's Schedule →",
            )
        else:
            st.markdown(
                f"""
                <div class="insight-card">
                    <p>📅 <strong>{date.today().strftime('%B %d, %Y')}</strong> — {len(tasks_today)} task(s) planned.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            use_today_shortcut = st.button(
                "✨ Generate Today's Coaching Report",
                type="primary",
                use_container_width=True,
                key="gen_today",
            )
            if use_today_shortcut:
                target_plan_id = today_plan["plan_id"]
                target_label = "today"

with tab_past:
    if not recent_plans:
        st.caption("No past plans found in the last 30 days.")
    else:
        options = {
            f"{p['plan_date']} · plan #{p['plan_id']} · {len(p.get('tasks', []))} tasks": p["plan_id"]
            for p in recent_plans
        }
        choice = st.selectbox("Choose a plan to analyse", list(options.keys()))
        if st.button("✨ Generate Coaching Report for This Plan", use_container_width=True, key="gen_past"):
            target_plan_id = options[choice]
            target_label = choice


# ─────────────────────────────────────────────────────────────
# Generate & Cache the Recommendation for This Session
# ─────────────────────────────────────────────────────────────

if target_plan_id is not None:
    with st.status("🧠 Coach is analysing your schedule and history...", expanded=True) as status:
        st.write("Loading today's schedule...")
        st.write("Building your 30-day analytics profile...")
        st.write("Asking the AI coach for personalised feedback...")
        try:
            if use_today_shortcut:
                result = load_recommendations_today(user_id=user_id)
            else:
                result = load_recommendations_for_plan(target_plan_id)
            st.session_state.last_recommendation = result
            st.session_state.last_recommendation_plan_id = target_plan_id
            status.update(label="✅ Coaching report ready", state="complete", expanded=False)
        except ValueError as e:
            status.update(label="⚠️ Could not generate report", state="error", expanded=False)
            st.warning(f"**Nothing to analyse yet.** {e}")
            st.session_state.last_recommendation = None
        except RuntimeError as e:
            status.update(label="❌ AI Coach unavailable", state="error", expanded=False)
            st.error(
                "**The AI Coach couldn't reach the model right now.** "
                "This usually means the Gemini API key is missing, invalid, or rate-limited."
            )
            if st.session_state.get("debug_mode"):
                st.exception(e)
            st.session_state.last_recommendation = None
        except Exception as e:
            status.update(label="❌ Unexpected error", state="error", expanded=False)
            st.error("**Something went wrong while generating your coaching report.**")
            if st.session_state.get("debug_mode"):
                st.exception(e)
            st.session_state.last_recommendation = None


# ─────────────────────────────────────────────────────────────
# Render the Cached Report (persists across reruns/tab switches)
# ─────────────────────────────────────────────────────────────

result = st.session_state.get("last_recommendation")

if result is None:
    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
    empty_state(
        icon="🤖",
        title="No coaching report yet",
        message="Pick a plan above and generate a report — your AI coach will summarise strengths, "
                "weaknesses, and give you concrete next steps.",
    )
else:
    st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)

    user_name = st.session_state.get("user_display_name") or load_user(user_id=user_id).get("display_name", "there")
    st.markdown(
        f"""
        <div class="hero-gradient animate-in">
            <p style="font-size:0.85rem; opacity:0.8; margin-bottom:0.3rem;">🤖 Coach's Summary</p>
            <p style="font-size:1.05rem; line-height:1.6; margin:0;">
                Hey {user_name} — {result.summary}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)

    col_strengths, col_weaknesses = st.columns(2)

    with col_strengths:
        st.markdown("#### ✅ Strengths")
        if result.strengths:
            for s in result.strengths:
                st.markdown(f'<div class="strength-card">💪 {s}</div>', unsafe_allow_html=True)
        else:
            st.caption("No standout strengths identified yet.")

    with col_weaknesses:
        st.markdown("#### ⚠️ Areas to Improve")
        if result.weaknesses:
            for w in result.weaknesses:
                st.markdown(f'<div class="weakness-card">🎯 {w}</div>', unsafe_allow_html=True)
        else:
            st.caption("No weak spots identified yet.")

    st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)
    st.markdown("#### 💡 Recommendations")
    if result.recommendations:
        for i, r in enumerate(result.recommendations, start=1):
            st.markdown(
                f'<div class="rec-card"><strong>{i}.</strong> {r}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No specific recommendations were generated.")

    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
    with st.expander("📄 Raw JSON (for debugging / presentation)"):
        st.json(result.model_dump())