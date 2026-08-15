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

from datetime import date
from typing import Optional

from scheduler import Scheduler, ScheduledTask, SchedulingPreferences
from database import Database


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
            List of ScheduledTask objects with scheduling fields unset.
        """
        tasks: list[ScheduledTask] = []
        for row in rows:
            # sqlite3.Row supports both dict-style and attribute access
            st = ScheduledTask(
                title=str(row["title"]),
                category=str(self._row_get(row, "category", "") or ""),
                estimated_minutes=int(row["estimated_minutes"]),
                priority=int(row["priority"]),
                description=str(self._row_get(row, "description", "") or ""),
                order_index=int(self._row_get(row, "order_index", 0)),
            )
            tasks.append(st)
        return tasks

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