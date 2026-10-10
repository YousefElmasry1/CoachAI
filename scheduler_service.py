"""
CoachAI - Scheduler Service (Integration Layer)

Bridges the deterministic scheduling algorithm (scheduler.py) with the
persistence layer (database.py). Loads a plan's tasks from SQLite, assigns
time slots via the Scheduler, and writes the results back.

Usage:
    from scheduler_service import SchedulerService
    from database import Database
    from scheduler import SchedulingPreferences
    from datetime import time

    db = Database()
    service = SchedulerService(db)

    preferences = SchedulingPreferences(work_day_start=time(8, 0))

    # Primary API: schedule any plan by id (today, tomorrow, historical...)
    scheduled_tasks = service.schedule_plan(plan_id=42, preferences=preferences)

    # Convenience wrapper: schedule today's plan for a user
    scheduled_tasks = service.schedule_today(user_id=1, preferences=preferences)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from scheduler import (
    Scheduler, ScheduledTask, SchedulingPreferences,
    SchedulingConflict, FixedTaskConflict,
)
from smart_scheduler import SmartScheduler
from database import Database, StaleScheduleError
from config import MAX_SEGMENTS_PER_TASK, MIN_SEGMENT_MINUTES, WORK_DAY_END


@dataclass(frozen=True)
class SchedulingMovement:
    """One understandable old-to-new movement within a scheduling action."""

    task_id: int
    title: str
    old_segments: tuple[tuple[str, str], ...] = ()
    new_segments: tuple[tuple[str, str], ...] = ()
    requested: bool = False


@dataclass(frozen=True)
class SchedulingActionResult:
    """Structured result for one current-day scheduling interaction."""

    success: bool
    task_id: Optional[int] = None
    placed: bool = False
    operation_id: Optional[str] = None
    reason: Optional[str] = None
    message: str = ""
    segments: tuple[tuple[str, str], ...] = ()
    conflicts: tuple = field(default_factory=tuple)
    movements: tuple[SchedulingMovement, ...] = ()


# ---------------------------------------------------------------------------
# Persisted conflict detection (no scheduler run required)
# ---------------------------------------------------------------------------
#
# Scheduler.schedule() only records last_conflicts as a side effect of
# actually running, and that result lives on the (transient) service
# instance for the rest of the process — nowhere in the database. So a
# break that was left conflicting with a fixed task in an earlier
# session (or by a different page/flow) shows no warning at all on a
# fresh page load, until something happens to call the scheduler again.
# This mirrors the exact same break-vs-fixed-task overlap check inline,
# but reads straight off the already-saved scheduled_start/scheduled_end
# columns, so it reflects the real current state on every render —
# no scheduler run required.

def _row_get(row, key: str, default=None):
    """Read an optional column from a sqlite3.Row or plain dict."""
    if isinstance(row, dict):
        return row.get(key, default)
    if key in row.keys():
        return row[key]
    return default


def _parse_hhmm(value) -> Optional[time]:
    """Parse an 'HH:MM' string from the database into a time object."""
    if not value:
        return None
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def detect_persisted_break_conflicts(rows: list) -> list[SchedulingConflict]:
    """
    Detect break-vs-fixed-task conflicts directly from persisted task rows.

    Args:
        rows: task rows (sqlite3.Row or dict) such as those returned by
            Database.get_tasks_by_plan() / services.load_plan_tasks().
            Only rows with is_fixed_time set and both scheduled_start and
            scheduled_end populated are considered — anything else has no
            fixed, comparable time window yet.

    Returns:
        A list of SchedulingConflict objects (empty if none). Neither a
        break nor a fixed-time task is ever auto-moved, so an overlap
        here can't be silently resolved — it's meant to be surfaced to
        the user with the same "move before / move after / remove"
        choice the live scheduler run offers.
    """
    breaks: list[tuple[time, time, str, int]] = []
    fixed: list[tuple[time, time, str, int]] = []

    for row in rows:
        if not _row_get(row, "is_fixed_time", 0):
            continue
        start = _parse_hhmm(_row_get(row, "scheduled_start"))
        end = _parse_hhmm(_row_get(row, "scheduled_end"))
        if start is None or end is None:
            continue
        title = str(_row_get(row, "title", ""))
        if _row_get(row, "is_break", 0):
            breaks.append((start, end, title, int(row["task_id"])))
        else:
            fixed.append((start, end, title, int(row["task_id"])))

    conflicts: list[SchedulingConflict] = []
    for break_start, break_end, _break_title, break_id in breaks:
        for fixed_start, fixed_end, fixed_title, fixed_id in fixed:
            if break_start < fixed_end and break_end > fixed_start:
                conflicts.append(SchedulingConflict(
                    break_start=break_start,
                    break_end=break_end,
                    fixed_task_title=fixed_title,
                    fixed_task_start=fixed_start,
                    fixed_task_end=fixed_end,
                    break_task_id=break_id,
                    fixed_task_id=fixed_id,
                ))
    return conflicts


def detect_persisted_fixed_task_conflicts(rows: list) -> list[FixedTaskConflict]:
    """
    Detect fixed-task-vs-fixed-task conflicts directly from persisted
    task rows — the counterpart to detect_persisted_break_conflicts()
    for two ordinary (non-break) fixed-time tasks that overlap each
    other (e.g. two imported calendar events both pinned to the same
    hour). Same "no scheduler run required" reasoning applies: this
    reads straight off scheduled_start/scheduled_end, so a stale
    overlap from an earlier session is never silently hidden.

    Args:
        rows: task rows (sqlite3.Row or dict), same shape as
            detect_persisted_break_conflicts().

    Returns:
        A list of FixedTaskConflict objects (empty if none). Each pair
        is only reported once (i < j).
    """
    fixed: list[tuple[time, time, str, int]] = []
    for row in rows:
        if not _row_get(row, "is_fixed_time", 0) or _row_get(row, "is_break", 0):
            continue
        start = _parse_hhmm(_row_get(row, "scheduled_start"))
        end = _parse_hhmm(_row_get(row, "scheduled_end"))
        if start is None or end is None:
            continue
        fixed.append((start, end, str(_row_get(row, "title", "")), int(row["task_id"])))

    conflicts: list[FixedTaskConflict] = []
    for i in range(len(fixed)):
        a_start, a_end, a_title, a_id = fixed[i]
        for j in range(i + 1, len(fixed)):
            b_start, b_end, b_title, b_id = fixed[j]
            if a_start < b_end and a_end > b_start:
                conflicts.append(FixedTaskConflict(
                    task_a_title=a_title,
                    task_a_start=a_start,
                    task_a_end=a_end,
                    task_b_title=b_title,
                    task_b_start=b_start,
                    task_b_end=b_end,
                    task_a_id=a_id,
                    task_b_id=b_id,
                ))
    return conflicts


# ---------------------------------------------------------------------------
# Scheduler Service
# ---------------------------------------------------------------------------

class SchedulerService:
    """
    Integration service that loads tasks from the database, runs the
    deterministic scheduling algorithm, and persists the time slots back.

    Responsibilities:
        1. Load a plan (by id, or today's plan for a user) from SQLite.
        2. Load all tasks belonging to that plan.
        3. Convert database rows into ScheduledTask objects.
        4. Invoke the Scheduler to assign start/end times, anchored to the
           plan's own plan_date and the user's SchedulingPreferences.
        5. Write scheduled_start and scheduled_end back to the database.
        6. Return the fully scheduled tasks to the caller.

    Attributes:
        db: An initialised Database instance.
    """

    def __init__(self, db: Database) -> None:
        """
        Initialise the service with a database connection.

        Args:
            db: An already-connected Database object.
        """
        self.db: Database = db
        # SchedulingConflict objects (break vs. fixed-time task) found
        # during the most recent schedule_plan()/schedule_today() call.
        # Neither side can be auto-moved, so callers can surface this
        # list to the user as a "couldn't auto-resolve" warning.
        self.last_conflicts: list = []
        # FixedTaskConflict objects (fixed-time task vs. fixed-time
        # task) from the same run — same "can't auto-resolve" story,
        # just between two ordinary tasks instead of a break and a task.
        self.last_fixed_conflicts: list = []
        self.last_unplaced: list = []
        self.last_operation_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_scheduler(self, preferences: SchedulingPreferences) -> Scheduler:
        """
        Build the Scheduler to use for a single call.

        The scheduler always requires the user's preferences (in
        particular work_day_start) — there is no default scheduler to
        cache or fall back to, since the scheduler must never guess the
        user's day.

        Args:
            preferences: The user's scheduling preferences for this call.

        Returns:
            A Scheduler configured with the given preferences.
        """
        return SmartScheduler(preferences=preferences)

    @staticmethod
    def _row_get(row, key: str, default=None):
        """
        Safely read an optional column from a sqlite3.Row.

        sqlite3.Row supports dict-style indexing (row["col"]) and
        row.keys(), but — unlike a plain dict — it has no .get() method.
        This helper provides that missing convenience without changing
        how rows are produced elsewhere in the codebase.

        Args:
            row: A sqlite3.Row (or plain dict) from the database.
            key: The column name to read.
            default: Value to return if the column is absent.

        Returns:
            The column value, or default if the column doesn't exist.
        """
        if isinstance(row, dict):
            return row.get(key, default)
        if key in row.keys():
            return row[key]
        return default

    def _parse_plan_date(self, plan) -> date:
        """
        Extract and parse the plan_date stored on a plan row.

        Args:
            plan: sqlite3.Row from plans, expected to have a ``plan_date``
                column stored as an ISO-8601 date string.

        Returns:
            The plan's date as a ``date`` object.

        Raises:
            ValueError: If plan_date is missing or not a valid ISO date.
        """
        raw_value = plan["plan_date"]
        try:
            return date.fromisoformat(str(raw_value))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Plan {plan['plan_id']} has an invalid plan_date value: "
                f"{raw_value!r}"
            ) from exc

    def _rows_to_scheduled_tasks(
        self,
        rows: list,
    ) -> list[ScheduledTask]:
        """
        Convert database rows into ScheduledTask objects for the algorithm.

        Args:
            rows: sqlite3.Row objects from get_tasks_by_plan().

        Returns:
            List of ScheduledTask objects with scheduling fields unset,
            EXCEPT for fixed-time tasks (is_fixed_time=1), whose existing
            scheduled_start/scheduled_end are carried over as fixed_start/
            fixed_end so the Scheduler treats them as immovable. Each
            ScheduledTask also carries the originating row's task_id, so
            results can be persisted back to the exact right row instead
            of relying on list position/order_index.
        """
        tasks: list[ScheduledTask] = []
        for row in rows:
            is_fixed = bool(self._row_get(row, "is_fixed_time", 0))
            fixed_start = None
            fixed_end = None
            if is_fixed:
                fixed_start = self._parse_hhmm(self._row_get(row, "scheduled_start"))
                fixed_end = self._parse_hhmm(self._row_get(row, "scheduled_end"))

            # sqlite3.Row supports both dict-style and attribute access
            st = ScheduledTask(
                title=str(row["title"]),
                category=str(self._row_get(row, "category_name", "") or ""),
                estimated_minutes=int(row["estimated_minutes"]),
                priority=int(row["priority"]),
                description=str(self._row_get(row, "description", "") or ""),
                order_index=int(self._row_get(row, "order_index", 0)),
                task_id=int(row["task_id"]),
                is_fixed_time=is_fixed,
                fixed_start=fixed_start,
                fixed_end=fixed_end,
                is_break=bool(self._row_get(row, "is_break", 0)),
                earliest_start=ScheduledTask._parse_local_datetime(
                    self._row_get(row, "earliest_start")
                ),
                deadline=ScheduledTask._parse_local_datetime(
                    self._row_get(row, "deadline")
                ),
            )
            tasks.append(st)
        return tasks

    @staticmethod
    def _parse_hhmm(value) -> Optional[time]:
        """Parse an 'HH:MM' string from the database into a time object."""
        if not value:
            return None
        try:
            parts = str(value).split(":")
            return time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _segment_payload(rows: list) -> dict[int, list[dict]]:
        """Copy the authoritative segment state without losing Calendar identity."""
        payload: dict[int, list[dict]] = {}
        for row in rows:
            payload.setdefault(int(row["task_id"]), []).append({
                key: row.get(key)
                for key in (
                    "segment_index", "scheduled_start", "scheduled_end",
                    "google_event_id", "google_calendar_id", "calendar_provenance",
                    "calendar_sync_status", "calendar_sync_error",
                )
            })
        return payload

    def add_and_schedule_task(
        self,
        plan_id: int,
        user_id: str,
        preferences: SchedulingPreferences,
        *,
        title: str,
        estimated_minutes: int,
        is_fixed_time: bool,
        fixed_start: Optional[time] = None,
        category_id: Optional[int] = None,
        description: str = "",
        priority: int = 3,
        order_index: int = 0,
        blocked_slots: Optional[list[tuple]] = None,
        expected_revision: Optional[int] = None,
    ) -> SchedulingActionResult:
        """Create and place one current-day task as one atomic operation.

        A new flexible task competes by priority with the plan's eligible,
        never-started flexible tasks.  Started/terminal work and every
        fixed, break, or Calendar interval remain immutable anchors.  A fixed
        task is persisted only at its requested interval; illegal overlap is
        rejected before any write.
        """
        if not str(title).strip():
            raise ValueError("Task title is required.")
        if int(estimated_minutes) <= 0:
            raise ValueError("Task duration must be positive.")
        if is_fixed_time and fixed_start is None:
            raise ValueError("A Fixed task requires a start time.")

        plan = self.db.get_plan_by_id(plan_id, user_id)
        if plan is None:
            raise ValueError("Today's plan was not found for this user.")
        plan_date = self._parse_plan_date(plan)
        current_revision = int(plan["schedule_revision"] or 0)
        if expected_revision is not None and current_revision != int(expected_revision):
            raise StaleScheduleError(
                "Today's schedule changed before the task could be added."
            )
        expected_revision = current_revision
        existing_tasks = self.db.get_tasks_with_segments(plan_id, user_id)
        existing_segments = self.db.get_plan_segments(plan_id, user_id)
        progress_state = {
            int(row["task_id"]): (
                str(row.get("status") or "pending"),
                str(row.get("started_at")) if row.get("started_at") is not None else None,
            )
            for row in existing_tasks
        }
        all_occupied = list(blocked_slots or []) + [
            (self._parse_hhmm(row["scheduled_start"]), self._parse_hhmm(row["scheduled_end"]))
            for row in existing_segments
        ]
        all_occupied = [(start, end) for start, end in all_occupied if start and end]

        if is_fixed_time:
            requested_start = datetime.combine(plan_date, fixed_start)
            requested_end = requested_start + timedelta(minutes=int(estimated_minutes))
            if requested_end.date() != plan_date or requested_end.time() > WORK_DAY_END:
                raise ValueError("The Fixed task would cross the work-day boundary.")
            for blocked_start, blocked_end in all_occupied:
                blocked_start_dt = datetime.combine(plan_date, blocked_start)
                blocked_end_dt = datetime.combine(plan_date, blocked_end)
                if requested_start < blocked_end_dt and blocked_start_dt < requested_end:
                    raise ValueError(
                        "The Fixed task conflicts with an existing task, break, or Calendar block."
                    )

        task_spec = {
            "task_id": -1,
            "title": str(title).strip(),
            "category": "",
            "estimated_minutes": int(estimated_minutes),
            "priority": int(priority),
            "description": description or "",
            "order_index": int(order_index),
            "is_fixed_time": bool(is_fixed_time),
            "fixed_start": fixed_start.strftime("%H:%M") if fixed_start else None,
            "is_break": False,
        }

        replannable_ids: set[int] = set()
        if is_fixed_time:
            allocation = self._resolve_scheduler(
                SchedulingPreferences(
                    preferences.work_day_start, blocked_slots=all_occupied,
                )
            ).allocate([task_spec], plan_date)
        else:
            # A task is "started" only through the existing timer/status
            # contract, never merely because it has scheduled segments.  The
            # boundary is the end of the protected started/terminal portion;
            # fixed/break/Calendar windows remain anchors inside the later
            # feasible space rather than pushing the boundary themselves.
            replanning_boundary = preferences.work_day_start
            for row in existing_tasks:
                status = str(row.get("status") or "pending")
                started = bool(row.get("started_at")) or status == "in_progress"
                terminal = status in {"completed", "failed"}
                if not (started or terminal):
                    continue
                for segment in row.get("segments", []):
                    end = self._parse_hhmm(segment.get("scheduled_end"))
                    if end is not None and end > replanning_boundary:
                        replanning_boundary = end

            eligible: list[dict] = []
            for row in existing_tasks:
                if bool(row.get("is_fixed_time")) or bool(row.get("is_break")):
                    continue
                if str(row.get("status") or "pending") != "pending" or row.get("started_at"):
                    continue
                starts = [
                    self._parse_hhmm(segment.get("scheduled_start"))
                    for segment in row.get("segments", [])
                ]
                starts = [start for start in starts if start is not None]
                # A pending task with any pre-boundary segment is outside the
                # affected future region and remains an immutable anchor.
                if starts and min(starts) < replanning_boundary:
                    continue
                eligible.append({
                    "task_id": int(row["task_id"]),
                    "title": str(row.get("title") or "Untitled"),
                    "category": "",
                    "estimated_minutes": int(row["estimated_minutes"]),
                    "priority": int(row.get("priority") or 3),
                    "description": str(row.get("description") or ""),
                    "order_index": int(row.get("order_index") or 0),
                    "is_fixed_time": False,
                    "is_break": False,
                    "_was_scheduled": bool(starts),
                    "_existing_start": min(starts).strftime("%H:%M:%S") if starts else "23:59:59",
                })

            # First attempt the established incremental placement against the
            # intact schedule.  If it already puts the new task before every
            # lower-priority eligible task, no existing work needs to move.
            intact_anchors = list(blocked_slots or []) + [
                (self._parse_hhmm(row["scheduled_start"]), self._parse_hhmm(row["scheduled_end"]))
                for row in existing_segments
            ]
            intact_anchors = [(start, end) for start, end in intact_anchors if start and end]
            provisional = self._resolve_scheduler(
                SchedulingPreferences(replanning_boundary, blocked_slots=intact_anchors)
            ).allocate([task_spec], plan_date)
            provisional_new = next(
                (item for item in provisional.placed_tasks if item.task_id == -1), None
            )
            provisional_start = (
                provisional_new.segments[0].scheduled_start
                if provisional_new is not None and provisional_new.segments else None
            )

            replannable = []
            for item in eligible:
                existing_start = self._parse_hhmm(item.get("_existing_start"))
                lower_priority = int(item.get("priority") or 3) > int(priority)
                blocks_priority_position = (
                    provisional_start is None
                    or existing_start is None
                    or existing_start < provisional_start
                )
                # An unplaced task has no window to preserve and must still
                # compete; otherwise a new Low task could consume the last
                # slot while an older unscheduled Critical task is ignored.
                if not item.get("_was_scheduled") or (lower_priority and blocks_priority_position):
                    replannable_ids.add(int(item["task_id"]))
                    replannable.append(item)

            if not replannable:
                allocation = provisional
            else:
                anchors = list(blocked_slots or []) + [
                    (self._parse_hhmm(row["scheduled_start"]), self._parse_hhmm(row["scheduled_end"]))
                    for row in existing_segments
                    if int(row["task_id"]) not in replannable_ids
                ]
                anchors = [(start, end) for start, end in anchors if start and end]
                task_spec["_existing_start"] = "23:59:59"
                replannable.append(task_spec)
                replannable.sort(key=lambda item: (
                    int(item.get("priority") or 3),
                    str(item.get("_existing_start") or "23:59:59"),
                    1 if int(item.get("task_id") or -1) == -1 else 0,
                    int(item.get("order_index") or 0),
                    int(item.get("task_id") or -1),
                ))
                allocation = self._resolve_scheduler(
                    SchedulingPreferences(replanning_boundary, blocked_slots=anchors)
                ).allocate(replannable, plan_date)

        placed_by_id = {
            int(item.task_id): item for item in allocation.placed_tasks
            if item.task_id is not None
        }
        unplaced_by_id = {
            int(item.task_id): item for item in allocation.unplaced_tasks
            if item.task_id is not None
        }

        def segment_payload(item) -> list[dict]:
            return [] if item is None else [
                {
                    "segment_index": segment.segment_index,
                    "scheduled_start": segment.scheduled_start.strftime("%H:%M:%S"),
                    "scheduled_end": segment.scheduled_end.strftime("%H:%M:%S"),
                }
                for segment in item.segments
            ]

        candidate = segment_payload(placed_by_id.get(-1))
        unplaced = unplaced_by_id.get(-1)

        # Fixed tasks are intentionally immovable and the allocator always
        # emits their requested interval.  Complete-state validation below
        # remains authoritative for overlap and same-day constraints.
        fixed_start_str = candidate[0]["scheduled_start"] if candidate else None
        fixed_end_str = candidate[-1]["scheduled_end"] if candidate else None
        with self.db.transaction():
            current = self.db.get_plan_by_id(plan_id, user_id)
            if int(current["schedule_revision"] or 0) != expected_revision:
                raise StaleScheduleError("Today's schedule changed before the task could be added.")
            current_tasks = self.db.get_tasks_with_segments(plan_id, user_id)
            current_progress_state = {
                int(row["task_id"]): (
                    str(row.get("status") or "pending"),
                    str(row.get("started_at")) if row.get("started_at") is not None else None,
                )
                for row in current_tasks
            }
            if current_progress_state != progress_state:
                raise StaleScheduleError(
                    "Task progress changed before priority replanning could be saved."
                )
            before_state = self.db._schedule_snapshot(plan_id, user_id)
            task_id = self.db.add_task(
                plan_id=plan_id,
                user_id=user_id,
                title=str(title).strip(),
                category_id=category_id,
                description=description or "",
                priority=int(priority),
                estimated_minutes=int(estimated_minutes),
                scheduled_start=fixed_start_str if is_fixed_time else None,
                scheduled_end=fixed_end_str if is_fixed_time else None,
                order_index=int(order_index),
                is_fixed_time=bool(is_fixed_time),
            )
            payload = self._segment_payload(existing_segments)
            for row in existing_tasks:
                payload.setdefault(int(row["task_id"]), [])
            for existing_id in replannable_ids:
                payload[existing_id] = segment_payload(placed_by_id.get(existing_id))
            payload[int(task_id)] = candidate
            before_state["created_task_ids"] = [int(task_id)]
            operation_id = self.db.replace_plan_segments(
                plan_id, user_id, payload,
                expected_revision=expected_revision + 1,
                before_state_override=before_state,
            )

        self.last_operation_id = operation_id
        self.last_unplaced = allocation.unplaced_tasks
        self.last_conflicts = allocation.conflicts
        self.last_fixed_conflicts = allocation.fixed_conflicts
        windows = tuple(
            (segment["scheduled_start"], segment["scheduled_end"])
            for segment in candidate
        )
        return SchedulingActionResult(
            success=True,
            task_id=int(task_id),
            placed=bool(candidate),
            operation_id=operation_id,
            reason=unplaced.reason if unplaced else None,
            message=(
                unplaced.message if unplaced else
                "Fixed task saved at its requested time." if is_fixed_time else
                "Task placed after priority-replanning the eligible unstarted work."
            ),
            segments=windows,
            conflicts=tuple(allocation.conflicts) + tuple(allocation.fixed_conflicts),
        )

    def move_flexible_task(
        self,
        task_id: int,
        user_id: str,
        new_start: time,
        expected_revision: int,
        blocked_slots: Optional[list[tuple]] = None,
    ) -> SchedulingActionResult:
        """Pin one Flexible task, then Smart Schedule its scheduled peers.

        The requested task becomes one exact continuous anchor for this
        operation. Protected work remains occupied and immutable, while every
        other scheduled pending Flexible task competes in one SmartScheduler
        allocation. The complete result is saved as one atomic operation.
        """
        task = self.db.get_task(task_id, user_id)
        if task is None:
            from database import OwnershipError
            raise OwnershipError("Task not found or not owned by this user.")
        if bool(task["is_fixed_time"]) or bool(task["is_break"]):
            raise ValueError(f"{task['title']} is fixed and cannot be manually moved.")
        if str(task["status"] or "pending") != "pending" or task["started_at"] is not None:
            raise ValueError(f"{task['title']} has already started or finished and cannot be moved.")
        duration = int(task["estimated_minutes"])
        end_dt = datetime.combine(date.min, new_start) + timedelta(minutes=duration)
        if end_dt.date() != date.min or end_dt.time() > WORK_DAY_END:
            raise ValueError("The requested move would cross the work-day boundary.")
        plan_id = int(task["plan_id"])
        plan = self.db.get_plan_by_id(plan_id, user_id)
        if plan is None:
            raise ValueError("Today's plan was not found for this user.")
        if int(plan["schedule_revision"] or 0) != int(expected_revision):
            raise StaleScheduleError("Today's schedule changed before the task could be moved.")
        plan_date = self._parse_plan_date(plan)
        requested_constraint = ScheduledTask.from_task(task)
        requested_start_dt = datetime.combine(plan_date, new_start)
        requested_end_dt = datetime.combine(plan_date, end_dt.time())
        if (
            requested_constraint.earliest_start is not None
            and requested_start_dt < requested_constraint.earliest_start
        ):
            raise ValueError(f"Cannot move {task['title']} before its earliest start.")
        if (
            requested_constraint.deadline is not None
            and requested_end_dt > requested_constraint.deadline
        ):
            raise ValueError(f"Cannot move {task['title']} beyond its deadline.")
        tasks = self.db.get_tasks_with_segments(plan_id, user_id)
        tasks_by_id = {int(row["task_id"]): row for row in tasks}
        requested = tasks_by_id[int(task_id)]
        existing_segments = self.db.get_plan_segments(plan_id, user_id)
        payload = self._segment_payload(existing_segments)
        for row in tasks:
            payload.setdefault(int(row["task_id"]), [])

        def parsed_windows(segments) -> list[tuple[time, time]]:
            result = []
            for segment in segments:
                start = self._parse_hhmm(segment.get("scheduled_start"))
                end = self._parse_hhmm(segment.get("scheduled_end"))
                if start is not None and end is not None:
                    result.append((start, end))
            return result

        def string_windows(segments) -> tuple[tuple[str, str], ...]:
            return tuple(
                (str(segment.get("scheduled_start")), str(segment.get("scheduled_end")))
                for segment in segments
            )

        def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
            return a_start < b_end and b_start < a_end

        def calendar_locked(row: dict) -> bool:
            return any(
                segment.get("google_event_id")
                and segment.get("calendar_provenance") in {"imported", "legacy_unknown"}
                for segment in row.get("segments", [])
            )

        def protected_kind(row: dict) -> Optional[str]:
            if bool(row.get("is_break")):
                return "break"
            if calendar_locked(row):
                return "Calendar event"
            if bool(row.get("is_fixed_time")):
                return "fixed task"
            status = str(row.get("status") or "pending")
            if status in {"completed", "failed"}:
                return f"{status} task"
            if row.get("started_at") is not None or status == "in_progress":
                return "started task"
            return None

        def eligible(row: dict) -> bool:
            return (
                protected_kind(row) is None
                and str(row.get("status") or "pending") == "pending"
                and not bool(row.get("is_fixed_time"))
                and not bool(row.get("is_break"))
            )

        if calendar_locked(requested):
            raise ValueError(f"{requested['title']} is a protected Calendar event and cannot be moved.")

        external: list[tuple[time, time]] = []
        for raw in blocked_slots or []:
            if len(raw) < 2 or raw[0] is None or raw[1] is None or raw[1] <= raw[0]:
                raise ValueError("Calendar blocks must be valid same-day intervals.")
            if raw[1] > WORK_DAY_END:
                raise ValueError("A Calendar block crosses the work-day boundary.")
            external.append((raw[0], raw[1]))

        requested_end = end_dt.time()
        requested_old = parsed_windows(requested.get("segments", []))
        requested_new = [{
            "segment_index": 0,
            "scheduled_start": new_start.strftime("%H:%M:%S"),
            "scheduled_end": requested_end.strftime("%H:%M:%S"),
        }]
        payload[int(task_id)] = requested_new

        for blocked_start, blocked_end in external:
            if overlaps(new_start, requested_end, blocked_start, blocked_end):
                raise ValueError(
                    f"Cannot move {requested['title']} to {new_start.strftime('%H:%M')}–"
                    f"{requested_end.strftime('%H:%M')} because it conflicts with a Calendar event "
                    f"({blocked_start.strftime('%H:%M')}–{blocked_end.strftime('%H:%M')})."
                )

        for other_id, row in tasks_by_id.items():
            if other_id == int(task_id):
                continue
            conflict_window = next((
                (start, end) for start, end in parsed_windows(row.get("segments", []))
                if overlaps(new_start, requested_end, start, end)
            ), None)
            if conflict_window is None:
                continue
            kind = protected_kind(row)
            if kind is not None:
                start, end = conflict_window
                if kind == "started task":
                    raise ValueError(
                        f"Cannot move {requested['title']} to this time because "
                        f"{row['title']} has already started."
                    )
                raise ValueError(
                    f"Cannot move {requested['title']} to this time because it conflicts with "
                    f"{kind} {row['title']} ({start.strftime('%H:%M')}–{end.strftime('%H:%M')})."
                )
            if not eligible(row):
                raise ValueError(f"No valid local repair is available around {row['title']}.")

        progress_state = {
            row_id: (
                str(row.get("status") or "pending"),
                str(row.get("started_at")) if row.get("started_at") is not None else None,
            )
            for row_id, row in tasks_by_id.items()
        }
        changed_ids: set[int] = {int(task_id)}
        repair_ids: set[int] = {
            row_id for row_id, row in tasks_by_id.items()
            if row_id != int(task_id)
            and eligible(row)
            and bool(parsed_windows(row.get("segments", [])))
        }
        last_unplaced = []

        def task_spec(row: dict) -> dict:
            starts = parsed_windows(row.get("segments", []))
            return {
                "task_id": int(row["task_id"]),
                "title": str(row.get("title") or "Untitled"),
                "category": str(row.get("category_name") or ""),
                "estimated_minutes": int(row["estimated_minutes"]),
                "priority": int(row.get("priority") or 3),
                "description": str(row.get("description") or ""),
                "order_index": int(row.get("order_index") or 0),
                "is_fixed_time": False,
                "is_break": False,
                "earliest_start": row.get("earliest_start"),
                "deadline": row.get("deadline"),
                "_existing_start": starts[0][0].strftime("%H:%M:%S") if starts else "23:59:59",
            }

        def scheduler_segments(item) -> list[dict]:
            return [{
                "segment_index": segment.segment_index,
                "scheduled_start": segment.scheduled_start.strftime("%H:%M:%S"),
                "scheduled_end": segment.scheduled_end.strftime("%H:%M:%S"),
            } for segment in item.segments]

        def allocate_ids(candidate_ids: set[int]):
            if not candidate_ids:
                return None
            anchors = list(external)
            for owner_id, segments in payload.items():
                if owner_id in candidate_ids:
                    continue
                anchors.extend(parsed_windows(segments))
            starts = [new_start] + [start for start, _ in requested_old]
            for candidate_id in candidate_ids:
                starts.extend(start for start, _ in parsed_windows(tasks_by_id[candidate_id].get("segments", [])))
            specs = [task_spec(tasks_by_id[candidate_id]) for candidate_id in candidate_ids]
            def constraint_sort(item: dict):
                parsed = ScheduledTask.from_task(item)
                return (
                    parsed.deadline or datetime.max,
                    int(item["priority"]),
                    parsed.earliest_start or datetime.min,
                    item["_existing_start"],
                    int(item["order_index"]),
                    int(item["task_id"]),
                )

            specs.sort(key=constraint_sort)
            allocation = self._resolve_scheduler(
                SchedulingPreferences(min(starts), blocked_slots=anchors)
            ).allocate(specs, plan_date)
            placed = {int(item.task_id): item for item in allocation.placed_tasks}
            return allocation, placed

        if repair_ids:
            allocation, placed = allocate_ids(repair_ids)
            last_unplaced = allocation.unplaced_tasks
            if last_unplaced:
                reason = last_unplaced[0].reason
                detail = (
                    "true capacity shortage" if reason == "capacity_shortfall"
                    else "fragmentation and the segment limit"
                )
                raise ValueError(
                    f"Cannot move {requested['title']} without violating {detail}; "
                    "no valid local repair is available."
                )
            for repair_id in repair_ids:
                payload[repair_id] = scheduler_segments(placed[repair_id])
                changed_ids.add(repair_id)

        with self.db.transaction():
            current = self.db.get_plan_by_id(plan_id, user_id)
            if current is None or int(current["schedule_revision"] or 0) != int(expected_revision):
                raise StaleScheduleError("Today's schedule changed before the move could be saved.")
            current_tasks = self.db.get_tasks_with_segments(plan_id, user_id)
            current_progress = {
                int(row["task_id"]): (
                    str(row.get("status") or "pending"),
                    str(row.get("started_at")) if row.get("started_at") is not None else None,
                )
                for row in current_tasks
            }
            if current_progress != progress_state:
                raise StaleScheduleError("Task progress changed before the move could be saved.")
            operation_id = self.db.replace_plan_segments(
                plan_id, user_id, payload, expected_revision=int(expected_revision),
            )

        movements = []
        for changed_id in sorted(
            changed_ids,
            key=lambda value: (0 if value == int(task_id) else 1, value),
        ):
            old_value = string_windows(tasks_by_id[changed_id].get("segments", []))
            new_value = string_windows(payload[changed_id])
            if old_value == new_value:
                continue
            movements.append(SchedulingMovement(
                task_id=changed_id,
                title=str(tasks_by_id[changed_id].get("title") or "Untitled"),
                old_segments=old_value,
                new_segments=new_value,
                requested=changed_id == int(task_id),
            ))
        self.last_operation_id = operation_id
        self.last_unplaced = []
        return SchedulingActionResult(
            success=True,
            task_id=int(task_id),
            placed=True,
            operation_id=operation_id,
            message=(
                f"Moved {requested['title']} and Smart Scheduled {len(movements) - 1} "
                "Flexible task(s)." if len(movements) > 1
                else f"Moved {requested['title']} without changing unrelated tasks."
            ),
            segments=((new_start.strftime("%H:%M:%S"), requested_end.strftime("%H:%M:%S")),),
            movements=tuple(movements),
        )
    def _save_schedule(
        self,
        scheduled: list[ScheduledTask],
        plan_id: int,
        user_id: str,
    ) -> None:
        """
        Write scheduled_start and scheduled_end back to the database.

        Matches each ScheduledTask to its database row by ``task_id``
        (never by list position/order_index -- two tasks can share the
        same order_index, and positional zip() silently mispairs them
        the moment the scheduler's output order differs at all from the
        original query order).

        Wrapped in a single transaction (Database._update_task_no_commit
        + Database.transaction()) so that persisting a schedule is
        atomic: if any task update fails partway through -- including an
        ownership mismatch, which should never happen in practice since
        every task here was just loaded via get_tasks_by_plan(plan_id,
        user_id), but is checked again here as defense in depth -- the
        whole batch is rolled back rather than leaving some tasks with
        their new time slot and others still on the old one.

        Args:
            scheduled: Tasks with populated time slots.
            plan_id: The parent plan (used only for the error message
                below if a ScheduledTask is missing its task_id).
            user_id: The plan's owner -- every write is scoped through
                Database._update_task_no_commit's own ownership check.
        """
        with self.db.transaction():
            for st in scheduled:
                if st.task_id is None:
                    raise ValueError(
                        f"ScheduledTask '{st.title}' has no task_id -- cannot "
                        f"persist its schedule for plan {plan_id}."
                    )

                start_str: Optional[str] = st.scheduled_start.strftime("%H:%M") if st.scheduled_start else None
                end_str: Optional[str] = st.scheduled_end.strftime("%H:%M") if st.scheduled_end else None

                self.db._update_task_no_commit(
                    st.task_id,
                    user_id,
                    scheduled_start=start_str,
                    scheduled_end=end_str,
                )

    @staticmethod
    def _validate_allocation(allocation, rows: list, blocked_slots: Optional[list[tuple]]) -> None:
        """Validate the complete in-memory candidate before any write occurs."""
        source = {int(row["task_id"]): row for row in rows}
        placed = {int(task.task_id): task for task in allocation.placed_tasks}
        unplaced = {int(item.task_id) for item in allocation.unplaced_tasks}
        if set(placed) & unplaced or set(placed) | unplaced != set(source):
            raise ValueError("Allocator result does not partition every logical task exactly once.")

        intervals = []
        for task_id, task in placed.items():
            row = source[task_id]
            if not 1 <= len(task.segments) <= MAX_SEGMENTS_PER_TASK:
                raise ValueError(f"Task {task_id} has an invalid segment count.")
            total = 0
            for expected_index, segment in enumerate(task.segments):
                if segment.segment_index != expected_index:
                    raise ValueError(f"Task {task_id} segment indexes are not contiguous.")
                start = datetime.combine(date.min, segment.scheduled_start)
                end = datetime.combine(date.min, segment.scheduled_end)
                if end <= start or segment.scheduled_end > WORK_DAY_END:
                    raise ValueError(f"Task {task_id} has an invalid same-day segment.")
                minutes = int((end - start).total_seconds() // 60)
                if not bool(row["is_fixed_time"]) and minutes < MIN_SEGMENT_MINUTES:
                    raise ValueError(f"Task {task_id} has a segment below the minimum duration.")
                total += minutes
                intervals.append((start, end, task_id, bool(row["is_fixed_time"])))
            if bool(row["is_fixed_time"]):
                original_start = SchedulerService._parse_hhmm(row["scheduled_start"])
                original_end = SchedulerService._parse_hhmm(row["scheduled_end"])
                if (task.segments[0].scheduled_start, task.segments[-1].scheduled_end) != (original_start, original_end):
                    raise ValueError(f"Fixed task {task_id} was moved or split.")
            elif total != int(row["estimated_minutes"]):
                raise ValueError(f"Task {task_id} does not preserve its estimated duration.")

        for index, (start, end, task_id, fixed) in enumerate(intervals):
            for other_start, other_end, other_id, other_fixed in intervals[index + 1:]:
                if task_id != other_id and start < other_end and end > other_start and not (fixed and other_fixed):
                    raise ValueError(f"Unexpected overlap between tasks {task_id} and {other_id}.")
        for item in allocation.unplaced_tasks:
            if item.reason not in {"capacity_shortfall", "fragmentation_segment_cap"}:
                raise ValueError(f"Unplaced task {item.task_id} has no structured reason.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule_plan(
        self,
        plan_id: int,
        user_id: str,
        preferences: SchedulingPreferences,
        blocked_slots: Optional[list[tuple]] = None,
        expected_revision: Optional[int] = None,
    ) -> list[ScheduledTask]:

        # Per-run state must never leak across attempts.  In particular, a
        # failed run after a successful one must not leave the old operation
        # id visible to the UI as if the failure had persisted a new schedule.
        self.last_operation_id = None
        self.last_unplaced = []
        self.last_conflicts = []
        self.last_fixed_conflicts = []

        # ------------------------------------------------------------------
        # Step 1: Load the plan and its date -- scoped to user_id, so a
        # plan_id belonging to a different user raises instead of ever
        # being scheduled/read here.
        # ------------------------------------------------------------------
        plan = self.db.get_plan_by_id(plan_id, user_id)
        if plan is None:
            raise ValueError(
                f"No plan found with plan_id {plan_id} for user {user_id!r}."
            )
        if (
            expected_revision is not None
            and int(plan["schedule_revision"] or 0) != int(expected_revision)
        ):
            raise StaleScheduleError(
                "The schedule changed before this scheduling request was applied."
            )

        plan_date = self._parse_plan_date(plan)

        # ------------------------------------------------------------------
        # Step 2: Load tasks for this plan
        # ------------------------------------------------------------------
        db_rows = self.db.get_tasks_with_segments(plan_id, user_id)
        if not db_rows:
            return []  # No tasks to schedule

        # ------------------------------------------------------------------
        # Step 3: Convert to scheduler-friendly objects
        # ------------------------------------------------------------------
        tasks = self._rows_to_scheduled_tasks(db_rows)

        # ------------------------------------------------------------------
        # Step 4: Run the scheduling algorithm, anchored to plan_date and
        #         the user's own preferences
        # ------------------------------------------------------------------
        if blocked_slots:
            preferences.blocked_slots = blocked_slots
        scheduler = self._resolve_scheduler(preferences)
        allocation = scheduler.allocate(tasks, plan_date=plan_date)
        self._validate_allocation(allocation, db_rows, blocked_slots)
        scheduled: list[ScheduledTask] = allocation.placed_tasks
        self.last_conflicts = allocation.conflicts
        self.last_fixed_conflicts = allocation.fixed_conflicts
        self.last_unplaced = allocation.unplaced_tasks

        # ------------------------------------------------------------------
        # Step 5: Persist time slots back to the database
        # ------------------------------------------------------------------
        placed_by_id = {task.task_id: task for task in scheduled}
        segments_by_task: dict[int, list[dict]] = {}
        for row in db_rows:
            task_id = int(row["task_id"])
            task = placed_by_id.get(task_id)
            segments_by_task[task_id] = [] if task is None else [
                {
                    "segment_index": segment.segment_index,
                    "scheduled_start": segment.scheduled_start.strftime("%H:%M:%S"),
                    "scheduled_end": segment.scheduled_end.strftime("%H:%M:%S"),
                }
                for segment in task.segments
            ]
        self.last_operation_id = self.db.replace_plan_segments(
            plan_id, user_id, segments_by_task,
            expected_revision=int(plan["schedule_revision"] or 0),
        )

        return scheduled

    def schedule_today(
        self,
        user_id: str,
        preferences: SchedulingPreferences,
        blocked_slots: Optional[list[tuple]] = None,
    ) -> list[ScheduledTask]:

        plan = self.db.get_today_plan(user_id)
        if plan is None:
            raise ValueError(f"No plan found for user {user_id} today.")

        plan_id: int = int(plan["plan_id"])
        return self.schedule_plan(
            plan_id, user_id, preferences=preferences, blocked_slots=blocked_slots
        )
