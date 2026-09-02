"""
CoachAI - Planner Service (Integration Layer)

Bridges the AI Planner (planner.py) with the persistence layer
(database.py). Takes the user's free-form description of their day,
asks the PlannerEngine to split it into structured tasks, resolves each
task's category against the user's existing categories (creating a new
one only when genuinely needed), and persists everything to SQLite.

Usage:
    from planner_service import PlannerService
    from database import Database

    db = Database()
    service = PlannerService(db)

    result = service.generate_and_save_plan(
        raw_input="Study database for 2 hours, gym at 6pm, finish the report",
        user_id=1,
    )
    # result["plan_id"], result["planning_notes"], result["tasks"]
"""

from datetime import date, datetime, timedelta
from typing import Any, Optional

from planner import PlannerEngine, DayPlanOutput, TaskOutput
from database import Database
from config import CHART_COLORS
from text_matching import normalize_for_matching, is_break_term

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fallback color used for a new category if the AI didn't suggest one.
_DEFAULT_CATEGORY_COLOR: str = CHART_COLORS[0]


# ---------------------------------------------------------------------------
# Planner Service
# ---------------------------------------------------------------------------

class PlannerService:
    def __init__(self, db: Database) -> None:
        """
        Initialise the service with a database connection.

        Args:
            db: An already-connected Database object.
        """
        self.db: Database = db
        self._engine: Optional[PlannerEngine] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_engine(self) -> PlannerEngine:
        """
        Lazily create the PlannerEngine instance.

        Returns:
            A ready-to-use PlannerEngine.
        """
        if self._engine is None:
            self._engine = PlannerEngine()
        return self._engine

    def _row_get(self, row, key: str, default=None):
        """
        Safely retrieve a value from a sqlite3.Row.

        Args:
            row: A sqlite3.Row object.
            key: Column name to look up.
            default: Value to return if the key is absent.

        Returns:
            The row value or default.
        """
        try:
            return row[key]
        except (KeyError, IndexError):
            return default

    def _get_or_create_today_plan(self, user_id: int, raw_input: str) -> int:
        """
        Fetch today's plan for the user, creating one if it doesn't exist.

        If a plan already exists for today, new tasks are appended to it
        instead of failing — this lets the user run the Planner more than
        once in the same day (e.g. to add an afternoon update).

        Args:
            user_id: The plan's owner.
            raw_input: The user's free-form text for this planning pass.

        Returns:
            The plan_id to attach new tasks to.
        """
        existing = self.db.get_today_plan(user_id)
        if existing is not None:
            return int(existing["plan_id"])

        return self.db.create_plan(
            user_id=user_id,
            plan_date=date.today(),
            raw_input=raw_input,
        )

    def _load_existing_categories(self, user_id: int) -> dict[str, int]:
        """
        Load the user's existing categories as a normalized lookup.

        Args:
            user_id: The category owner.

        Returns:
            Dict mapping normalize_for_matching(name) -> category_id.
            Normalization (not just .lower()) so Arabic categories that
            differ only by diacritics or alef/teh-marbuta spelling
            variants (e.g. "أعمال" vs "اعمال") are treated as the same
            category rather than silently duplicated.
        """
        rows = self.db.get_categories(user_id)
        return {normalize_for_matching(row["name"]): int(row["category_id"]) for row in rows}

    def _resolve_category_id(
        self,
        task: TaskOutput,
        category_lookup: dict[str, int],
        user_id: int,
    ) -> Optional[int]:
        """
        Resolve a task's category_name to a category_id, creating a new
        category only when it genuinely doesn't exist yet.

        Mutates category_lookup in place so that if the AI proposes the
        same new category for multiple tasks in one plan, it only gets
        created once.

        Args:
            task: The AI-generated task, with category_name and the
                is_new_category / suggested_category_color hints.
            category_lookup: normalize_for_matching(name) -> category_id
                map, pre-loaded from the database.
            user_id: The category owner.

        Returns:
            The resolved category_id, or None if category_name was blank.
        """
        name = (task.category_name or "").strip()
        if not name:
            return None

        key = normalize_for_matching(name)
        if key in category_lookup:
            return category_lookup[key]

        # Not found - create it, regardless of what is_new_category said,
        # since the lookup is the source of truth for what already exists.
        color = task.suggested_category_color or _DEFAULT_CATEGORY_COLOR
        try:
            category_id = self.db.create_category(
                user_id=user_id,
                name=name,
                color=color,
            )
        except Exception:
            # Name collided with an existing category in a race, or an
            # invalid color was supplied — fall back to no category
            # rather than failing the whole plan.
            refreshed = self._load_existing_categories(user_id)
            return refreshed.get(key)

        category_lookup[key] = category_id
        return category_id

    @staticmethod
    def _is_break_task(task: TaskOutput) -> bool:
        """
        Detect whether a drafted task represents a break, so it can be
        flagged is_break=True and get the dedicated Start/countdown UI
        (see render_break_card in 2___Todays_Schedule.py) instead of
        being treated as an ordinary task with Complete/Fail actions.

        The Planner's system prompt (rules 9 and 10 in planner.py)
        instructs the AI to ALWAYS use category_name 'Break' (or its
        Arabic equivalent, when the user is writing in Arabic — see
        is_break_term()) — with or without a concrete clock time — for
        anything the user describes as a break, rest, lunch, or meal
        period. That's the one reliable signal available here; matching
        on the task's title instead would miss AI-generated breaks with
        a different wording, and this must be case/diacritic-insensitive
        since the AI won't always spell it exactly the same way twice.

        Args:
            task: A drafted TaskOutput from the Planner.

        Returns:
            True if this task should be persisted as a break.
        """
        return is_break_term((task.category_name or "").strip())

    @staticmethod
    def _compute_fixed_end(fixed_start: str, estimated_minutes: int) -> Optional[str]:
        """
        Compute a fixed task's end time from its start time and duration.

        Args:
            fixed_start: Start time as 'HH:MM'.
            estimated_minutes: Duration in minutes.

        Returns:
            End time as 'HH:MM', or None if fixed_start couldn't be parsed.
        """
        try:
            hour, minute = (int(p) for p in fixed_start.split(":")[:2])
            start_dt = datetime(2000, 1, 1, hour, minute)
        except (ValueError, AttributeError):
            return None
        end_dt = start_dt + timedelta(minutes=max(estimated_minutes, 0))
        return end_dt.strftime("%H:%M")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def draft_plan(
        self,
        raw_input: str,
        user_id: int,
        calendar_events: Optional[list[dict]] = None,
    ) -> DayPlanOutput:
        """
        Ask the AI Planner to split free-form text into structured tasks
        WITHOUT persisting anything to the database.

        This is the first half of what ``generate_and_save_plan`` used to
        do in one shot. Splitting it out lets a caller inspect the draft
        (e.g. total estimated minutes, for a realistic-capacity check)
        and decide whether to save it, discard it, or let the user
        revise their input and draft again — before anything touches
        the database.

        Args:
            raw_input: The user's free-form description of their day.
            user_id: The plan's owner (used only to look up existing
                categories so the AI can reuse them).

        Returns:
            The raw DayPlanOutput from the Planner engine — not yet
            saved anywhere.

        Raises:
            ValueError: If raw_input is empty.
            RuntimeError: If the Planner engine fails.
        """
        cleaned_input = raw_input.strip()
        if not cleaned_input:
            raise ValueError("raw_input cannot be empty.")

        category_lookup = self._load_existing_categories(user_id)
        engine = self._get_engine()

        return engine.plan_day(
            raw_input=cleaned_input,
            existing_categories=[
                name.title() for name in category_lookup.keys()
            ] if category_lookup else [],
            calendar_events=calendar_events,
        )

    def save_plan(
        self,
        plan_output: DayPlanOutput,
        raw_input: str,
        user_id: int,
    ) -> dict[str, Any]:
        """
        Persist an already-drafted DayPlanOutput (see ``draft_plan``) to
        the database: create/reuse today's plan container, resolve or
        create each task's category, and insert every task.

        Args:
            plan_output: A DayPlanOutput previously returned by
                ``draft_plan`` (or ``PlannerEngine.plan_day`` directly).
            raw_input: The original free-form text, used only when a new
                plan container needs to be created for today.
            user_id: The plan's owner.

        Returns:
            A dict with:
                - plan_id: int
                - planning_notes: str (the AI's brief interpretation note)
                - tasks: list of dicts, each with task_id, title,
                  category_name, is_new_category, needs_review,
                  review_reason, is_fixed_time, is_break.
        """
        cleaned_input = raw_input.strip()

        # 1. Load existing categories for this user (case-insensitive lookup)
        category_lookup = self._load_existing_categories(user_id)

        # 2. Get (or create) today's plan container
        plan_id = self._get_or_create_today_plan(user_id, cleaned_input)

        # 3. Figure out where new tasks should start in display order
        existing_task_count = len(self.db.get_tasks_by_plan(plan_id))

        # 4. Persist each task, resolving/creating categories as we go
        saved_tasks: list[dict[str, Any]] = []
        for offset, task in enumerate(plan_output.tasks):
            category_id = self._resolve_category_id(task, category_lookup, user_id)
            is_break = self._is_break_task(task)

            scheduled_start: Optional[str] = None
            scheduled_end: Optional[str] = None
            if task.is_fixed_time and task.fixed_start:
                scheduled_start = task.fixed_start
                scheduled_end = self._compute_fixed_end(
                    task.fixed_start, task.estimated_minutes
                )

            task_id = self.db.add_task(
                plan_id=plan_id,
                title=task.title,
                category_id=category_id,
                description=task.description or None,
                priority=task.priority,
                estimated_minutes=task.estimated_minutes,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                order_index=existing_task_count + offset,
                is_fixed_time=task.is_fixed_time,
                is_break=is_break,
            )

            saved_tasks.append({
                "task_id": task_id,
                "title": task.title,
                "category_name": task.category_name,
                "is_new_category": task.is_new_category,
                "needs_review": task.needs_review,
                "review_reason": task.review_reason,
                "is_fixed_time": task.is_fixed_time,
                "is_break": is_break,
            })

        return {
            "plan_id": plan_id,
            "planning_notes": plan_output.planning_notes,
            "tasks": saved_tasks,
        }

    def generate_and_save_plan(
        self,
        raw_input: str,
        user_id: int,
    ) -> dict[str, Any]:
        """
        Turn free-form text into structured tasks and persist them in
        one call — convenience wrapper for callers that don't need to
        inspect the draft first (e.g. don't need a capacity check).

        Equivalent to ``save_plan(draft_plan(raw_input, user_id),
        raw_input, user_id)``.

        Args:
            raw_input: The user's free-form description of their day.
            user_id: The plan's owner.

        Returns:
            Same shape as ``save_plan``.

        Raises:
            ValueError: If raw_input is empty.
            RuntimeError: If the Planner engine fails.
        """
        plan_output = self.draft_plan(raw_input=raw_input, user_id=user_id)
        return self.save_plan(
            plan_output=plan_output, raw_input=raw_input, user_id=user_id,
        )