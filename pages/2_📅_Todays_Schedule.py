"""
CoachAI – Today's Schedule Page
==================================

Visualises today's plan as a Gantt-style timeline plus rich task cards,
lets the user run the deterministic Scheduler (scheduler_service.py,
unmodified) to assign time slots, and lets them mark tasks
completed/failed — all through existing Database methods only.
"""

from __future__ import annotations

from datetime import time

import streamlit as st

from config import FAILURE_REASONS, PRIORITY_COLORS, PRIORITY_LABELS
from layout import page_setup, page_title, empty_state
from helpers import (
    format_duration,
    format_time_12h,
    get_priority_icon,
    get_status_icon,
    progress_bar_html,
    status_badge_html,
)
from services import (
    get_current_user_id,
    load_today_plan,
    load_today_tasks,
    load_categories,
    create_today_plan,
    generate_today_plan,
    add_task_to_plan,
    delete_task,
    update_task_status,
    run_scheduler_for_today,
)
from charts import create_timeline, create_donut


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Today's Schedule", icon="📅")
page_title("📅", "Today's Schedule", "Your plan for today, scheduled and tracked in real time.")

user_id = get_current_user_id()

plan = load_today_plan(user_id=user_id)


# ─────────────────────────────────────────────────────────────
# Onboarding: No Plan Yet
# ─────────────────────────────────────────────────────────────

if plan is None:
    empty_state(
        icon="📋",
        title="No plan for today",
        message="Describe your day below and let the AI Planner split it into tasks, "
                "or create an empty plan and add tasks yourself.",
    )
    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)

    tab_ai, tab_manual = st.tabs(["✨ AI Planner", "✍️ Manual"])

    with tab_ai:
        with st.form("ai_plan_form"):
            st.markdown("#### ✨ Describe Your Day")
            ai_raw_input = st.text_area(
                "What's on your mind for today?",
                placeholder="e.g. Study for the database exam for 2 hours, gym at 6pm, "
                            "finish the CoachAI report...",
                height=90,
                key="ai_raw_input",
            )
            ai_submitted = st.form_submit_button(
                "✨ Generate My Plan", type="primary", use_container_width=True
            )

        if ai_submitted:
            if not ai_raw_input or not ai_raw_input.strip():
                st.error("Please describe your day first.")
            else:
                with st.spinner("The AI Planner is splitting your day into tasks..."):
                    try:
                        result = generate_today_plan(raw_input=ai_raw_input, user_id=user_id)
                        st.session_state.last_planner_result = result
                        st.toast(
                            f"Plan created with {len(result['tasks'])} task(s)! 🎉",
                            icon="✅",
                        )
                        st.rerun()
                    except ValueError as e:
                        st.warning(f"**Nothing to plan yet.** {e}")
                    except RuntimeError as e:
                        st.error(
                            "**The AI Planner couldn't reach the model right now.** "
                            "This usually means the Gemini API key is missing, invalid, "
                            "or rate-limited."
                        )
                        if st.session_state.get("debug_mode"):
                            st.exception(e)
                    except Exception as e:
                        st.error("**Something went wrong while generating your plan.**")
                        if st.session_state.get("debug_mode"):
                            st.exception(e)

    with tab_manual:
        manual_categories = load_categories(user_id=user_id)
        with st.form("create_plan_form"):
            st.markdown("#### ✍️ Add Your First Task")
            st.caption(
                "This creates today's plan and your first task in one step. "
                "You can add more tasks below afterward."
            )
            c1, c2 = st.columns(2)
            with c1:
                m_title = st.text_input("Task title *")
                m_minutes = st.number_input(
                    "Estimated minutes", min_value=5, max_value=480, value=30, step=5
                )
            with c2:
                m_priority = st.select_slider(
                    "Priority", options=[1, 2, 3, 4, 5],
                    value=3, format_func=lambda p: f"{get_priority_icon(p)} {PRIORITY_LABELS[p]}",
                )
                m_cat_options = {
                    "— None —": None,
                    **{c["name"]: c["category_id"] for c in manual_categories},
                }
                m_cat_choice = st.selectbox("Category", list(m_cat_options.keys()))
            m_description = st.text_area("Notes (optional)", height=70)

            submitted = st.form_submit_button("Create Plan", use_container_width=True)
            if submitted:
                if not m_title.strip():
                    st.error("Please give the task a title.")
                else:
                    plan_id = create_today_plan(raw_input=m_title.strip(), user_id=user_id)
                    if plan_id:
                        new_id = add_task_to_plan(
                            plan_id=plan_id,
                            title=m_title.strip(),
                            category_id=m_cat_options[m_cat_choice],
                            description=m_description.strip(),
                            priority=m_priority,
                            estimated_minutes=int(m_minutes),
                            order_index=0,
                        )
                        if new_id:
                            st.toast("Plan and first task created! 🎉", icon="✅")
                            st.rerun()
                        else:
                            st.warning("Plan created, but the task couldn't be saved. Add it below.")
                            st.rerun()
                    else:
                        st.error("Couldn't create a plan — one may already exist for today.")

    st.stop()


# ─────────────────────────────────────────────────────────────
# Show AI Planner Results (once, right after generation)
# ─────────────────────────────────────────────────────────────

_planner_result = st.session_state.pop("last_planner_result", None)
if _planner_result is not None:
    st.markdown(
        f'<div class="insight-card">🤖 {_planner_result["planning_notes"]}</div>',
        unsafe_allow_html=True,
    )
    _needs_review = [t for t in _planner_result["tasks"] if t.get("needs_review")]
    if _needs_review:
        with st.expander(f"⚠️ {len(_needs_review)} task(s) may need a quick review", expanded=True):
            for t in _needs_review:
                st.markdown(
                    f"- **{t['title']}** — {t.get('review_reason') or 'Please double-check this one.'}"
                )
    st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Load Tasks
# ─────────────────────────────────────────────────────────────

tasks = load_today_tasks(user_id=user_id)
categories = load_categories(user_id=user_id)
category_lookup = {c["category_id"]: c for c in categories}
is_scheduled = any(t.get("scheduled_start") for t in tasks)


# ─────────────────────────────────────────────────────────────
# Schedule Summary Row
# ─────────────────────────────────────────────────────────────

total_estimated = sum(t.get("estimated_minutes", 0) for t in tasks)
total_scheduled = sum(1 for t in tasks if t.get("scheduled_start"))
completed_count = sum(1 for t in tasks if t.get("status") == "completed")
failed_count = sum(1 for t in tasks if t.get("status") == "failed")

sum_cols = st.columns(4)
with sum_cols[0]:
    st.metric("Total Tasks", len(tasks))
with sum_cols[1]:
    st.metric("Total Estimated Time", format_duration(total_estimated))
with sum_cols[2]:
    st.metric("Scheduled", f"{total_scheduled}/{len(tasks)}")
with sum_cols[3]:
    st.metric("Completed", f"{completed_count}/{len(tasks)}", delta=f"{failed_count} failed" if failed_count else None)

progress_val = (completed_count / len(tasks)) if tasks else 0.0
st.markdown(progress_bar_html(progress_val, color="#10b981", height=10), unsafe_allow_html=True)
st.caption(f"{format_duration(total_estimated)} planned · {progress_val:.0%} completed so far")

st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Scheduler Controls
# ─────────────────────────────────────────────────────────────

with st.expander("⏱️ Run / Re-run the Scheduler", expanded=not is_scheduled):
    st.caption(
        "The Scheduler assigns start/end times to every task in order, "
        "inserting any breaks you define below. It never reorders or resizes tasks."
    )
    work_start = st.time_input("Work day starts at", value=time(9, 0))

    st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
    st.markdown("**☕ Breaks**")

    if "schedule_breaks" not in st.session_state:
        st.session_state.schedule_breaks = []

    # Existing break rows — each editable and individually removable
    remove_idx = None
    for i, brk in enumerate(st.session_state.schedule_breaks):
        b_cols = st.columns([2, 2, 1])
        with b_cols[0]:
            new_start = st.time_input(
                "Starts at", value=brk["start"], key=f"break_start_{brk['id']}"
            )
        with b_cols[1]:
            new_minutes = st.number_input(
                "Duration (min)", min_value=5, max_value=180,
                value=brk["minutes"], step=5, key=f"break_minutes_{brk['id']}",
            )
        with b_cols[2]:
            st.markdown("<div style='height:1.8rem;'></div>", unsafe_allow_html=True)
            if st.button("🗑️", key=f"break_del_{brk['id']}", use_container_width=True):
                remove_idx = i
        brk["start"] = new_start
        brk["minutes"] = int(new_minutes)

    if remove_idx is not None:
        st.session_state.schedule_breaks.pop(remove_idx)
        st.rerun()

    if st.button("➕ Add another break"):
        next_id = (
            max((b["id"] for b in st.session_state.schedule_breaks), default=0) + 1
        )
        st.session_state.schedule_breaks.append(
            {"id": next_id, "start": time(12, 0), "minutes": 30}
        )
        st.rerun()

    if not st.session_state.schedule_breaks:
        st.caption("No breaks added yet — the scheduler will run without one.")

    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)

    breaks = [
        (b["start"], b["minutes"]) for b in st.session_state.schedule_breaks
    ]

    if st.button("▶️ Run Scheduler", type="primary"):
        with st.spinner("Assigning time slots..."):
            scheduled = run_scheduler_for_today(work_day_start=work_start, breaks=breaks, user_id=user_id)
        if scheduled:
            st.toast("Schedule updated!", icon="✅")
            st.rerun()
        else:
            st.error("Scheduling failed. Make sure today's plan has at least one task.")


# ─────────────────────────────────────────────────────────────
# Timeline + Distribution
# ─────────────────────────────────────────────────────────────

if not tasks:
    empty_state(
        icon="🗒️",
        title="No tasks in today's plan yet",
        message="Add your first task below to see it on the timeline.",
    )
else:
    tl_col, dist_col = st.columns([3, 2])
    with tl_col:
        st.markdown("#### 🗓️ Timeline")
        if is_scheduled:
            fig = create_timeline(tasks, title="")
            st.plotly_chart(fig, use_container_width=True, key="schedule_timeline")
        else:
            st.info("Run the Scheduler above to see your tasks on a timeline.", icon="⏱️")

    with dist_col:
        st.markdown("#### 🥯 Task Distribution")
        priority_counts: dict[int, int] = {}
        for t in tasks:
            p = t.get("priority", 3)
            priority_counts[p] = priority_counts.get(p, 0) + 1
        if priority_counts:
            ordered_priorities = sorted(priority_counts.keys())
            fig_donut = create_donut(
                labels=[PRIORITY_LABELS.get(p, str(p)) for p in ordered_priorities],
                values=[priority_counts[p] for p in ordered_priorities],
                colors=[PRIORITY_COLORS.get(p, "#6b7280") for p in ordered_priorities],
                height=260,
            )
            st.plotly_chart(fig_donut, use_container_width=True, key="priority_donut")

st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Task Cards (expandable, with status actions)
# ─────────────────────────────────────────────────────────────

st.markdown("#### 📋 Tasks")

for task in tasks:
    priority = task.get("priority", 3)
    status = task.get("status", "pending")
    p_color = PRIORITY_COLORS.get(priority, "#6b7280")
    cat = category_lookup.get(task.get("category_id"))
    cat_name = cat["name"] if cat else "Uncategorised"

    with st.expander(
        f"{get_status_icon(status)} {task.get('title', 'Untitled')}  ·  "
        f"{format_time_12h(task.get('scheduled_start'))} – {format_time_12h(task.get('scheduled_end'))}",
        expanded=False,
    ):
        top = st.columns([3, 1])
        with top[0]:
            st.markdown(
                f"""
                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:8px;">
                    {status_badge_html(status)}
                    <span class="badge" style="background:{p_color}20; color:{p_color};">
                        {get_priority_icon(priority)} {PRIORITY_LABELS.get(priority, priority)}
                    </span>
                    <span class="badge badge-muted">🏷️ {cat_name}</span>
                    <span class="badge badge-muted">⏱️ {format_duration(task.get('estimated_minutes'))}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if task.get("description"):
                st.caption(task["description"])
        with top[1]:
            if st.button("🗑️ Delete", key=f"del_{task['task_id']}", use_container_width=True):
                if delete_task(task["task_id"]):
                    st.toast("Task deleted.", icon="🗑️")
                    st.rerun()

        if status in ("pending", "in_progress"):
            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("✅ Mark Completed", key=f"complete_{task['task_id']}", use_container_width=True):
                    # actual_minutes is left unset on purpose: the database
                    # auto-computes it from (now - started_at) if the task
                    # was started, or falls back to the estimate otherwise.
                    if update_task_status(task["task_id"], status="completed"):
                        st.toast("Nice work! Task completed.", icon="🎉")
                        st.rerun()
            with action_cols[1]:
                if status == "pending":
                    if st.button("▶️ Start Task", key=f"start_{task['task_id']}", use_container_width=True):
                        if update_task_status(task["task_id"], status="in_progress"):
                            st.toast("Timer started ⏱️", icon="▶️")
                            st.rerun()
            with action_cols[2]:
                with st.popover("❌ Mark Failed", use_container_width=True):
                    reason = st.selectbox(
                        "Why?", FAILURE_REASONS, key=f"reason_{task['task_id']}"
                    )
                    if st.button("Confirm", key=f"fail_{task['task_id']}"):
                        if update_task_status(task["task_id"], status="failed", failure_reason=reason):
                            st.toast("Logged — your coach will factor this in.", icon="📝")
                            st.rerun()
        else:
            st.caption(f"Marked {status} · {format_duration(task.get('actual_minutes'))} actual")


# ─────────────────────────────────────────────────────────────
# Add More Tasks (AI Planner / Manual — same choice as onboarding)
# ─────────────────────────────────────────────────────────────

st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
with st.expander("➕ Add Tasks to Today's Plan"):
    add_tab_ai, add_tab_manual = st.tabs(["✨ AI Planner", "✍️ Manual"])

    with add_tab_ai:
        with st.form("ai_add_tasks_form", clear_on_submit=True):
            st.markdown("#### ✨ Describe Your Day")
            more_raw_input = st.text_area(
                "What's on your mind for today?",
                placeholder="e.g. Study for the database exam for 2 hours, gym at 6pm, "
                            "finish the CoachAI report...",
                height=90,
                key="more_raw_input",
            )
            more_submitted = st.form_submit_button(
                "✨ Generate My Plan", type="primary", use_container_width=True
            )

        if more_submitted:
            if not more_raw_input or not more_raw_input.strip():
                st.error("Please describe what you need to add first.")
            else:
                with st.spinner("The AI Planner is splitting this into tasks..."):
                    try:
                        more_result = generate_today_plan(raw_input=more_raw_input, user_id=user_id)
                        st.session_state.last_planner_result = more_result
                        st.toast(
                            f"Added {len(more_result['tasks'])} task(s)! 🎉", icon="✅"
                        )
                        st.rerun()
                    except ValueError as e:
                        st.warning(f"**Nothing to add yet.** {e}")
                    except RuntimeError as e:
                        st.error(
                            "**The AI Planner couldn't reach the model right now.** "
                            "This usually means the Gemini API key is missing, invalid, "
                            "or rate-limited."
                        )
                        if st.session_state.get("debug_mode"):
                            st.exception(e)
                    except Exception as e:
                        st.error("**Something went wrong while adding these tasks.**")
                        if st.session_state.get("debug_mode"):
                            st.exception(e)

    with add_tab_manual:
        with st.form("add_task_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                title = st.text_input("Task title *")
                minutes = st.number_input("Estimated minutes", min_value=5, max_value=480, value=30, step=5)
            with c2:
                priority = st.select_slider(
                    "Priority", options=[1, 2, 3, 4, 5],
                    value=3, format_func=lambda p: f"{get_priority_icon(p)} {PRIORITY_LABELS[p]}",
                )
                cat_options = {"— None —": None, **{c["name"]: c["category_id"] for c in categories}}
                cat_choice = st.selectbox("Category", list(cat_options.keys()))
            description = st.text_area("Notes (optional)", height=70)

            if st.form_submit_button("Add Task", type="primary", use_container_width=True):
                if not title.strip():
                    st.error("Please give the task a title.")
                else:
                    new_id = add_task_to_plan(
                        plan_id=plan["plan_id"],
                        title=title.strip(),
                        category_id=cat_options[cat_choice],
                        description=description.strip(),
                        priority=priority,
                        estimated_minutes=int(minutes),
                        order_index=len(tasks),
                    )
                    if new_id:
                        st.toast("Task added!", icon="➕")
                        st.rerun()
                    else:
                        st.error("Couldn't add the task. Please try again.")