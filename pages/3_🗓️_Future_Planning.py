"""Progressive, preview-first UI for CoachAI's single Future Planner."""

from datetime import datetime, time, timedelta
import uuid

import streamlit as st

from config import MAX_FUTURE_PLANNING_WEEKS, WORK_DAY_END
from layout import page_setup, page_title
from planning_request import (
    PlanningClarificationRequired,
    PlanningInputMethod,
    PlanningScope,
    PlanningTaskRequest,
    StructuredPlanningRequest,
)
from services import (
    apply_future_plan,
    get_current_user_id,
    get_database,
    load_future_planning_candidates,
    preview_structured_future_plan,
    reconcile_schedule_calendar_operation,
    transcribe_voice_note,
    understand_future_planning_text,
    undo_smart_schedule,
)
from timezone_utils import user_today


SCOPE_KEY = "future_planning_scope"
METHOD_KEY = "future_planning_method"
REQUEST_KEY = "future_planning_request"
PREVIEW_KEY = "future_plan_preview"
OPERATION_KEY = "future_plan_operation_id"


def _clear_manual_widget_state() -> None:
    prefixes = (
        "future_draft_", "future_remaining_", "future_priority_",
        "future_use_earliest_", "future_earliest_", "future_use_deadline_",
        "future_deadline_",
    )
    st.session_state.pop("future_manual_task_ids", None)
    st.session_state.pop("future_manual_draft_ids", None)
    for key in list(st.session_state):
        if any(str(key).startswith(prefix) for prefix in prefixes):
            st.session_state.pop(key, None)


def _clear_after_scope() -> None:
    for key in (
        METHOD_KEY, REQUEST_KEY, PREVIEW_KEY, "future_voice_text",
        "future_written_request",
    ):
        st.session_state.pop(key, None)
    _clear_manual_widget_state()


def _clear_after_method() -> None:
    for key in (REQUEST_KEY, PREVIEW_KEY, "future_voice_text", "future_written_request"):
        st.session_state.pop(key, None)
    _clear_manual_widget_state()


def _choose_scope(value: PlanningScope) -> None:
    st.session_state[SCOPE_KEY] = value.value
    _clear_after_scope()


def _choose_method(value: PlanningInputMethod) -> None:
    st.session_state[METHOD_KEY] = value.value
    _clear_after_method()


def _render_timing_constraints(
    *,
    key_prefix: str,
    identity: str | int,
    scope: PlanningScope,
    planning_start,
    planning_end,
    work_day_start,
    existing_earliest: datetime | None = None,
    existing_deadline: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Render optional per-task timing without duplicating the planning scope."""
    use_earliest = st.checkbox(
        "Set earliest start",
        value=existing_earliest is not None,
        key=f"{key_prefix}use_earliest_{identity}",
    )
    earliest = None
    if use_earliest:
        if scope == PlanningScope.SPECIFIC_DAY:
            earliest_time = st.time_input(
                "Start no earlier than",
                value=(existing_earliest.time() if existing_earliest else None),
                key=f"{key_prefix}earliest_time_{identity}",
            )
            if earliest_time is not None:
                if (
                    existing_earliest is not None
                    and existing_earliest.date() != planning_start
                    and earliest_time == existing_earliest.time()
                ):
                    earliest = existing_earliest
                    st.caption(f"Stored earliest start: {existing_earliest:%b %d, %Y at %H:%M}")
                else:
                    earliest = datetime.combine(planning_start, earliest_time)
        else:
            e_date, e_time = st.columns(2)
            earliest_date = e_date.date_input(
                "Earliest date",
                value=(existing_earliest.date() if existing_earliest else planning_start),
                key=f"{key_prefix}earliest_date_{identity}",
            )
            earliest_time = e_time.time_input(
                "Earliest time",
                value=(existing_earliest.time() if existing_earliest else work_day_start),
                key=f"{key_prefix}earliest_time_{identity}",
            )
            earliest = datetime.combine(earliest_date, earliest_time)

    use_deadline = st.checkbox(
        "Add task-specific deadline",
        value=existing_deadline is not None,
        key=f"{key_prefix}use_deadline_{identity}",
        help=(
            "Optional. Without this, the task may be scheduled through "
            f"Planning End ({planning_end:%b %d, %Y})."
        ),
    )
    deadline = None
    if use_deadline:
        if scope == PlanningScope.SPECIFIC_DAY:
            deadline_time = st.time_input(
                "Finish by",
                value=(existing_deadline.time() if existing_deadline else None),
                key=f"{key_prefix}deadline_time_{identity}",
            )
            if deadline_time is not None:
                if (
                    existing_deadline is not None
                    and existing_deadline.date() != planning_start
                    and deadline_time == existing_deadline.time()
                ):
                    deadline = existing_deadline
                    st.caption(f"Stored deadline: {existing_deadline:%b %d, %Y at %H:%M}")
                else:
                    deadline = datetime.combine(planning_start, deadline_time)
        else:
            d_date, d_time = st.columns(2)
            deadline_date = d_date.date_input(
                "Deadline date",
                value=(existing_deadline.date() if existing_deadline else None),
                key=f"{key_prefix}deadline_date_{identity}",
            )
            deadline_time = d_time.time_input(
                "Deadline time",
                value=(existing_deadline.time() if existing_deadline else None),
                key=f"{key_prefix}deadline_time_{identity}",
            )
            if deadline_date is not None and deadline_time is not None:
                deadline = datetime.combine(deadline_date, deadline_time)

    return earliest, deadline


def _show_request(request: StructuredPlanningRequest) -> None:
    st.subheader("Request to preview")
    scope_label = (
        request.planning_start.strftime("%B %d, %Y")
        if request.scope == PlanningScope.SPECIFIC_DAY
        else f"{request.planning_start:%b %d, %Y} – {request.planning_end:%b %d, %Y}"
    )
    st.caption(f"{scope_label} · {request.input_method.value.title()} · work day from {request.work_day_start:%H:%M}")
    for task in request.tasks:
        details = ["NEW" if task.is_draft else "EXISTING"]
        if task.remaining_minutes is not None:
            details.append(f"{task.remaining_minutes} minutes remaining")
            if task.duration_source == "estimated":
                details.append("duration estimated by AI")
            elif task.duration_source == "user_explicit":
                details.append("duration provided by you")
        if task.priority is not None:
            details.append(f"priority {task.priority}")
        if task.earliest_start is not None:
            details.append(f"earliest {task.earliest_start:%b %d %H:%M}")
        if task.deadline is not None:
            details.append(f"deadline {task.deadline:%b %d %H:%M}")
        st.markdown(f"**{task.title}**" + (f" — {', '.join(details)}" if details else ""))


def _preview_request(request: StructuredPlanningRequest, user_id: str) -> None:
    try:
        st.session_state[PREVIEW_KEY] = preview_structured_future_plan(request, user_id)
        st.session_state[REQUEST_KEY] = request
    except Exception as exc:
        st.error(f"Nothing was changed: {exc}")


def _render_preview(user_id: str) -> None:
    preview = st.session_state.get(PREVIEW_KEY)
    if preview is None:
        return
    st.divider()
    st.subheader("Preview")
    if not preview.outcomes:
        st.info("No tasks need planning in this scope.")
        return
    for outcome in preview.outcomes:
        identity = "NEW" if outcome.is_draft else "EXISTING"
        if outcome.placed:
            windows = ", ".join(
                f"{segment.scheduled_date:%b %d} {segment.scheduled_start:%H:%M}–{segment.scheduled_end:%H:%M}"
                for segment in outcome.segments
            ) or "No remaining work"
            label = "Kept" if outcome.reason == "already_valid" else "Planned"
            st.success(f"{identity} · {label}: {outcome.title} — {windows}")
        else:
            st.warning(f"{identity} · {outcome.message}")
    if preview.changes:
        st.subheader("Proposed changes")
        for change in preview.changes:
            old = ", ".join(
                f"{s.scheduled_date:%b %d} {s.scheduled_start:%H:%M}–{s.scheduled_end:%H:%M}"
                for s in change.old_segments
            ) or "Unscheduled"
            new = ", ".join(
                f"{s.scheduled_date:%b %d} {s.scheduled_start:%H:%M}–{s.scheduled_end:%H:%M}"
                for s in change.new_segments
            ) or "Unscheduled"
            st.markdown(f"**{change.title}**  \n{old} → {new}  \n{change.explanation}")
    elif not preview.task_updates and not preview.draft_tasks:
        st.info("Selected tasks already have valid, stable placements. Nothing needs saving.")

    can_apply = bool(preview.segments_by_task or preview.draft_tasks) and bool(
        preview.changes or preview.task_updates or preview.draft_tasks
    )
    if can_apply and st.button("Apply this preview", type="primary", use_container_width=True):
        try:
            operation_id = apply_future_plan(preview, user_id)
            st.session_state[OPERATION_KEY] = operation_id
            reconciliation = reconcile_schedule_calendar_operation(operation_id, user_id)
            if reconciliation.get("errors"):
                st.warning("The plan was saved, but Calendar reconciliation is pending retry.")
            else:
                st.success("Future plan saved as one atomic operation.")
            _clear_after_method()
            st.rerun()
        except Exception as exc:
            st.error(f"Nothing was saved: {exc}")


page_setup(title="Future Planning", icon="🗓️")
page_title("🗓️", "Future Planning", "Choose the work first, then preview exact placements before saving.")

user_id = get_current_user_id()
today = user_today(get_database(), user_id)

st.subheader("What do you want to plan?")
scope_value = st.session_state.get(SCOPE_KEY)
if scope_value is None:
    left, right = st.columns(2)
    if left.button("🗓️ Date Range", use_container_width=True, type="primary"):
        _choose_scope(PlanningScope.DATE_RANGE)
        st.rerun()
    if right.button("📅 Specific Day", use_container_width=True, type="primary"):
        _choose_scope(PlanningScope.SPECIFIC_DAY)
        st.rerun()
    st.stop()

scope = PlanningScope(scope_value)
scope_label = "Date Range" if scope == PlanningScope.DATE_RANGE else "Specific Day"
heading, change = st.columns([4, 1])
heading.markdown(f"**{scope_label}**")
if change.button("Change scope", use_container_width=True):
    st.session_state.pop(SCOPE_KEY, None)
    _clear_after_scope()
    st.rerun()

if scope == PlanningScope.DATE_RANGE:
    left, right = st.columns(2)
    planning_start = left.date_input(
        "Planning starts", value=today + timedelta(days=1), min_value=today,
        key="future_range_start",
    )
    planning_end = right.date_input(
        "Planning ends",
        value=min(planning_start + timedelta(days=6), planning_start + timedelta(weeks=MAX_FUTURE_PLANNING_WEEKS)),
        min_value=planning_start,
        max_value=planning_start + timedelta(weeks=MAX_FUTURE_PLANNING_WEEKS),
        key="future_range_end",
    )
    st.caption(f"Maximum automated planning horizon: {MAX_FUTURE_PLANNING_WEEKS} weeks. Longer ranges are rejected, never truncated.")
else:
    planning_start = st.date_input(
        "Select day", value=today + timedelta(days=1), min_value=today,
        key="future_specific_day",
    )
    planning_end = planning_start

work_day_start = st.time_input("Work day starts", value=time(8, 0), key="future_work_day_start")
if work_day_start >= WORK_DAY_END:
    st.error(f"Work day must start before {WORK_DAY_END:%H:%M}.")
    st.stop()

stored_request = st.session_state.get(REQUEST_KEY)
if stored_request is not None and (
    stored_request.scope != scope
    or stored_request.planning_start != planning_start
    or stored_request.planning_end != planning_end
    or stored_request.work_day_start != work_day_start
):
    _clear_after_method()

st.subheader("How do you want to plan this day?" if scope == PlanningScope.SPECIFIC_DAY else "How do you want to plan?")
method_value = st.session_state.get(METHOD_KEY)
if method_value is None:
    write_col, voice_col, manual_col = st.columns(3)
    if write_col.button("✍️ Write", use_container_width=True):
        _choose_method(PlanningInputMethod.WRITE)
        st.rerun()
    if voice_col.button("🎙️ Voice", use_container_width=True):
        _choose_method(PlanningInputMethod.VOICE)
        st.rerun()
    if manual_col.button("⚙️ Manual", use_container_width=True):
        _choose_method(PlanningInputMethod.MANUAL)
        st.rerun()
    st.stop()

method = PlanningInputMethod(method_value)
method_heading, method_change = st.columns([4, 1])
method_heading.markdown(f"**{method.value.title()} input**")
if method_change.button("Change input", use_container_width=True):
    st.session_state.pop(METHOD_KEY, None)
    _clear_after_method()
    st.rerun()

try:
    candidates = load_future_planning_candidates(planning_start, planning_end, user_id)
except Exception as exc:
    st.error(str(exc))
    st.stop()

candidate_by_id = {int(task["task_id"]): task for task in candidates}

if method in {PlanningInputMethod.WRITE, PlanningInputMethod.VOICE}:
    if method == PlanningInputMethod.VOICE:
        recording = st.audio_input("Record your planning request")
        if recording is not None and st.button("Transcribe recording", use_container_width=True):
            try:
                st.session_state["future_voice_text"] = transcribe_voice_note(
                    recording.getvalue(), getattr(recording, "type", "audio/wav")
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Voice transcription is unavailable: {exc}")
        text_key = "future_voice_text"
        prompt_label = "Review the transcript"
    else:
        text_key = "future_written_request"
        prompt_label = "Describe the tasks and constraints"
    raw_input = st.text_area(
        prompt_label,
        key=text_key,
        placeholder="Example: Plan 120 minutes for Machine Learning; it is critical.",
    )
    if st.button("Understand request", type="primary", use_container_width=True):
        try:
            request = understand_future_planning_text(
                raw_input,
                scope=scope,
                input_method=method,
                planning_start=planning_start,
                planning_end=planning_end,
                work_day_start=work_day_start,
                user_id=user_id,
            )
            st.session_state[REQUEST_KEY] = request
            st.session_state.pop(PREVIEW_KEY, None)
        except PlanningClarificationRequired as exc:
            st.warning(str(exc))
        except Exception as exc:
            st.error(str(exc))

    request = st.session_state.get(REQUEST_KEY)
    if request is not None:
        _show_request(request)
        if st.button("Preview plan", type="primary", use_container_width=True):
            _preview_request(request, user_id)

else:
    requests = []
    priority_labels = {1: "Critical", 2: "High", 3: "Medium", 4: "Low", 5: "Optional"}
    if candidate_by_id:
        labels = {
            task_id: f"{task['title']} · {task['plan_date']}"
            for task_id, task in candidate_by_id.items()
        }
        selected_ids = st.multiselect(
            "Existing tasks to include",
            options=list(candidate_by_id),
            format_func=lambda task_id: labels[task_id],
            key="future_manual_task_ids",
        )
    else:
        selected_ids = []
        st.caption("There are no existing tasks in this scope yet. Add new planning drafts below.")

    for task_id in selected_ids:
        task = candidate_by_id[task_id]
        actual = int(task.get("actual_minutes") or 0)
        current_remaining = max(0, int(task["estimated_minutes"]) - actual)
        with st.expander(f"EXISTING · {task['title']}", expanded=True):
            duration = st.number_input(
                "Remaining work (minutes)", min_value=0, step=15, value=current_remaining,
                key=f"future_remaining_{task_id}",
            )
            priorities = [1, 2, 3, 4, 5]
            current_priority = int(task["priority"])
            priority = st.selectbox(
                "Priority", priorities, index=priorities.index(current_priority),
                format_func=lambda value: f"{value} · {priority_labels[value]}",
                key=f"future_priority_{task_id}",
            )
            existing_earliest = (
                datetime.fromisoformat(str(task["earliest_start"])) if task.get("earliest_start") else None
            )
            existing_deadline = (
                datetime.fromisoformat(str(task["deadline"])) if task.get("deadline") else None
            )
            earliest, deadline = _render_timing_constraints(
                key_prefix="future_",
                identity=task_id,
                scope=scope,
                planning_start=planning_start,
                planning_end=planning_end,
                work_day_start=work_day_start,
                existing_earliest=existing_earliest,
                existing_deadline=existing_deadline,
            )
            requests.append(PlanningTaskRequest(
                task_id=task_id,
                title=str(task["title"]),
                remaining_minutes=int(duration),
                priority=int(priority),
                earliest_start=earliest,
                deadline=deadline,
            ))

    draft_ids = st.session_state.setdefault("future_manual_draft_ids", [])
    if st.button("＋ Add new task", use_container_width=True):
        draft_ids.append(uuid.uuid4().hex)
    for number, draft_id in enumerate(list(draft_ids), start=1):
        with st.expander(f"NEW · Draft task {number}", expanded=True):
            title = st.text_input("Task name", key=f"future_draft_title_{draft_id}")
            description = st.text_area(
                "Description (optional)", key=f"future_draft_description_{draft_id}"
            )
            duration = st.number_input(
                "Duration (minutes)", min_value=15, step=15, value=60,
                key=f"future_draft_duration_{draft_id}",
            )
            priority = st.selectbox(
                "Priority", [1, 2, 3, 4, 5], index=2,
                format_func=lambda value: f"{value} · {priority_labels[value]}",
                key=f"future_draft_priority_{draft_id}",
            )
            earliest, deadline = _render_timing_constraints(
                key_prefix="future_draft_",
                identity=draft_id,
                scope=scope,
                planning_start=planning_start,
                planning_end=planning_end,
                work_day_start=work_day_start,
            )
            if st.button("Remove draft", key=f"future_remove_draft_{draft_id}"):
                st.session_state["future_manual_draft_ids"] = [
                    value for value in draft_ids if value != draft_id
                ]
                st.rerun()
            if title.strip():
                requests.append(PlanningTaskRequest(
                    temporary_id=f"draft:{draft_id}",
                    title=title,
                    description=description or None,
                    remaining_minutes=int(duration),
                    priority=int(priority),
                    earliest_start=earliest,
                    deadline=deadline,
                ))

    if st.button("Preview plan", type="primary", use_container_width=True, disabled=not requests):
        try:
            request = StructuredPlanningRequest(
                scope=scope,
                input_method=PlanningInputMethod.MANUAL,
                planning_start=planning_start,
                planning_end=planning_end,
                work_day_start=work_day_start,
                tasks=requests,
            )
            _preview_request(request, user_id)
        except Exception as exc:
            st.error(f"Nothing was changed: {exc}")

_render_preview(user_id)

operation_id = st.session_state.get(OPERATION_KEY)
if operation_id and st.button("Undo last future plan", use_container_width=True):
    if undo_smart_schedule(operation_id, user_id):
        st.success("The exact previous schedule and planning constraints were restored.")
        st.session_state.pop(OPERATION_KEY, None)
        st.rerun()
    else:
        st.error("Undo was rejected because the schedule changed or Calendar cleanup is pending.")
