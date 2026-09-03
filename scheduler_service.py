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

from datetime import date, time
from typing import Optional

from scheduler import (
    Scheduler, ScheduledTask, SchedulingPreferences,
    SchedulingConflict, FixedTaskConflict,
)
from database import Database


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
    breaks: list[tuple[time, time, str]] = []
    fixed: list[tuple[time, time, str]] = []

    for row in rows:
        if not _row_get(row, "is_fixed_time", 0):
            continue
        start = _parse_hhmm(_row_get(row, "scheduled_start"))
        end = _parse_hhmm(_row_get(row, "scheduled_end"))
        if start is None or end is None:
            continue
        title = str(_row_get(row, "title", ""))
        if _row_get(row, "is_break", 0):
            breaks.append((start, end, title))
        else:
            fixed.append((start, end, title))

    conflicts: list[SchedulingConflict] = []
    for break_start, break_end, _break_title in breaks:
        for fixed_start, fixed_end, fixed_title in fixed:
            if break_start < fixed_end and break_end > fixed_start:
                conflicts.append(SchedulingConflict(
                    break_start=break_start,
                    break_end=break_end,
                    fixed_task_title=fixed_title,
                    fixed_task_start=fixed_start,
                    fixed_task_end=fixed_end,
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
    fixed: list[tuple[time, time, str]] = []
    for row in rows:
        if not _row_get(row, "is_fixed_time", 0) or _row_get(row, "is_break", 0):
            continue
        start = _parse_hhmm(_row_get(row, "scheduled_start"))
        end = _parse_hhmm(_row_get(row, "scheduled_end"))
        if start is None or end is None:
            continue
        fixed.append((start, end, str(_row_get(row, "title", ""))))

    conflicts: list[FixedTaskConflict] = []
    for i in range(len(fixed)):
        a_start, a_end, a_title = fixed[i]
        for j in range(i + 1, len(fixed)):
            b_start, b_end, b_title = fixed[j]
            if a_start < b_end and a_end > b_start:
                conflicts.append(FixedTaskConflict(
                    task_a_title=a_title,
                    task_a_start=a_start,
                    task_a_end=a_end,
                    task_b_title=b_title,
                    task_b_start=b_start,
                    task_b_end=b_end,
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
        return Scheduler(preferences=preferences)

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
            fixed_end so the Scheduler treats them as immovable.
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
                category=str(self._row_get(row, "category", "") or ""),
                estimated_minutes=int(row["estimated_minutes"]),
                priority=int(row["priority"]),
                description=str(self._row_get(row, "description", "") or ""),
                order_index=int(self._row_get(row, "order_index", 0)),
                is_fixed_time=is_fixed,
                fixed_start=fixed_start,
                fixed_end=fixed_end,
                is_break=bool(self._row_get(row, "is_break", 0)),
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

    def _save_schedule(
        self,
        scheduled: list[ScheduledTask],
        plan_id: int,
    ) -> None:
        """
        Write scheduled_start and scheduled_end back to the database.

        Uses positional matching between the database rows (returned by
        get_tasks_by_plan, which orders by order_index ASC, task_id ASC)
        and the scheduled task list (same original ordering) to pair each
        ScheduledTask with its correct database row.

        The entire save is wrapped in a single transaction so that either
        all task updates succeed or none of them are committed.

        Args:
            scheduled: Tasks with populated time slots.
            plan_id: The parent plan (used to load the corresponding rows).
        """
        # Load original DB rows — same order used to build ScheduledTasks
        db_rows = self.db.get_tasks_by_plan(plan_id)

        # Pair each scheduled task with its database row by position
        with self.db.transaction():
            for row, st in zip(db_rows, scheduled):
                task_id = int(row["task_id"])

                # Format times as HH:MM strings for SQLite TIME column
                start_str: str = st.scheduled_start.strftime("%H:%M") if st.scheduled_start else None
                end_str: str = st.scheduled_end.strftime("%H:%M") if st.scheduled_end else None

                self.db.execute(
                    """
                    UPDATE tasks
                    SET scheduled_start = ?,
                        scheduled_end = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                    """,
                    (start_str, end_str, task_id),
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule_plan(
        self,
        plan_id: int,
        preferences: SchedulingPreferences,
        blocked_slots: Optional[list[tuple]] = None,
    ) -> list[ScheduledTask]:
        
        # ------------------------------------------------------------------
        # Step 1: Load the plan and its date
        # ------------------------------------------------------------------
        plan = self.db.get_plan_by_id(plan_id)
        if plan is None:
            raise ValueError(f"No plan found with plan_id {plan_id}.")

        plan_date = self._parse_plan_date(plan)

        # ------------------------------------------------------------------
        # Step 2: Load tasks for this plan
        # ------------------------------------------------------------------
        db_rows = self.db.get_tasks_by_plan(plan_id)
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
        scheduled: list[ScheduledTask] = scheduler.schedule(tasks, plan_date=plan_date)
        self.last_conflicts = getattr(scheduler, "last_conflicts", [])
        self.last_fixed_conflicts = getattr(scheduler, "last_fixed_conflicts", [])

        # ------------------------------------------------------------------
        # Step 5: Persist time slots back to the database
        # ------------------------------------------------------------------
        self._save_schedule(scheduled, plan_id)

        return scheduled

    def schedule_today(
        self,
        user_id: int,
        preferences: SchedulingPreferences,
        blocked_slots: Optional[list[tuple]] = None,
    ) -> list[ScheduledTask]:

        plan = self.db.get_today_plan(user_id)
        if plan is None:
            raise ValueError(f"No plan found for user {user_id} today.")

        plan_id: int = int(plan["plan_id"])
        return self.schedule_plan(plan_id, preferences=preferences, blocked_slots=blocked_slots)