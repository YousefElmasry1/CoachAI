from typing import Optional

from recommendation import RecommendationEngine, RecommendationOutput
from database import Database
from analytics import AnalyticsEngine, AnalyticsFormatter


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Format template for a single scheduled task line.
_TASK_LINE_FORMAT: str = "{start} - {end} | {title} ({minutes} min)"

# How far back to look for coaching context. Time-based rather than a row
# count, so the window reflects a consistent span of long-term behavior
# regardless of how many tasks a user logs per day.
_HISTORY_WINDOW_DAYS: int = 30


# ---------------------------------------------------------------------------
# Recommendation Service
# ---------------------------------------------------------------------------

class RecommendationService:

    def __init__(self, db: Database) -> None:
        """
        Initialise the service with a database connection.

        Args:
            db: An already-connected Database object.
        """
        self.db: Database = db
        self._engine: Optional[RecommendationEngine] = None
        self._analytics_engine: Optional[AnalyticsEngine] = None
        self._analytics_formatter: Optional[AnalyticsFormatter] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_engine(self) -> RecommendationEngine:
        """
        Lazily create the RecommendationEngine instance.

        Returns:
            A ready-to-use RecommendationEngine.
        """
        if self._engine is None:
            self._engine = RecommendationEngine()
        return self._engine

    def _get_analytics_engine(self) -> AnalyticsEngine:
        """
        Lazily create the AnalyticsEngine instance.

        Returns:
            A ready-to-use AnalyticsEngine.
        """
        if self._analytics_engine is None:
            self._analytics_engine = AnalyticsEngine(self.db)
        return self._analytics_engine

    def _get_analytics_formatter(self) -> AnalyticsFormatter:
        """
        Lazily create the AnalyticsFormatter instance.

        Returns:
            A ready-to-use AnalyticsFormatter.
        """
        if self._analytics_formatter is None:
            self._analytics_formatter = AnalyticsFormatter()
        return self._analytics_formatter

    def _row_get(self, row, key: str, default=None):
        """
        Safely retrieve a value from a sqlite3.Row.

        sqlite3.Row does not implement .get(), so we catch KeyError
        and return the default when the column is missing.

        Args:
            row: A sqlite3.Row object.
            key: Column name to look up.
            default: Value to return if the key is absent.

        Returns:
            The row value or default.
        """
        try:
            return row[key]
        except KeyError:
            return default

    def _format_schedule_text(self, tasks: list) -> str:
        """
        Convert database task rows into a human-readable schedule string.

        Args:
            tasks: sqlite3.Row objects from get_tasks_by_plan().

        Returns:
            A multi-line string with one task per line, formatted as:
                "HH:MM - HH:MM | Task Title (duration min)"
        """
        lines: list[str] = []
        for row in tasks:
            start = self._row_get(row, "scheduled_start")
            end = self._row_get(row, "scheduled_end")
            title = self._row_get(row, "title", "Untitled")
            minutes = self._row_get(row, "estimated_minutes", 0)

            # Handle tasks that haven't been scheduled yet
            start_str = str(start) if start else "??:??"
            end_str = str(end) if end else "??:??"

            line = _TASK_LINE_FORMAT.format(
                start=start_str,
                end=end_str,
                title=title,
                minutes=minutes,
            )
            lines.append(line)

        return "\n".join(lines)

    def _format_schedule_text_for_plan(self, plan_id: int) -> str:
        """
        Load tasks for a plan and format them into schedule text.

        Args:
            plan_id: The plan whose tasks to load.

        Returns:
            Formatted schedule text.

        Raises:
            ValueError: If the plan has no tasks.
        """
        tasks = self.db.get_tasks_by_plan(plan_id)
        if not tasks:
            raise ValueError(f"Plan {plan_id} has no tasks to analyze.")
        return self._format_schedule_text(tasks)

    def _generate_recommendations(
        self,
        plan_id: int,
        user_id: int,
    ) -> RecommendationOutput:
        """
        Shared implementation for both public entry points.

        Builds an analytics profile via AnalyticsEngine, converts it to
        LLM-ready context via AnalyticsFormatter, then delegates all
        analysis to the RecommendationEngine.

        Args:
            plan_id: The plan to analyze as "today's schedule".
            user_id: The plan's owner, used to build the analytics profile.

        Returns:
            A RecommendationOutput containing coaching feedback informed
            by both today's schedule and the user's analytics profile.

        Raises:
            ValueError: If the plan has no tasks.
            RuntimeError: If the recommendation engine fails.
        """
        # 1. Format today's schedule
        schedule_text = self._format_schedule_text_for_plan(plan_id)

        # 2. Build analytics profile (single DB load, all metrics computed)
        analytics_engine = self._get_analytics_engine()
        profile = analytics_engine.build_profile(
            user_id=user_id,
            window_days=_HISTORY_WINDOW_DAYS,
        )

        # 3. Convert profile into LLM-ready context
        formatter = self._get_analytics_formatter()
        historical_context = formatter.to_llm_context(profile)

        # 4. Delegate to RecommendationEngine
        engine = self._get_engine()
        return engine.analyze_schedule(
            schedule_text=schedule_text,
            historical_context=historical_context,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recommendations_for_today(
        self,
        user_id: int,
    ) -> RecommendationOutput:
        """
        Load today's scheduled tasks for a user and return AI-generated
        productivity recommendations, informed by their historical
        behavior.

        Args:
            user_id: The user whose day should be analyzed.

        Returns:
            A RecommendationOutput containing summary, strengths,
            weaknesses, and actionable recommendations.

        Raises:
            ValueError: If the user has no plan or no tasks for today.
            RuntimeError: If the recommendation engine fails.
        """
        plan = self.db.get_today_plan(user_id)
        if plan is None:
            raise ValueError(
                f"No plan found for user {user_id} today. "
                "Cannot generate recommendations without a schedule."
            )

        plan_id: int = int(plan["plan_id"])
        return self._generate_recommendations(plan_id=plan_id, user_id=user_id)

    def get_recommendations_for_plan(
        self,
        plan_id: int,
    ) -> RecommendationOutput:
        """
        Load any plan's scheduled tasks and return AI-generated
        recommendations, informed by that plan owner's historical
        behavior.

        Args:
            plan_id: The plan to analyze.

        Returns:
            A RecommendationOutput containing coaching advice.

        Raises:
            ValueError: If the plan does not exist or has no tasks.
            RuntimeError: If the recommendation engine fails.
        """
        plan = self.db.get_plan_by_id(plan_id)
        if plan is None:
            raise ValueError(f"No plan found with plan_id {plan_id}.")

        user_id: int = int(plan["user_id"])
        return self._generate_recommendations(plan_id=plan_id, user_id=user_id)