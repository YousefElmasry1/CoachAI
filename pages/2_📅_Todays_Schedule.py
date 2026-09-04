"""
CoachAI – Today's Schedule Page
==================================

Visualises today's plan as a Gantt-style timeline plus rich task cards,
lets the user run the deterministic Scheduler (scheduler_service.py,
unmodified) to assign time slots, and lets them mark tasks
completed/failed — all through existing Database methods only.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

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
    draft_today_plan,
    save_planner_draft,
    draft_plan_total_minutes,
    check_capacity_for_today,
    add_task_to_plan,
    delete_task,
    defer_task_to_tomorrow,
    save_draft_tasks_to_plan,
    defer_draft_tasks_to_tomorrow,
    update_task_status,
    start_task_timer,
    pause_task_timer,
    resume_task_timer,
    finish_task_with_timer,
    get_task_elapsed_seconds,
    get_task_paused_seconds,
    is_task_timer_paused,
    load_pause_matrix,
    run_scheduler_for_today,
    get_last_scheduling_conflicts,
    get_persisted_scheduling_conflicts,
    get_last_fixed_task_conflicts,
    get_persisted_fixed_task_conflicts,
    reschedule_fixed_task,
    get_last_scheduler_error,
    add_break_to_plan,
    reschedule_break,
    start_break,
    complete_break,
    transcribe_voice_note,
    is_google_calendar_connected,
    sync_google_calendar,
    get_last_sync_time,
    get_google_calendar_events_today,
    import_calendar_event_as_task,
    get_selected_calendars,
    export_all_scheduled_tasks,
    get_stale_export_count,
)
from voice_service import VoiceTranscriptionError
from charts import create_timeline, create_donut


# ─────────────────────────────────────────────────────────────
# Break "session's over" messages — one is picked at random each time
# an in-progress break's countdown is rendered, so it doesn't say the
# exact same line every single time.
# ─────────────────────────────────────────────────────────────
BREAK_OVER_MESSAGES = [
    "Nicely paced — back to it.",
    "Recharged and ready.",
    "That's the reset you needed.",
    "Good call taking that.",
    "Alright, let's keep the momentum going.",
    "Batteries topped up.",
    "Short and sweet — on you go.",
    "Clear head, full steam ahead.",
]


def render_break_countdown(task_id: int, remaining_seconds: int, key_suffix: str) -> None:
    """
    Render a live client-side countdown for an in-progress break.

    ``remaining_seconds`` is computed server-side (from the task's real
    ``started_at`` timestamp + its duration) on every render, so the
    displayed time is always accurate even after a page reload — the
    JS below only handles the *visual* per-second ticking between
    reruns, it never owns the source of truth.
    """
    message = random.choice(BREAK_OVER_MESSAGES)
    html_code = f"""
    <div id="break-wrap-{key_suffix}" style="
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        text-align: center;
        padding: 18px 12px;
        border-radius: 12px;
        background: linear-gradient(135deg, rgba(124,58,237,0.14), rgba(79,70,229,0.14));
        border: 1px solid rgba(124,58,237,0.35);
    ">
        <div id="timer-display-{key_suffix}" style="font-size: 2.1rem; font-weight: 700; color: #a78bfa; letter-spacing: 1px;">
            --:--
        </div>
        <div id="timer-label-{key_suffix}" style="color: #9ca3af; font-size: 0.85rem; margin-top: 2px;">
            ☕ Break in progress
        </div>
        <div id="timer-alert-{key_suffix}" style="display:none; margin-top:12px; padding:10px 14px; border-radius:8px; background:rgba(16,185,129,0.14); color:#34d399; font-weight:600;">
            ☕ Break's over! {message}
            <div style="font-weight:400; font-size:0.82rem; color:#9ca3af; margin-top:2px;">
                Continue your day →
            </div>
        </div>
    </div>
    <script>
        (function() {{
            let remaining = {remaining_seconds};
            const display = document.getElementById("timer-display-{key_suffix}");
            const label = document.getElementById("timer-label-{key_suffix}");
            const alertBox = document.getElementById("timer-alert-{key_suffix}");

            function tick() {{
                if (remaining <= 0) {{
                    display.textContent = "00:00";
                    label.style.display = "none";
                    alertBox.style.display = "block";
                    clearInterval(interval);
                    return;
                }}
                const m = Math.floor(remaining / 60);
                const s = remaining % 60;
                display.textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
                remaining -= 1;
            }}
            tick();
            const interval = setInterval(tick, 1000);
        }})();
    </script>
    """
    components.html(html_code, height=150)


def render_break_card(b: dict) -> None:
    """
    Compact card for a break task: just its time window and one action
    button (Start → live countdown → Done), instead of the full
    complete/fail/category controls a real task gets.
    """
    status = b.get("status", "pending")
    with st.container(border=True):
        cols = st.columns([4, 1])
        with cols[0]:
            st.markdown(
                f"☕ **{b.get('title', 'Break')}** · "
                f"{format_time_12h(b.get('scheduled_start'))} – "
                f"{format_time_12h(b.get('scheduled_end'))}"
            )
        with cols[1]:
            if st.button("🗑️", key=f"break_card_del_{b['task_id']}", use_container_width=True):
                if delete_task(b["task_id"]):
                    st.toast("Break removed.", icon="🗑️")
                    st.rerun()

        if status == "pending":
            if st.button("▶️ Start Break", key=f"break_start_{b['task_id']}", use_container_width=True):
                if start_break(b["task_id"]):
                    st.toast("Break started ☕", icon="▶️")
                    st.rerun()
        elif status == "in_progress":
            duration_minutes = int(b.get("estimated_minutes") or 0)
            remaining_seconds = duration_minutes * 60
            started_at_raw = b.get("started_at")
            if started_at_raw:
                try:
                    started_dt = datetime.fromisoformat(str(started_at_raw))
                    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    elapsed = (now_utc - started_dt).total_seconds()
                    remaining_seconds = max(0, int(duration_minutes * 60 - elapsed))
                except (ValueError, TypeError):
                    pass
            render_break_countdown(b["task_id"], remaining_seconds, key_suffix=str(b["task_id"]))
            if st.button("✅ Done — continue your day", key=f"break_done_{b['task_id']}", use_container_width=True):
                if complete_break(b["task_id"]):
                    st.toast("Break complete. Back at it! 💪", icon="✅")
                    st.rerun()
        else:
            st.caption("✅ Break completed")


TASK_TIME_UP_MESSAGES = [
    "Estimated time's up.",
    "That's the time you planned for.",
    "Hit your estimate.",
    "Estimate reached.",
]


def render_task_countdown(task_id: int, remaining_seconds: int, estimated_seconds: int, key_suffix: str) -> None:
    """
    Live client-side countdown for an active (non-paused) task timer.
    Running past zero isn't alarming for a real task — it's normal to
    go over an estimate — so once it crosses zero this shows a single,
    low-key "estimated time's up" banner (once) and then keeps counting
    upward as "+mm:ss (N% over)" in a muted amber, rather than the
    break countdown's more insistent end-of-timer alert.

    ``remaining_seconds`` is computed server-side (via
    get_task_elapsed_seconds, which already excludes any paused time)
    on every render, so it's always accurate after a reload — same
    accuracy contract as render_break_countdown.
    """
    message = random.choice(TASK_TIME_UP_MESSAGES)
    html_code = f"""
    <div id="task-wrap-{key_suffix}" style="
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        text-align: center;
        padding: 10px 12px;
        border-radius: 10px;
        background: rgba(124,58,237,0.10);
        border: 1px solid rgba(124,58,237,0.25);
    ">
        <div id="task-timer-display-{key_suffix}" style="font-size: 1.5rem; font-weight: 700; color: #a78bfa;">
            --:--
        </div>
        <div id="task-timer-label-{key_suffix}" style="color: #9ca3af; font-size: 0.78rem; margin-top: 1px;">
            time left
        </div>
        <div id="task-timer-alert-{key_suffix}" style="display:none; margin-top:8px; padding:6px 10px; border-radius:6px; background:rgba(245,158,11,0.14); color:#f59e0b; font-size:0.8rem; font-weight:600;">
            ⏰ {message}
        </div>
    </div>
    <script>
        (function() {{
            let remaining = {remaining_seconds};
            const estimated = {estimated_seconds};
            const display = document.getElementById("task-timer-display-{key_suffix}");
            const label = document.getElementById("task-timer-label-{key_suffix}");
            const alertBox = document.getElementById("task-timer-alert-{key_suffix}");
            let alertShown = {str(remaining_seconds <= 0).lower()};

            function fmt(totalSeconds) {{
                const sign = totalSeconds < 0 ? "+" : "";
                const abs = Math.abs(totalSeconds);
                const m = Math.floor(abs / 60);
                const s = abs % 60;
                return sign + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
            }}

            function tick() {{
                display.textContent = fmt(remaining);
                if (remaining <= 0) {{
                    display.style.color = "#f59e0b";
                    const overSeconds = -remaining;
                    const pct = estimated > 0 ? Math.round((overSeconds / estimated) * 100) : 0;
                    label.textContent = pct > 0 ? pct + "% over estimate" : "over estimate";
                    if (!alertShown) {{
                        alertBox.style.display = "block";
                        alertShown = true;
                    }}
                }}
                remaining -= 1;
            }}
            tick();
            setInterval(tick, 1000);
        }})();
    </script>
    """
    components.html(html_code, height=115)



# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Today's Schedule", icon="📅")
page_title("📅", "Today's Schedule", "Your plan for today, scheduled and tracked in real time.")

user_id = get_current_user_id()

# ─────────────────────────────────────────────────────────────
# Google Calendar Auto-Sync (silent, on page load)
# ─────────────────────────────────────────────────────────────

try:
    if is_google_calendar_connected(user_id):
        from datetime import datetime
        _last_sync = get_last_sync_time(user_id)
        _should_sync = True
        if _last_sync:
            try:
                _sync_dt = datetime.fromisoformat(_last_sync)
                _should_sync = (datetime.now() - _sync_dt).total_seconds() > 900
            except (ValueError, TypeError):
                pass
        if _should_sync:
            sync_google_calendar(user_id)
except Exception:
    pass  # Never block the page for a sync failure

plan = load_today_plan(user_id=user_id)


# ─────────────────────────────────────────────────────────────
# Realistic Capacity Warning
#
# Before a freshly-drafted (AI Planner / Voice) or manually-entered
# task is actually saved, check it against this user's historical
# Realistic Capacity (analytics.py::CapacityCalculator). If today's
# total planned load would land meaningfully above what this specific
# user has historically been able to finish, pause and show a warning
# instead of silently saving — with evidence, and a choice: create it
# anyway, try a lighter-plan suggestion, or cancel.
#
# One pending-warning "slot" per context (identified by key_prefix), so
# the AI tab, Voice tab, and Manual tab in both the onboarding section
# and the "Add More Tasks" section below can each have their own
# independent pending warning without clashing.
# ─────────────────────────────────────────────────────────────

def _capacity_state_key(key_prefix: str) -> str:
    return f"_capacity_warning_{key_prefix}"


def _get_capacity_warning(key_prefix: str) -> dict | None:
    return st.session_state.get(_capacity_state_key(key_prefix))


def _set_capacity_warning(key_prefix: str, **kwargs) -> None:
    st.session_state[_capacity_state_key(key_prefix)] = kwargs


def _clear_capacity_warning(key_prefix: str) -> None:
    st.session_state.pop(_capacity_state_key(key_prefix), None)


def _capacity_evidence_caption(info: dict) -> None:
    """Shared 'why we're saying this' line, shown when we have real history."""
    if info["basis"] == "insufficient_data":
        return
    st.caption(
        f"📊 On days you planned around this much before, you completed "
        f"only **{info['heavy_day_completion_rate']:.0%}** of your tasks "
        f"on average — versus **{info['light_day_completion_rate']:.0%}** "
        f"on lighter days."
    )


def _capacity_split(plan_output, recommended_minutes: int) -> tuple[list, list, list]:
    """
    Split a drafted plan's tasks into (fixed, keep, defer) using the
    same greedy priority-budget rule described in
    _capacity_suggestions()'s docstring. Shared by the "Keep top
    priorities, defer the rest" direct action and its preview text.
    """
    tasks = list(plan_output.tasks or [])
    fixed = [t for t in tasks if t.is_fixed_time]
    flexible = sorted(
        [t for t in tasks if not t.is_fixed_time],
        key=lambda t: t.priority,  # 1 = highest importance first
    )
    fixed_minutes = sum(t.estimated_minutes for t in fixed)
    budget = max(0, recommended_minutes - fixed_minutes)

    keep: list = []
    defer: list = []
    running = 0
    for t in flexible:
        if running + t.estimated_minutes <= budget:
            keep.append(t)
            running += t.estimated_minutes
        else:
            defer.append(t)
    return fixed, keep, defer


def _capacity_suggestions(raw_input: str, info: dict) -> list[dict]:
    """
    Build a capacity-aware rewrite of the drafted plan that keeps every
    task but asks the planner to compress durations instead of dropping
    anything. Clicking it prefills the text box; the user still
    reviews, edits, and presses Generate themselves.

    (The other option — keep top priorities, defer the rest — is a
    direct one-click action instead of a text rewrite; see the
    "Keep top priorities, defer the rest" button in
    _render_ai_capacity_warning, which actually saves today's kept
    tasks AND creates the deferred ones on tomorrow's plan, rather than
    just asking the AI to quietly leave them out of a regenerated draft.)
    """
    recommended = int(info["recommended_minutes"])
    return [{
        "label": "🪶 Shorten instead of dropping",
        "text": (
            f"{raw_input.strip()}\n\n"
            f"(Note to planner: keep every item but compress the "
            f"durations so today's total stays close to {recommended} "
            f"minutes.)"
        ),
    }]


def _render_ai_capacity_warning(key_prefix: str) -> bool:
    """
    If a capacity warning is pending for this AI/Voice context, render
    it (message, evidence, suggestions, actions) and return True so the
    caller skips rendering its normal form this run. Returns False if
    there's nothing pending.
    """
    pending = _get_capacity_warning(key_prefix)
    if pending is None or pending.get("kind") != "ai":
        return False

    info = pending["capacity_info"]
    raw_input = pending["raw_input"]
    plan_output = pending["plan_output"]

    st.markdown("#### 🛡️ Realistic Capacity Check")
    st.warning(
        f"**Today's plan looks heavier than usual for you.** You'd be "
        f"planning about **{format_duration(info['planned_minutes'])}** "
        f"today, but your historical Realistic Capacity is closer to "
        f"**{format_duration(int(info['recommended_minutes']))}** — "
        f"about **{info['overload_fraction'] * 100:.0f}% over**.",
        icon="⚠️",
    )
    _capacity_evidence_caption(info)

    st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
    st.caption("Create it anyway, split it below, or cancel.")

    fixed, keep, defer = _capacity_split(plan_output, int(info["recommended_minutes"]))
    flexible = keep + defer  # same priority order, just re-joined

    if flexible:
        st.markdown("**✂️ Review the split before confirming**")
        st.caption(
            "The AI ranked these by priority — flip any that are wrong "
            "(e.g. something due today that got ranked low) before you "
            "confirm. Fixed-time tasks always stay today."
        )
        override_keys = []
        for i, t in enumerate(flexible):
            cb_key = f"{key_prefix}_keep_{i}"
            override_keys.append((cb_key, t))
            default_keep = t in keep
            row = st.columns([5, 2])
            with row[0]:
                st.checkbox(
                    f"{get_priority_icon(t.priority)} {t.title} · {format_duration(t.estimated_minutes)}",
                    value=default_keep,
                    key=cb_key,
                )
            with row[1]:
                st.caption("today" if st.session_state.get(cb_key, default_keep) else "→ tomorrow")

        today_flexible = [t for cb_key, t in override_keys if st.session_state.get(cb_key, t in keep)]
        tomorrow_flexible = [t for cb_key, t in override_keys if not st.session_state.get(cb_key, t in keep)]

        st.markdown("<div style='height:0.3rem;'></div>", unsafe_allow_html=True)
        if st.button(
            f"✅ Confirm: {len(fixed) + len(today_flexible)} today, "
            f"{len(tomorrow_flexible)} tomorrow",
            key=f"{key_prefix}_split_defer", use_container_width=True, type="primary",
        ):
            if plan is None:
                target_plan_id = create_today_plan(raw_input=raw_input, user_id=user_id)
                if target_plan_id is None:
                    st.error("Couldn't create today's plan. Try again.")
                    return True
            else:
                target_plan_id = plan["plan_id"]

            kept_count = save_draft_tasks_to_plan(fixed + today_flexible, target_plan_id)
            deferred_count = defer_draft_tasks_to_tomorrow(tomorrow_flexible, user_id=user_id)

            for k in pending.get("also_pop_keys", []):
                st.session_state.pop(k, None)
            for cb_key, _ in override_keys:
                st.session_state.pop(cb_key, None)
            _clear_capacity_warning(key_prefix)
            st.toast(
                f"Saved {kept_count} task(s) today, deferred {deferred_count} "
                f"to tomorrow's plan.",
                icon="✂️",
            )
            st.rerun()
        st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)

    suggestions = _capacity_suggestions(raw_input, info)
    sug_cols = st.columns(len(suggestions))
    for i, sug in enumerate(suggestions):
        with sug_cols[i]:
            if st.button(sug["label"], key=f"{key_prefix}_sugg_{i}", use_container_width=True):
                for text_key in pending["text_session_keys"]:
                    st.session_state[text_key] = sug["text"]
                _clear_capacity_warning(key_prefix)
                st.rerun()

    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button(
            "✅ Create It Anyway", key=f"{key_prefix}_ai_proceed",
            type="primary", use_container_width=True,
        ):
            result = save_planner_draft(
                pending["plan_output"], raw_input=raw_input, user_id=user_id,
            )
            st.session_state.last_planner_result = result
            for k in pending.get("also_pop_keys", []):
                st.session_state.pop(k, None)
            _clear_capacity_warning(key_prefix)
            st.toast(f"Plan created with {len(result['tasks'])} task(s)! 🎉", icon="✅")
            st.rerun()
    with action_cols[1]:
        if st.button("✖️ Cancel", key=f"{key_prefix}_ai_cancel", use_container_width=True):
            _clear_capacity_warning(key_prefix)
            st.rerun()

    return True


def _handle_ai_generation(
    raw_input: str,
    key_prefix: str,
    text_session_keys: list[str],
    also_pop_keys: list[str] | None = None,
) -> None:
    """
    Shared draft -> Realistic Capacity check -> (save | warn) flow, used
    by the AI Planner text tab and the Voice tab, in both the onboarding
    section and "Add More Tasks" below.

    Drafts via the AI Planner WITHOUT saving (draft_today_plan), checks
    the draft's total planned minutes against this user's capacity, and
    either saves it immediately (no issue found) or stores a pending
    warning + reruns so _render_ai_capacity_warning can take over.
    """
    with st.spinner("The AI Planner is splitting your day into tasks..."):
        try:
            draft = draft_today_plan(raw_input=raw_input, user_id=user_id)
        except ValueError as e:
            st.warning(f"**Nothing to plan yet.** {e}")
            return
        except RuntimeError as e:
            st.error(
                "**The AI Planner couldn't reach the model right now.** "
                "This usually means the Gemini API key is missing, invalid, "
                "or rate-limited."
            )
            if st.session_state.get("debug_mode"):
                st.exception(e)
            return
        except Exception as e:
            st.error("**Something went wrong while generating your plan.**")
            if st.session_state.get("debug_mode"):
                st.exception(e)
            return

    draft_minutes = draft_plan_total_minutes(draft)
    capacity_info = check_capacity_for_today(draft_minutes, user_id=user_id)

    if capacity_info["triggered"]:
        _set_capacity_warning(
            key_prefix,
            kind="ai",
            plan_output=draft,
            raw_input=raw_input,
            capacity_info=capacity_info,
            text_session_keys=text_session_keys,
            also_pop_keys=also_pop_keys or [],
        )
        st.rerun()
    else:
        result = save_planner_draft(draft, raw_input=raw_input, user_id=user_id)
        st.session_state.last_planner_result = result
        for k in (also_pop_keys or []):
            st.session_state.pop(k, None)
        st.toast(f"Plan created with {len(result['tasks'])} task(s)! 🎉", icon="✅")
        st.rerun()


def _render_manual_capacity_warning(key_prefix: str) -> bool:
    """
    Same idea as _render_ai_capacity_warning, for the Manual tab — but
    since there's no free text to rewrite here, capacity is recomputed
    live on every render instead of frozen at trigger time. That way,
    deferring or deleting one of today's existing lower-priority tasks
    (via the quick actions below) immediately reflects in the numbers,
    and once the day fits again the pending task is added automatically.
    """
    pending = _get_capacity_warning(key_prefix)
    if pending is None or pending.get("kind") != "manual":
        return False

    kwargs = pending["task_kwargs"]
    info = check_capacity_for_today(kwargs["estimated_minutes"], user_id=user_id)

    if not info["triggered"]:
        # Enough room was freed up (a task got deferred/deleted below) —
        # the original intent was to add this task, so just add it now.
        _commit_manual_task(pending)
        _clear_capacity_warning(key_prefix)
        st.rerun()

    st.markdown("#### 🛡️ Realistic Capacity Check")
    st.warning(
        f"**Adding '{kwargs['title']}' would push today over your usual "
        f"capacity.** Today would total about "
        f"**{format_duration(info['planned_minutes'])}**, versus your "
        f"historical Realistic Capacity of about "
        f"**{format_duration(int(info['recommended_minutes']))}** — "
        f"about **{info['overload_fraction'] * 100:.0f}% over**.",
        icon="⚠️",
    )
    _capacity_evidence_caption(info)

    st.markdown("<div style='height:0.3rem;'></div>", unsafe_allow_html=True)

    low_priority_today = [
        t for t in load_today_tasks(user_id=user_id)
        if t.get("status") == "pending" and int(t.get("priority", 3)) >= 4
    ]
    if low_priority_today:
        st.caption(
            "Free up room by deferring or dropping a lower-priority task "
            "already in today's plan:"
        )
        for t in low_priority_today:
            row = st.columns([3, 1, 1])
            with row[0]:
                st.markdown(
                    f"**{t.get('title', 'Untitled')}** · "
                    f"{PRIORITY_LABELS.get(int(t.get('priority', 3)), '')} · "
                    f"{format_duration(t.get('estimated_minutes'))}"
                )
            with row[1]:
                if st.button(
                    "📆 Defer", key=f"{key_prefix}_defer_{t['task_id']}",
                    use_container_width=True,
                ):
                    if defer_task_to_tomorrow(t["task_id"], user_id=user_id):
                        st.toast("Moved to tomorrow.", icon="📆")
                        st.rerun()
            with row[2]:
                if st.button(
                    "🗑️", key=f"{key_prefix}_dropit_{t['task_id']}",
                    use_container_width=True,
                ):
                    if delete_task(t["task_id"]):
                        st.toast("Removed.", icon="🗑️")
                        st.rerun()
        st.markdown("<div style='height:0.3rem;'></div>", unsafe_allow_html=True)
    else:
        st.caption("No lower-priority tasks in today's plan to defer.")

    st.caption("...or just add it anyway, or skip it for today.")
    cols = st.columns(2)
    with cols[0]:
        if st.button(
            "✅ Add It Anyway", key=f"{key_prefix}_manual_proceed",
            type="primary", use_container_width=True,
        ):
            _commit_manual_task(pending)
            _clear_capacity_warning(key_prefix)
            st.rerun()
    with cols[1]:
        if st.button("✖️ Skip / Cancel", key=f"{key_prefix}_manual_cancel", use_container_width=True):
            _clear_capacity_warning(key_prefix)
            st.rerun()

    return True


def _commit_manual_task(pending: dict) -> None:
    """Actually persist a manually-entered task, after any capacity gate has cleared."""
    kwargs = pending["task_kwargs"]
    if pending["mode"] == "create_plan":
        new_plan_id = create_today_plan(raw_input=kwargs["title"], user_id=user_id)
        if new_plan_id:
            new_id = add_task_to_plan(
                plan_id=new_plan_id,
                title=kwargs["title"],
                category_id=kwargs["category_id"],
                description=kwargs["description"],
                priority=kwargs["priority"],
                estimated_minutes=kwargs["estimated_minutes"],
                order_index=0,
            )
            if new_id:
                st.toast("Plan and first task created! 🎉", icon="✅")
            else:
                st.warning("Plan created, but the task couldn't be saved. Add it below.")
        else:
            st.error("Couldn't create a plan — one may already exist for today.")
    else:  # mode == "add_task"
        new_id = add_task_to_plan(**kwargs)
        if new_id:
            st.toast("Task added!", icon="➕")
        else:
            st.error("Couldn't add the task. Please try again.")


# ─────────────────────────────────────────────────────────────
# Shared Voice Tab (record -> transcribe -> review -> generate)
#
# Used by both the onboarding tabs and the "Add More Tasks" section
# below, so there's only one implementation to maintain. Runs the same
# draft -> capacity-check -> save flow as the AI Planner tab — the only
# extra step is turning audio into text via transcribe_voice_note().
# ─────────────────────────────────────────────────────────────

def _render_voice_tab(key_prefix: str) -> None:
    if _render_ai_capacity_warning(key_prefix):
        return

    st.markdown("#### 🎤 Say Your Day Out Loud")
    st.caption(
        "Record yourself describing your day in your own words, "
        "review the transcript below, then generate your plan."
    )

    transcript_key = f"{key_prefix}_transcript"
    audio_value = st.audio_input("Tap to record", key=f"{key_prefix}_audio")

    if audio_value is not None:
        if st.button(
            "📝 Transcribe", key=f"{key_prefix}_transcribe_btn", use_container_width=True
        ):
            with st.spinner("Transcribing your recording..."):
                try:
                    st.session_state[transcript_key] = transcribe_voice_note(
                        audio_value.getvalue(),
                        mime_type=getattr(audio_value, "type", None) or "audio/wav",
                    )
                except VoiceTranscriptionError as e:
                    st.error(f"**Couldn't transcribe that recording.** {e}")
                except Exception as e:
                    st.error("**Something went wrong while transcribing.**")
                    if st.session_state.get("debug_mode"):
                        st.exception(e)

    transcript = st.session_state.get(transcript_key)
    if transcript:
        edited_transcript = st.text_area(
            "Transcript (edit if anything came out wrong before generating)",
            value=transcript,
            height=90,
            key=f"{key_prefix}_transcript_edit",
        )
        if st.button(
            "✨ Generate My Plan",
            type="primary",
            use_container_width=True,
            key=f"{key_prefix}_voice_generate",
        ):
            if not edited_transcript.strip():
                st.error("Please make sure the transcript isn't empty.")
            else:
                _handle_ai_generation(
                    raw_input=edited_transcript,
                    key_prefix=key_prefix,
                    text_session_keys=[transcript_key, f"{key_prefix}_transcript_edit"],
                    also_pop_keys=[transcript_key, f"{key_prefix}_transcript_edit"],
                )


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

    tab_ai, tab_voice, tab_manual = st.tabs(["✨ AI Planner", "🎤 Voice", "✍️ Manual"])

    with tab_ai:
        if not _render_ai_capacity_warning("onboard_ai"):
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
                    _handle_ai_generation(
                        raw_input=ai_raw_input,
                        key_prefix="onboard_ai",
                        text_session_keys=["ai_raw_input"],
                    )

    with tab_voice:
        _render_voice_tab(key_prefix="onboard_voice")

    with tab_manual:
        if not _render_manual_capacity_warning("onboard_manual"):
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
                        task_kwargs = {
                            "title": m_title.strip(),
                            "category_id": m_cat_options[m_cat_choice],
                            "description": m_description.strip(),
                            "priority": m_priority,
                            "estimated_minutes": int(m_minutes),
                        }
                        capacity_info = check_capacity_for_today(
                            int(m_minutes), user_id=user_id,
                        )
                        if capacity_info["triggered"]:
                            _set_capacity_warning(
                                "onboard_manual",
                                kind="manual",
                                mode="create_plan",
                                task_kwargs=task_kwargs,
                                capacity_info=capacity_info,
                            )
                        else:
                            _commit_manual_task({
                                "mode": "create_plan", "task_kwargs": task_kwargs,
                            })
                        st.rerun()

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

# Surface any break-vs-fixed-task conflict already saved in the database,
# even if nobody has run the scheduler yet *this session* — otherwise a
# conflict left over from an earlier visit (or created some other way)
# renders silently, like Math/Break both sitting at 2:00 PM with no
# warning. Button actions below (_run_and_track_conflicts) refresh this
# same key after a live scheduler run, so it always reflects the latest
# state either way.
CONFLICTS_KEY = "_scheduling_conflicts"
st.session_state[CONFLICTS_KEY] = get_persisted_scheduling_conflicts(user_id=user_id)

# Same idea, but for two ordinary fixed-time tasks overlapping each
# other (e.g. two imported calendar events both pinned to 10:00-11:00)
# instead of a break vs. a fixed task.
FIXED_CONFLICTS_KEY = "_fixed_task_conflicts"
st.session_state[FIXED_CONFLICTS_KEY] = get_persisted_fixed_task_conflicts(user_id=user_id)


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
        "The Scheduler assigns start/end times to your tasks, routing "
        "around any breaks below and filling small gaps with a shorter "
        "task when one fits. It never resizes a task, and the list "
        "above always stays in your original order."
    )
    work_start = st.time_input("Work day starts at", value=time(9, 0))

    st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
    st.markdown("**☕ Breaks**")
    st.caption(
        "Breaks are saved with your plan — they'll still be here if you "
        "leave and come back, and they show up in your Tasks list below "
        "with their own Start button and countdown."
    )

    existing_breaks = sorted(
        (t for t in tasks if t.get("is_break")),
        key=lambda t: t.get("scheduled_start") or "",
    )
    if existing_breaks:
        for b in existing_breaks:
            b_cols = st.columns([3, 1])
            with b_cols[0]:
                st.caption(
                    f"☕ {format_time_12h(b.get('scheduled_start'))} – "
                    f"{format_time_12h(b.get('scheduled_end'))} "
                    f"({format_duration(b.get('estimated_minutes'))}) · {b.get('status', 'pending')}"
                )
            with b_cols[1]:
                if st.button("🗑️", key=f"break_cfg_del_{b['task_id']}", use_container_width=True):
                    if delete_task(b["task_id"]):
                        st.rerun()
    else:
        st.caption("No breaks added yet — the scheduler will run without one.")

    # ── Conflict state (survives st.rerun, unlike a plain st.warning) ──
    # CONFLICTS_KEY itself is set once above (right after tasks load) from
    # what's actually persisted, so a conflict from an earlier session is
    # never hidden. The helpers below just refresh it after a live
    # scheduler run or a resolution action, so it stays in sync.

    # A high-priority flexible task getting pushed back by a new break is
    # NOT a conflict (the scheduler resolves it fine on its own — see
    # _best_gap_filler) but it's still worth flagging, since it can
    # silently shove something important to later in the day. Unlike
    # CONFLICTS_KEY this is intentionally session-only and NOT reseeded
    # from the database on every load: it only makes sense right after
    # the specific "Add" click that caused it, since Undo needs the
    # "before" snapshot taken at that same moment.
    DELAY_WARNING_THRESHOLD_MINUTES = 60
    DELAY_WARNING_KEY = "_break_delay_warning"
    LAST_BREAK_ADDED_KEY = "_last_break_added_id"
    st.session_state.setdefault(DELAY_WARNING_KEY, [])

    def _shift_time(t: time, delta_minutes: int) -> time:
        return (datetime.combine(date.today(), t) + timedelta(minutes=delta_minutes)).time()

    def _run_and_track_conflicts() -> list:
        with st.spinner("Assigning time slots..."):
            scheduled = run_scheduler_for_today(work_day_start=work_start, user_id=user_id)
        st.session_state[CONFLICTS_KEY] = get_last_scheduling_conflicts() if scheduled else []
        st.session_state[FIXED_CONFLICTS_KEY] = get_last_fixed_task_conflicts() if scheduled else []
        return scheduled

    def _find_break_task(break_start_t: time, duration_minutes: int) -> dict | None:
        target = break_start_t.strftime("%H:%M")
        for b in tasks:
            if (
                b.get("is_break")
                and str(b.get("scheduled_start"))[:5] == target
                and int(b.get("estimated_minutes") or 0) == duration_minutes
            ):
                return b
        return None

    def _find_fixed_task(title: str, start_t: time, duration_minutes: int) -> dict | None:
        """Match a FixedTaskConflict side back to its DB row — same
        idea as _find_break_task, but also keyed on title since two
        *different* fixed tasks can share the same start time (that's
        exactly the conflict being resolved)."""
        target = start_t.strftime("%H:%M")
        for t in tasks:
            if (
                t.get("is_fixed_time")
                and not t.get("is_break")
                and t.get("title") == title
                and str(t.get("scheduled_start"))[:5] == target
                and int(t.get("estimated_minutes") or 0) == duration_minutes
            ):
                return t
        return None

    def _find_high_priority_delays(
        before: dict[int, Optional[str]],
        after: list[dict],
        threshold_minutes: int = DELAY_WARNING_THRESHOLD_MINUTES,
    ) -> list[dict]:
        """
        Compare each high-priority (priority 1–2), non-break, non-fixed
        task's scheduled_start before vs. after a schedule change and
        return the ones pushed back by more than threshold_minutes.
        Only flags a *later* start (a task moving earlier is never a
        problem worth a warning).
        """

        def _to_minutes(hhmm) -> Optional[int]:
            if not hhmm:
                return None
            try:
                h, m = str(hhmm)[:5].split(":")
                return int(h) * 60 + int(m)
            except (ValueError, IndexError):
                return None

        delays = []
        for t in after:
            if t.get("is_break") or t.get("is_fixed_time"):
                continue
            if int(t.get("priority") or 5) > 2:
                continue
            old_minutes = _to_minutes(before.get(t["task_id"]))
            new_minutes = _to_minutes(t.get("scheduled_start"))
            if old_minutes is None or new_minutes is None:
                continue
            delta = new_minutes - old_minutes
            if delta >= threshold_minutes:
                delays.append({
                    "title": t.get("title"),
                    "old_start": before[t["task_id"]],
                    "new_start": t.get("scheduled_start"),
                    "delta_minutes": delta,
                })
        return delays

    add_cols = st.columns([2, 2, 1])
    with add_cols[0]:
        new_break_start = st.time_input("Starts at", value=time(12, 0), key="new_break_start")
    with add_cols[1]:
        new_break_minutes = st.number_input(
            "Duration (min)", min_value=5, max_value=180, value=30, step=5, key="new_break_minutes"
        )
    with add_cols[2]:
        st.markdown("<div style='height:1.8rem;'></div>", unsafe_allow_html=True)
        if st.button("➕ Add", key="add_break_btn", use_container_width=True):
            # Snapshot pre-add start times so we can (a) detect a
            # high-priority delay caused by THIS break, and (b) restore
            # the exact prior schedule if the user hits Undo.
            before_starts = {t["task_id"]: t.get("scheduled_start") for t in tasks}
            new_break_id = add_break_to_plan(plan["plan_id"], new_break_start, int(new_break_minutes))
            if new_break_id:
                _run_and_track_conflicts()
                if st.session_state.get(CONFLICTS_KEY):
                    st.session_state[DELAY_WARNING_KEY] = []
                    st.toast("Break added — but it overlaps a fixed task, see below.", icon="⚠️")
                else:
                    refreshed_tasks = load_today_tasks(user_id=user_id)
                    delays = _find_high_priority_delays(before_starts, refreshed_tasks)
                    st.session_state[DELAY_WARNING_KEY] = delays
                    st.session_state[LAST_BREAK_ADDED_KEY] = new_break_id
                    if delays:
                        st.toast("Break added — but it pushed back a high-priority task.", icon="⚠️")
                    else:
                        st.toast("Break added and slotted into your schedule ☕", icon="✅")
                st.rerun()
            else:
                st.error("Couldn't add the break. Try again.")

    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)

    if st.button("▶️ Run Scheduler", type="primary"):
        scheduled = _run_and_track_conflicts()
        if scheduled:
            if not st.session_state.get(CONFLICTS_KEY):
                st.toast("Schedule updated!", icon="✅")
            st.rerun()
        else:
            st.session_state[CONFLICTS_KEY] = []
            error_detail = get_last_scheduler_error()
            if error_detail:
                st.error(f"Scheduling failed: {error_detail}")
            else:
                st.error("Scheduling failed. Make sure today's plan has at least one task.")

    # ── Persistent conflict banner + resolution actions ──
    # Shown on every render until resolved (unlike st.warning above the
    # old st.rerun(), which vanished after ~1 frame).
    for idx, c in enumerate(st.session_state.get(CONFLICTS_KEY) or []):
        duration = int(
            (datetime.combine(date.today(), c.break_end)
             - datetime.combine(date.today(), c.break_start)).total_seconds() // 60
        )
        st.warning(
            f"⚠️ Couldn't fit your break "
            f"({format_time_12h(c.break_start.strftime('%H:%M'))} – "
            f"{format_time_12h(c.break_end.strftime('%H:%M'))}): "
            f"it overlaps your fixed task **'{c.fixed_task_title}'** "
            f"({format_time_12h(c.fixed_task_start.strftime('%H:%M'))} – "
            f"{format_time_12h(c.fixed_task_end.strftime('%H:%M'))}). "
            f"Both were left at their original times."
        )
        res_cols = st.columns(3)
        with res_cols[0]:
            if st.button("⬅️ Move break before task", key=f"conflict_before_{idx}", use_container_width=True):
                row = _find_break_task(c.break_start, duration)
                if row is not None:
                    new_time = _shift_time(c.fixed_task_start, -duration)
                    if reschedule_break(row["task_id"], new_time):
                        _run_and_track_conflicts()
                        st.rerun()
        with res_cols[1]:
            if st.button("➡️ Move break after task", key=f"conflict_after_{idx}", use_container_width=True):
                row = _find_break_task(c.break_start, duration)
                if row is not None:
                    new_time = c.fixed_task_end
                    if reschedule_break(row["task_id"], new_time):
                        _run_and_track_conflicts()
                        st.rerun()
        with res_cols[2]:
            if st.button("🗑️ Remove this break", key=f"conflict_remove_{idx}", use_container_width=True):
                row = _find_break_task(c.break_start, duration)
                if row is not None and delete_task(row["task_id"]):
                    _run_and_track_conflicts()
                    st.rerun()

    # ── Persistent fixed-task-vs-fixed-task conflict banner ──
    # Two ordinary (non-break) fixed-time tasks overlapping each other
    # — e.g. two imported calendar events both pinned to 10:00-11:00.
    # Same "neither side can move" story as the break banner above,
    # resolved with a move-either-direction / remove-either choice —
    # unlike the break banner, there's no "the break" side to anchor a
    # before/after move against, so BOTH directions need to be offered
    # (move A after B, or move B after A); either task could reasonably
    # be the one that moves.
    for f_idx, fc in enumerate(st.session_state.get(FIXED_CONFLICTS_KEY) or []):
        a_duration = int(
            (datetime.combine(date.today(), fc.task_a_end)
             - datetime.combine(date.today(), fc.task_a_start)).total_seconds() // 60
        )
        b_duration = int(
            (datetime.combine(date.today(), fc.task_b_end)
             - datetime.combine(date.today(), fc.task_b_start)).total_seconds() // 60
        )
        st.warning(
            f"⚠️ **'{fc.task_a_title}'** "
            f"({format_time_12h(fc.task_a_start.strftime('%H:%M'))} – "
            f"{format_time_12h(fc.task_a_end.strftime('%H:%M'))}) overlaps "
            f"**'{fc.task_b_title}'** "
            f"({format_time_12h(fc.task_b_start.strftime('%H:%M'))} – "
            f"{format_time_12h(fc.task_b_end.strftime('%H:%M'))}). "
            f"Both were left at their original times — neither is a break, "
            f"so the scheduler can't tell which one should move."
        )
        fx_move_cols = st.columns(2)
        with fx_move_cols[0]:
            if st.button(
                f"➡️ Move '{fc.task_a_title}' after '{fc.task_b_title}'",
                key=f"fixed_conflict_move_a_{f_idx}", use_container_width=True,
            ):
                row = _find_fixed_task(fc.task_a_title, fc.task_a_start, a_duration)
                if row is not None and reschedule_fixed_task(row["task_id"], fc.task_b_end):
                    _run_and_track_conflicts()
                    st.rerun()
        with fx_move_cols[1]:
            if st.button(
                f"➡️ Move '{fc.task_b_title}' after '{fc.task_a_title}'",
                key=f"fixed_conflict_move_b_{f_idx}", use_container_width=True,
            ):
                row = _find_fixed_task(fc.task_b_title, fc.task_b_start, b_duration)
                if row is not None and reschedule_fixed_task(row["task_id"], fc.task_a_end):
                    _run_and_track_conflicts()
                    st.rerun()
        fx_remove_cols = st.columns(2)
        with fx_remove_cols[0]:
            if st.button(
                f"🗑️ Remove '{fc.task_a_title}'",
                key=f"fixed_conflict_remove_a_{f_idx}", use_container_width=True,
            ):
                row = _find_fixed_task(fc.task_a_title, fc.task_a_start, a_duration)
                if row is not None and delete_task(row["task_id"]):
                    _run_and_track_conflicts()
                    st.rerun()
        with fx_remove_cols[1]:
            if st.button(
                f"🗑️ Remove '{fc.task_b_title}'",
                key=f"fixed_conflict_remove_b_{f_idx}", use_container_width=True,
            ):
                row = _find_fixed_task(fc.task_b_title, fc.task_b_start, b_duration)
                if row is not None and delete_task(row["task_id"]):
                    _run_and_track_conflicts()
                    st.rerun()

    # ── Delay warning: break pushed back a high-priority task ──
    # Not a conflict (the scheduler placed everything fine on its own),
    # just a heads-up + a one-click way back to how it was.
    delay_warnings = st.session_state.get(DELAY_WARNING_KEY) or []
    if delay_warnings:
        delay_lines = "; ".join(
            f"**'{d['title']}'** {format_time_12h(d['old_start'])} → {format_time_12h(d['new_start'])}"
            for d in delay_warnings
        )
        st.warning(f"⚠️ This break pushed back a high-priority task: {delay_lines}.")
        warn_cols = st.columns([2, 1])
        with warn_cols[0]:
            if st.button("↩️ Undo — remove this break", key="undo_break_delay", use_container_width=True):
                last_break_id = st.session_state.get(LAST_BREAK_ADDED_KEY)
                if last_break_id and delete_task(last_break_id):
                    st.session_state[DELAY_WARNING_KEY] = []
                    st.session_state[LAST_BREAK_ADDED_KEY] = None
                    _run_and_track_conflicts()
                    st.toast("Break removed — schedule restored.", icon="↩️")
                    st.rerun()
                else:
                    st.error("Couldn't undo — the break may have already been changed.")
        with warn_cols[1]:
            if st.button("Keep it", key="dismiss_break_delay", use_container_width=True):
                st.session_state[DELAY_WARNING_KEY] = []
                st.rerun()

    # ── Export to Google Calendar ──
    if is_google_calendar_connected(user_id) and is_scheduled:
        st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
        st.markdown("**📤 Export to Google Calendar**")
        st.caption(
            "Create events in your Primary Google Calendar for each "
            "scheduled task."
        )
        stale_count = get_stale_export_count(user_id)
        if stale_count:
            st.warning(
                f"⚠️ {stale_count} task(s) changed since your last export — "
                "Google Calendar still shows the old time. Export again to "
                "update it.",
                icon="⚠️",
            )
        if st.button(
            "📤 Export Scheduled Tasks",
            use_container_width=True,
        ):
            with st.spinner("Exporting..."):
                export_result = export_all_scheduled_tasks(user_id)
            if export_result.get("errors"):
                st.warning(
                    f"Exported {export_result.get('exported', 0)} task(s) "
                    f"with {export_result['errors']} error(s).",
                    icon="⚠️",
                )
            else:
                st.toast(
                    f"Exported {export_result.get('exported', 0)} task(s) to Google Calendar!",
                    icon="📤",
                )


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

        # ── Google Calendar Events ──
        if is_google_calendar_connected(user_id):
            _gcal_events = get_google_calendar_events_today(user_id)
            if _gcal_events:
                _selected_cals = get_selected_calendars(user_id)
                _cal_colors = {c["calendar_id"]: c.get("color", "#4285F4") for c in _selected_cals}
                _cal_names = {c["calendar_id"]: c.get("calendar_name", "") for c in _selected_cals}
                # Events already turned into a task (via Import as Task,
                # or previously exported the other way) — matched by
                # google_event_id, which every real task row carries once
                # it's linked to a calendar event either direction.
                _imported_event_ids = {
                    t.get("google_event_id") for t in tasks if t.get("google_event_id")
                }

                with st.expander(f"📅 Google Calendar ({len(_gcal_events)} event{'s' if len(_gcal_events) != 1 else ''})", expanded=False):
                    for _ev_idx, ev in enumerate(_gcal_events):
                        _ev_color = _cal_colors.get(ev.get("calendar_id"), "#4285F4")
                        _ev_cal_name = _cal_names.get(ev.get("calendar_id"), "")
                        _ev_row_cols = st.columns([5, 1.3])
                        with _ev_row_cols[0]:
                            st.markdown(
                                f"<div style='display:flex; align-items:center; gap:10px; "
                                f"padding:6px 10px; margin-bottom:4px; "
                                f"border-left:3px solid {_ev_color}; "
                                f"background:var(--bg-card); border-radius:4px;'>"
                                f"<span style='font-weight:600; color:var(--text-primary);'>"
                                f"{ev.get('start_time', '?')} – {ev.get('end_time', '?')}</span>"
                                f"<span style='color:var(--text-secondary);'>{ev.get('title', '(No title)')}</span>"
                                f"<span style='font-size:0.75rem; color:var(--text-muted); margin-left:auto;'>{_ev_cal_name}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        with _ev_row_cols[1]:
                            if ev.get("google_event_id") in _imported_event_ids:
                                st.caption("✅ Imported")
                            else:
                                if st.button(
                                    "➕ Import as Task",
                                    key=f"import_gcal_event_{_ev_idx}",
                                    use_container_width=True,
                                ):
                                    new_task_id = import_calendar_event_as_task(plan["plan_id"], ev)
                                    if new_task_id:
                                        st.toast(f"Imported '{ev.get('title')}' as a task.", icon="📥")
                                        st.rerun()
                                    else:
                                        st.error("Couldn't import this event as a task.")

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
# Focus Pattern Matrix (Category x Time-of-day pause counts)
#
# Standalone data source (load_pause_matrix in services.py) meant to
# be reusable by other analytics/AI Coach prompts too — this is just
# the simplest possible surface for it here on Today's Schedule.
#
# A (category, time-of-day) bucket built from only 1-2 tasks isn't a
# "pattern" yet — it's a single data point dressed up as one, and
# showing it alongside a bucket with real signal buries the one row
# that actually matters in noise. Mirrors how RescueTime/Toggl-style
# tools handle this in practice: wait for enough of a baseline before
# surfacing an insight, rather than showing a low-confidence one.
# ─────────────────────────────────────────────────────────────

FOCUS_PATTERN_MIN_TASKS = 3

_pause_matrix = load_pause_matrix(user_id=user_id)
if _pause_matrix:
    _bucket_order = ["Morning", "Afternoon", "Evening", "Night", "Unknown"]
    _sorted_matrix = sorted(
        [r for r in _pause_matrix if r["task_count"] >= FOCUS_PATTERN_MIN_TASKS],
        key=lambda r: (
            r["category"],
            _bucket_order.index(r["time_bucket"]) if r["time_bucket"] in _bucket_order else 99,
        ),
    )
    with st.expander("📊 Focus Pattern Matrix (category × time of day)", expanded=False):
        if _sorted_matrix:
            st.caption(
                "How many times you've paused tasks, broken down by category "
                "and when you started them. A category with a high pause "
                "count at a specific time of day is a pattern worth acting on "
                "— e.g. move it to a time slot where it rarely gets paused."
            )
            _mx_cols = st.columns([2, 2, 1, 1, 1, 1])
            for col, label in zip(_mx_cols, ["Category", "Time of day", "Tasks", "Paused", "Avg/task", "Avg pause"]):
                col.markdown(f"**{label}**")
            for row in _sorted_matrix:
                _r_cols = st.columns([2, 2, 1, 1, 1, 1])
                _r_cols[0].write(row["category"])
                _r_cols[1].write(row["time_bucket"])
                _r_cols[2].write(row["task_count"])
                highlight = "⚠️ " if row["avg_pauses_per_task"] >= 1.5 else ""
                _r_cols[3].write(f"{highlight}{row['total_pauses']}")
                _r_cols[4].write(f"{row['avg_pauses_per_task']:.1f}")
                _r_cols[5].write(format_duration(round(row["avg_pause_duration_seconds"] / 60)))
        else:
            st.caption(
                f"Not enough data yet — patterns need at least "
                f"{FOCUS_PATTERN_MIN_TASKS} tasks started in the same "
                f"category and time of day before they mean anything. "
                f"Keep using the task timer and this will fill in."
            )

st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Task Cards (expandable, with status actions)
# ─────────────────────────────────────────────────────────────

st.markdown("#### 📋 Tasks")

# Display in chronological order — scheduled tasks by their start time
# first, then anything not yet scheduled (no scheduled_start) at the end
# in its existing order_index order, instead of raw insertion order.
tasks_display = sorted(
    tasks,
    key=lambda t: (t.get("scheduled_start") is None, t.get("scheduled_start") or ""),
)

for task in tasks_display:
    if task.get("is_break"):
        render_break_card(task)
        continue

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
        top = st.columns([3, 1, 1])
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
            if status == "pending":
                if st.button("📆", key=f"defer_{task['task_id']}", use_container_width=True, help="Defer to tomorrow"):
                    if defer_task_to_tomorrow(task["task_id"], user_id=user_id):
                        st.toast("Moved to tomorrow.", icon="📆")
                        st.rerun()
        with top[2]:
            if st.button("🗑️ Delete", key=f"del_{task['task_id']}", use_container_width=True):
                if delete_task(task["task_id"]):
                    st.toast("Task deleted.", icon="🗑️")
                    st.rerun()

        if status in ("pending", "in_progress"):
            paused = is_task_timer_paused(task)
            if status == "in_progress" and not paused:
                duration_minutes = int(task.get("estimated_minutes") or 0)
                remaining_seconds = duration_minutes * 60 - get_task_elapsed_seconds(task)
                render_task_countdown(
                    task["task_id"], remaining_seconds, duration_minutes * 60,
                    key_suffix=str(task["task_id"]),
                )
            elif status == "in_progress" and paused:
                banked_minutes = get_task_elapsed_seconds(task) / 60
                paused_minutes = get_task_paused_seconds(task) / 60
                st.caption(
                    f"⏸️ Paused · {banked_minutes:.1f} of {format_duration(task.get('estimated_minutes'))} "
                    f"elapsed · paused for {paused_minutes:.1f}m so far"
                )

            if int(task.get("pause_count") or 0) > 0:
                st.caption(f"🔁 Paused {int(task['pause_count'])}x so far")

            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("✅ Mark Completed", key=f"complete_{task['task_id']}", use_container_width=True):
                    if finish_task_with_timer(task["task_id"], status="completed"):
                        st.toast("Nice work! Task completed.", icon="🎉")
                        st.rerun()
            with action_cols[1]:
                if status == "pending":
                    if st.button("▶️ Start Task", key=f"start_{task['task_id']}", use_container_width=True):
                        if start_task_timer(task["task_id"]):
                            st.toast("Timer started ⏱️", icon="▶️")
                            st.rerun()
                elif not paused:
                    if st.button("⏸️ Pause", key=f"pause_{task['task_id']}", use_container_width=True):
                        if pause_task_timer(task["task_id"]):
                            st.toast("Timer paused ⏸️", icon="⏸️")
                            st.rerun()
                else:
                    if st.button("▶️ Resume", key=f"resume_{task['task_id']}", use_container_width=True):
                        if resume_task_timer(task["task_id"]):
                            st.toast("Timer resumed ▶️", icon="▶️")
                            st.rerun()
            with action_cols[2]:
                with st.popover("❌ Mark Failed", use_container_width=True):
                    reason = st.selectbox(
                        "Why?", FAILURE_REASONS, key=f"reason_{task['task_id']}"
                    )
                    if st.button("Confirm", key=f"fail_{task['task_id']}"):
                        if finish_task_with_timer(task["task_id"], status="failed", failure_reason=reason):
                            st.toast("Logged — your coach will factor this in.", icon="📝")
                            st.rerun()
        else:
            st.caption(f"Marked {status} · {format_duration(task.get('actual_minutes'))} actual")


# ─────────────────────────────────────────────────────────────
# Add More Tasks (AI Planner / Manual — same choice as onboarding)
# ─────────────────────────────────────────────────────────────

st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
with st.expander("➕ Add Tasks to Today's Plan"):
    add_tab_ai, add_tab_voice, add_tab_manual = st.tabs(["✨ AI Planner", "🎤 Voice", "✍️ Manual"])

    with add_tab_ai:
        if not _render_ai_capacity_warning("more_ai"):
            with st.form("ai_add_tasks_form", clear_on_submit=False):
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
                    _handle_ai_generation(
                        raw_input=more_raw_input,
                        key_prefix="more_ai",
                        text_session_keys=["more_raw_input"],
                        also_pop_keys=["more_raw_input"],
                    )

    with add_tab_voice:
        _render_voice_tab(key_prefix="more_voice")

    with add_tab_manual:
        if not _render_manual_capacity_warning("more_manual"):
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
                        task_kwargs = {
                            "plan_id": plan["plan_id"],
                            "title": title.strip(),
                            "category_id": cat_options[cat_choice],
                            "description": description.strip(),
                            "priority": priority,
                            "estimated_minutes": int(minutes),
                            "order_index": len(tasks),
                        }
                        capacity_info = check_capacity_for_today(
                            int(minutes), user_id=user_id,
                        )
                        if capacity_info["triggered"]:
                            _set_capacity_warning(
                                "more_manual",
                                kind="manual",
                                mode="add_task",
                                task_kwargs=task_kwargs,
                                capacity_info=capacity_info,
                            )
                        else:
                            _commit_manual_task({
                                "mode": "add_task", "task_kwargs": task_kwargs,
                            })
                        st.rerun()