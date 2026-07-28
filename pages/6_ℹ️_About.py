"""
CoachAI – About Page
========================

Static, presentation-friendly explanation of the system architecture.
Built entirely from HTML/CSS (no extra dependencies) so it renders
reliably during a live demo.
"""

from __future__ import annotations

import streamlit as st

from config import APP_NAME, APP_DESCRIPTION, APP_VERSION
from layout import page_setup, page_title
from services import get_system_info, get_database_status


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="About", icon="ℹ️")
page_title("ℹ️", f"About {APP_NAME}", APP_DESCRIPTION)


# ─────────────────────────────────────────────────────────────
# Architecture Diagram (pure HTML/CSS)
# ─────────────────────────────────────────────────────────────

st.markdown("#### 🧭 System Architecture")

_NODE = """
<div style="background:var(--bg-card); border:1px solid var(--border); border-radius:14px;
            padding:1rem 1.2rem; text-align:center; min-width:150px; box-shadow:var(--shadow-sm);">
    <div style="font-size:1.6rem;">{icon}</div>
    <div style="font-weight:700; color:var(--text-primary); font-size:0.9rem; margin-top:2px;">{title}</div>
    <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px;">{subtitle}</div>
</div>
"""
_ARROW = """<div style="display:flex; align-items:center; justify-content:center; color:var(--text-muted); font-size:1.4rem;">→</div>"""
_ARROW_DOWN = """<div style="display:flex; align-items:center; justify-content:center; color:var(--text-muted); font-size:1.4rem; margin:4px 0;">↓</div>"""

st.markdown(
    f"""
    <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:center;">
        {_NODE.format(icon="👤", title="User Input", subtitle="Streamlit UI")}
        {_ARROW}
        {_NODE.format(icon="🗄️", title="Database", subtitle="SQLite · database.py")}
        {_ARROW}
        {_NODE.format(icon="🗓️", title="Scheduler", subtitle="scheduler.py")}
    </div>
    {_ARROW_DOWN}
    <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:center;">
        {_NODE.format(icon="📊", title="Analytics Engine", subtitle="analytics.py · 40+ metrics")}
        {_ARROW}
        {_NODE.format(icon="🧩", title="Analytics Formatter", subtitle="LLM-ready context")}
        {_ARROW}
        {_NODE.format(icon="🤖", title="Recommendation Engine", subtitle="Gemini · recommendation.py")}
    </div>
    {_ARROW_DOWN}
    <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:center;">
        {_NODE.format(icon="🖥️", title="Streamlit Frontend", subtitle="Dashboard · Coach · Analytics")}
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Component Explanations
# ─────────────────────────────────────────────────────────────

st.markdown("#### 🧱 Core Components")

components = [
    ("🗄️", "Database Layer", "database.py",
     "SQLite persistence with 7 tables (users, plans, tasks, categories, "
     "user_profiles, badges, user_badges). Provides parameterised query "
     "helpers and a transaction context manager — the single source of "
     "truth every other layer reads from."),
    ("🗓️", "Scheduler", "scheduler.py + scheduler_service.py",
     "A deterministic algorithm that assigns start/end times to a plan's "
     "tasks in order, respecting the user's work-day start and any "
     "user-defined breaks. Never reorders or resizes tasks — it only "
     "places them on the clock."),
    ("📊", "Analytics Engine", "analytics.py",
     "Computes 15+ independent metrics — productivity, priority, category, "
     "planning bias, consistency, trend, burnout, habits, heatmaps, "
     "correlations, duration buckets, weekday/weekend — each with its own "
     "confidence score based on sample size and observation days."),
    ("🤖", "Recommendation Engine", "recommendation.py + recommendation_service.py",
     "Feeds today's schedule and the Analytics Formatter's precomputed "
     "context into Gemini through LangChain, with a Pydantic output "
     "parser that forces structured JSON — summary, strengths, "
     "weaknesses, and actionable recommendations."),
    ("🖥️", "Streamlit Frontend", "app.py + pages/",
     "Consumes every backend service exactly as-is through a thin "
     "caching layer (services.py) — Dashboard, Today's Schedule, "
     "Analytics, AI Coach, History, and Settings."),
]

for icon, title, module, desc in components:
    st.markdown(
        f"""
        <div class="task-card" style="align-items:flex-start;">
            <div style="font-size:1.6rem;">{icon}</div>
            <div class="task-info">
                <div class="task-title">{title}</div>
                <div class="task-meta" style="margin-bottom:4px;"><span>📄 {module}</span></div>
                <p style="color:var(--text-secondary); font-size:0.85rem; margin:0;">{desc}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# AI Flow
# ─────────────────────────────────────────────────────────────

st.markdown("#### 🔄 How a Coaching Report Gets Generated")
st.markdown(
    """
    1. **Load today's schedule** — tasks for the active plan, formatted as plain text.
    2. **Build a 30-day analytics profile** — every metric in `AnalyticsProfile`, computed fresh from the database.
    3. **Format for the LLM** — `AnalyticsFormatter.to_llm_context()` turns the profile into a structured, human-readable brief so the model never re-derives numbers itself.
    4. **Ask Gemini** — a strict system prompt instructs the model to combine today's schedule with historical patterns and return **only** valid JSON.
    5. **Parse & validate** — a Pydantic `PydanticOutputParser` guarantees the response matches `RecommendationOutput` before it ever reaches the UI.
    """
)

st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Tech Stack & Live Stats
# ─────────────────────────────────────────────────────────────

st.markdown("#### 🛠️ Tech Stack")
stack_cols = st.columns(4)
stack_items = [
    ("🐍", "Python", "Core language"),
    ("🎈", "Streamlit", "Frontend framework"),
    ("🗄️", "SQLite", "Database"),
    ("✅", "Pydantic", "Data validation"),
    ("🔗", "LangChain", "LLM orchestration"),
    ("✨", "Gemini", "AI reasoning"),
    ("📈", "Plotly", "Visualisations"),
    ("🎨", "Custom CSS", "Premium UI theme"),
]
for i, (icon, name, desc) in enumerate(stack_items):
    with stack_cols[i % 4]:
        st.markdown(
            f"""
            <div class="kpi-card" style="text-align:center; padding:1rem;">
                <div style="font-size:1.6rem;">{icon}</div>
                <div style="font-weight:700; color:var(--text-primary); font-size:0.88rem;">{name}</div>
                <div style="font-size:0.72rem; color:var(--text-muted);">{desc}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)

st.markdown("#### 📡 Live System Info")
sys_info = get_system_info()
db_status = get_database_status()
info_cols = st.columns(5)
info_cols[0].metric("App Version", APP_VERSION)
info_cols[1].metric("Database", "Online" if db_status["connected"] else "Offline")
info_cols[2].metric("AI Model", sys_info.get("ai_model", "—"))
info_cols[3].metric("Analytics", f"v{sys_info.get('analytics_version', '?')}")
info_cols[4].metric("DB Size", db_status.get("size_display", "—"))
