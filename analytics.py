"""
CoachAI – Analytics Engine
==========================

Deterministic analytics layer that computes all behavioural metrics from
historical task data.  No AI, no randomness, no network calls.

Architecture
------------
AnalyticsLoader   → Single database load, normalises rows into TaskSnapshot objects.
IntermediateStats → Immutable shared statistics computed once from snapshots.
*Calculator       → Focused calculators that read IntermediateStats only.
AnalyticsProfile  → Pydantic model carrying the full analytics output.
AnalyticsFormatter→ Converts AnalyticsProfile into various output formats.
AnalyticsEngine   → Orchestrator that wires loader → stats → calculators → profile.

Invariants
----------
- AnalyticsLoader is the ONLY module that touches Database directly.
- No calculator may call Database methods.
- No calculator may mutate IntermediateStats.
- No calculator may depend on another calculator.
- All shared data flows through IntermediateStats.
- Every metric is computed exactly once.
- All calculations are deterministic (no AI).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

from typing import Any, Optional

from pydantic import BaseModel, Field

from database import Database
from insight_templates import (
    render_pattern,
    render_insight,
    render_correlation,
    translate_reason,
    translate_direction,
)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

ANALYTICS_VERSION: str = "1.0.0"
STATISTICS_VERSION: str = "1.0.0"
PROFILE_VERSION: str = "1.0.0"

DEFAULT_WINDOW_DAYS: int = 30

# Minimum sample sizes for confidence thresholds
MIN_TASKS_FOR_BASIC_CONFIDENCE: int = 5
MIN_TASKS_FOR_MEDIUM_CONFIDENCE: int = 15
MIN_TASKS_FOR_HIGH_CONFIDENCE: int = 30
MIN_DAYS_FOR_TREND: int = 7
MIN_DAYS_FOR_HIGH_CONFIDENCE: int = 14

# Productivity score weights — all must sum to 1.0
PRODUCTIVITY_WEIGHTS: dict[str, float] = {
    "completion_rate": 0.20,
    "failure_rate_inverse": 0.10,
    "planning_accuracy": 0.10,
    "avg_delay_penalty": 0.05,
    "consistency": 0.10,
    "current_streak": 0.05,
    "longest_streak": 0.05,
    "high_priority_completion": 0.10,
    "focus_quality": 0.10,
    "task_completion_stability": 0.05,
    "trend_score": 0.10,
}

# Duration analysis buckets (minutes)
DURATION_BUCKETS: list[tuple[int, int]] = [
    (0, 30),
    (31, 60),
    (61, 120),
    (121, 9999),
]

# Habit score decay — how many days of inactivity before habit decays
HABIT_DECAY_DAYS: int = 3

# --- Realistic Capacity ---
# A day counts as "successful" (used to derive the recommended capacity)
# when its completion rate is at least this fraction.
CAPACITY_SUCCESS_DAY_THRESHOLD: float = 0.7

# Minimum distinct days of history required before the capacity estimate
# is considered trustworthy enough to base a recommendation on real data.
CAPACITY_MIN_OBSERVATION_DAYS: int = 5

# Generic fallback used only when there isn't enough history yet.
# Roughly a comfortable half-day of focused work.
CAPACITY_FALLBACK_MINUTES: float = 240.0


# ═══════════════════════════════════════════════════════════════════════════
# TASK SNAPSHOT — Normalised immutable task record
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TaskSnapshot:
    """
    Immutable normalised representation of a task row.

    Definition:
        A frozen snapshot of a single task joined with its plan date
        and category name.  Created once by AnalyticsLoader from a
        database row; never modified afterwards.

    Fields:
        task_id:            Primary key from tasks table.
        plan_date:          ISO date string from the parent plan.
        title:              Task title.
        category_name:      Resolved category name or 'Uncategorized'.
        priority:           1 (highest) to 5 (lowest).
        estimated_minutes:  AI-estimated duration.
        actual_minutes:     User-reported actual duration (None if not set).
        scheduled_start:    HH:MM string or None.
        scheduled_end:      HH:MM string or None.
        status:             'pending', 'in_progress', 'completed', 'failed'.
        failure_reason:     One of the CHECK constraint values, or None.
        completed_at:       ISO datetime string or None.
        created_at:         ISO datetime string.
        weekday:            0=Monday … 6=Sunday, derived from plan_date.
        hour:               Scheduled start hour (0-23), or None.
    """

    task_id: int
    plan_date: str
    title: str
    category_name: str
    priority: int
    estimated_minutes: int
    actual_minutes: Optional[int]
    scheduled_start: Optional[str]
    scheduled_end: Optional[str]
    status: str
    failure_reason: Optional[str]
    completed_at: Optional[str]
    created_at: str
    weekday: int
    hour: Optional[int]


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS LOADER — Single database access point
# ═══════════════════════════════════════════════════════════════════════════

class AnalyticsLoader:
    """
    The ONLY module allowed to access Database directly for analytics.

    Loads all required data in a single call and normalises rows into
    TaskSnapshot objects.  No other analytics code touches the database.

    Definition:
        Responsible for a single SQL query that joins tasks, plans, and
        categories, then maps each row to a frozen TaskSnapshot.

    Formula:
        N/A (data loading, not computation).

    Input Fields:
        user_id, window_days → used to scope the SQL query.

    Output:
        List[TaskSnapshot] — one per task in the window.

    Edge Cases:
        - No tasks → returns empty list.
        - Missing category → 'Uncategorized'.
        - Missing scheduled_start → hour is None.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _safe_get(row, key: str, default=None):
        """Safely retrieve a value from a sqlite3.Row."""
        try:
            value = row[key]
            return value if value is not None else default
        except (KeyError, IndexError):
            return default

    @staticmethod
    def _parse_hour(time_str: Optional[str]) -> Optional[int]:
        """
        Extract hour from an 'HH:MM' string.

        Definition:
            Parses the hour component from a time string.

        Formula:
            int(time_str.split(':')[0])

        Input Fields:
            time_str — 'HH:MM' format string or None.

        Output Range:
            0–23, or None if input is None or unparseable.

        Edge Cases:
            - None → None
            - Malformed string → None
        """
        if not time_str:
            return None
        try:
            return int(str(time_str).split(":")[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_weekday(date_str: str) -> int:
        """
        Derive weekday (0=Monday … 6=Sunday) from an ISO date string.

        Definition:
            Converts an ISO date string to its weekday index.

        Formula:
            date.fromisoformat(date_str).weekday()

        Input Fields:
            date_str — ISO date string (YYYY-MM-DD).

        Output Range:
            0–6.

        Edge Cases:
            - Invalid date → defaults to 0 (Monday).
        """
        try:
            return date.fromisoformat(str(date_str)).weekday()
        except (ValueError, TypeError):
            return 0

    def load_snapshots(
        self,
        user_id: int,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> list[TaskSnapshot]:
        """
        Load all tasks for a user within the analysis window.

        Single database query.  Returns normalised TaskSnapshot objects.

        Definition:
            Joins tasks → plans → categories for a user, scoped to
            the last ``window_days`` days.

        Formula:
            SELECT … WHERE p.user_id = ? AND p.plan_date >= ?

        Input Fields:
            user_id, window_days.

        Output:
            List[TaskSnapshot], newest first by plan_date.

        Edge Cases:
            - No tasks → [].
            - plan_date before window → excluded by SQL.

        Examples:
            >>> loader = AnalyticsLoader(db)
            >>> snaps = loader.load_snapshots(user_id=1, window_days=30)
        """
        since_date = date.today() - timedelta(days=window_days)
        rows = self._db.get_recent_tasks_for_user(
            user_id=user_id,
            since_date=since_date,
        )

        sg = self._safe_get
        snapshots: list[TaskSnapshot] = []
        for row in rows:
            plan_date_val = sg(row, "plan_date", "")
            plan_date_str = str(plan_date_val)
            title_val = sg(row, "title", "Untitled")
            category_val = sg(row, "category_name")
            priority_val = sg(row, "priority", 3)
            estimated_val = sg(row, "estimated_minutes", 0)
            actual_val = sg(row, "actual_minutes")
            scheduled_start_val = sg(row, "scheduled_start")
            scheduled_end_val = sg(row, "scheduled_end")
            status_val = sg(row, "status", "pending")
            failure_reason_val = sg(row, "failure_reason")
            completed_at_val = sg(row, "completed_at")
            created_at_val = sg(row, "created_at", "")

            snapshots.append(TaskSnapshot(
                task_id=int(sg(row, "task_id", 0)),
                plan_date=plan_date_str,
                title=str(title_val),
                category_name=str(category_val or "Uncategorized"),
                priority=int(priority_val),
                estimated_minutes=int(estimated_val),
                actual_minutes=(
                    int(actual_val) if actual_val is not None else None
                ),
                scheduled_start=(
                    str(scheduled_start_val) if scheduled_start_val else None
                ),
                scheduled_end=(
                    str(scheduled_end_val) if scheduled_end_val else None
                ),
                status=str(status_val),
                failure_reason=failure_reason_val,
                completed_at=(
                    str(completed_at_val) if completed_at_val else None
                ),
                created_at=str(created_at_val),
                weekday=AnalyticsLoader._parse_weekday(plan_date_str),
                hour=AnalyticsLoader._parse_hour(
                    str(scheduled_start_val) if scheduled_start_val else None
                ),
            ))

        return snapshots


# ═══════════════════════════════════════════════════════════════════════════
# INTERMEDIATE STATS — Immutable shared statistics
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class IntermediateStats:
    """
    Immutable container of pre-computed intermediate statistics.

    Built once from TaskSnapshot list.  Every calculator reads from this;
    none may mutate it.

    Definition:
        A frozen dataclass holding all shared counters, rates, groupings,
        and distributions needed by downstream calculators.

    Fields — see inline documentation below.
    """

    # --- Core Counts ---
    total_tasks: int
    total_completed: int
    total_failed: int
    total_pending: int
    total_in_progress: int

    # --- Rates ---
    completion_rate: float          # total_completed / total_tasks, 0.0–1.0
    failure_rate: float             # total_failed / total_tasks, 0.0–1.0

    # --- Duration Stats ---
    total_estimated_minutes: int
    total_actual_minutes: int       # sum of non-None actual_minutes
    tasks_with_actual: int          # count of tasks that have actual_minutes
    avg_estimated_minutes: float
    avg_actual_minutes: float       # average over tasks_with_actual only

    # --- Planning ---
    planning_errors: list[float]    # (actual - estimated) / estimated per task
    avg_planning_error: float       # mean of planning_errors
    planning_accuracy: float        # 1.0 - abs(avg_planning_error), clamped 0–1

    # --- Delay ---
    delays: list[float]             # max(0, actual - estimated) per task
    avg_delay_minutes: float
    max_delay_minutes: float

    # --- Time Groupings ---
    tasks_by_hour: dict[int, list[TaskSnapshot]]
    tasks_by_weekday: dict[int, list[TaskSnapshot]]
    tasks_by_date: dict[str, list[TaskSnapshot]]
    tasks_by_category: dict[str, list[TaskSnapshot]]
    tasks_by_priority: dict[int, list[TaskSnapshot]]

    # --- Status Sublists ---
    completed_tasks: list[TaskSnapshot]
    failed_tasks: list[TaskSnapshot]

    # --- Failure Distribution ---
    failure_reason_counts: dict[str, int]
    main_failure_reason: Optional[str]

    # --- Date Coverage ---
    unique_dates: list[str]         # sorted ascending
    observation_days: int           # len(unique_dates)
    window_days: int                # the configured window size

    # --- Duration Buckets ---
    tasks_by_duration_bucket: dict[str, list[TaskSnapshot]]

    # --- Weekday / Weekend ---
    weekday_tasks: list[TaskSnapshot]   # Monday–Friday
    weekend_tasks: list[TaskSnapshot]   # Saturday–Sunday

    # --- Precomputed Shared Metrics ---
    current_streak: int
    longest_streak: int
    trend_score: float              # 0.0–1.0, 0.5 = stable
    active_days: int                # days with >= 1 completed task

    # --- All snapshots (for calculators that need raw iteration) ---
    all_snapshots: tuple[TaskSnapshot, ...]


def _compute_streaks_from_dates(
    unique_dates: list[str],
    tasks_by_date: dict[str, list[TaskSnapshot]],
) -> tuple[int, int]:
    """
    Compute current and longest streaks.

    Definition:
        A streak day is a date where >= 1 task was completed.
        Consecutive streak days form a streak.

    Formula:
        Walk dates in ascending order.  If a date is active, increment
        streak; else reset.  Current streak = streak at the end.

    Input Fields:
        unique_dates — sorted ascending date strings.
        tasks_by_date — date to task list mapping.

    Output:
        (current_streak, longest_streak).

    Edge Cases:
        - No dates → (0, 0).
        - All dates active → current = longest = observation_days.
    """
    if not unique_dates:
        return (0, 0)

    # Build set of active dates (dates with >= 1 completed task)
    active_dates: set[str] = set()
    for dt, tasks in tasks_by_date.items():
        if any(t.status == "completed" for t in tasks):
            active_dates.add(dt)

    if not active_dates:
        return (0, 0)

    try:
        first_date = date.fromisoformat(unique_dates[0])
        last_date = date.fromisoformat(unique_dates[-1])
    except (ValueError, TypeError):
        return (0, 0)

    current = 0
    longest = 0
    d = first_date
    while d <= last_date:
        if d.isoformat() in active_dates:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
        d += timedelta(days=1)

    # If today is beyond last_date by more than 1 day, streak is broken
    today = date.today()
    if last_date < today:
        gap = (today - last_date).days
        if gap > 1:
            current = 0

    return (current, longest)


def _compute_trend_score_from_dates(
    unique_dates: list[str],
    tasks_by_date: dict[str, list[TaskSnapshot]],
    observation_days: int,
) -> float:
    """
    Compute a normalised trend score (0.0–1.0, 0.5 = stable).

    Definition:
        Compares average daily completion rate in the first half vs
        second half of the observation window.

    Formula:
        diff = second_half_rate - first_half_rate
        trend_score = (diff + 1) / 2   (clamped 0–1)

    Input Fields:
        unique_dates — sorted ascending date strings.
        tasks_by_date — date to task list mapping.
        observation_days — number of unique dates.

    Output Range:
        0.0–1.0.  0.5 = stable, > 0.5 = improving, < 0.5 = declining.

    Edge Cases:
        - < MIN_DAYS_FOR_TREND → 0.5.
    """
    if observation_days < MIN_DAYS_FOR_TREND:
        return 0.5

    mid = len(unique_dates) // 2
    first_half = set(unique_dates[:mid])
    second_half = set(unique_dates[mid:])

    def avg_rate(date_set: set) -> float:
        rates = []
        for dt in date_set:
            tasks = tasks_by_date.get(dt, [])
            total = len(tasks)
            if total > 0:
                done = sum(1 for t in tasks if t.status == "completed")
                rates.append(done / total)
        return sum(rates) / len(rates) if rates else 0.0

    first_rate = avg_rate(first_half)
    second_rate = avg_rate(second_half)
    diff = second_rate - first_rate

    return max(0.0, min(1.0, (diff + 1.0) / 2.0))


def build_intermediate_stats(
    snapshots: list[TaskSnapshot],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> IntermediateStats:
    """
    Build IntermediateStats from a list of TaskSnapshots.

    Performs a single O(n) pass over the data to compute all shared
    statistics.  Every downstream calculator reads from this; none
    recompute these values.

    Definition:
        Factory function that aggregates TaskSnapshot data into an
        immutable IntermediateStats instance.

    Formula:
        Single-pass aggregation with counters, sums, and groupings.

    Input Fields:
        snapshots — list of TaskSnapshot from AnalyticsLoader.
        window_days — the configured analysis window.

    Output:
        IntermediateStats (frozen dataclass).

    Edge Cases:
        - Empty snapshots → all zeroes, empty collections.
        - No completed tasks → completion_rate = 0.0.
        - No tasks with actual_minutes → avg_actual_minutes = 0.0.
        - Division by zero guarded everywhere.

    Examples:
        >>> stats = build_intermediate_stats(snapshots, window_days=30)
        >>> stats.completion_rate
        0.75
    """
    total = len(snapshots)

    completed: list[TaskSnapshot] = []
    failed: list[TaskSnapshot] = []
    pending_count = 0
    in_progress_count = 0

    total_estimated = 0
    total_actual = 0
    tasks_with_actual = 0

    planning_errors: list[float] = []
    delays: list[float] = []

    by_hour: dict[int, list[TaskSnapshot]] = defaultdict(list)
    by_weekday: dict[int, list[TaskSnapshot]] = defaultdict(list)
    by_date: dict[str, list[TaskSnapshot]] = defaultdict(list)
    by_category: dict[str, list[TaskSnapshot]] = defaultdict(list)
    by_priority: dict[int, list[TaskSnapshot]] = defaultdict(list)

    failure_reasons: list[str] = []

    weekday_tasks: list[TaskSnapshot] = []
    weekend_tasks: list[TaskSnapshot] = []

    by_duration_bucket: dict[str, list[TaskSnapshot]] = defaultdict(list)

    for snap in snapshots:
        # --- Status counts ---
        if snap.status == "completed":
            completed.append(snap)
        elif snap.status == "failed":
            failed.append(snap)
        elif snap.status == "pending":
            pending_count += 1
        elif snap.status == "in_progress":
            in_progress_count += 1

        # --- Duration aggregation ---
        total_estimated += snap.estimated_minutes
        if snap.actual_minutes is not None:
            total_actual += snap.actual_minutes
            tasks_with_actual += 1

            # Planning error: (actual - estimated) / estimated
            if snap.estimated_minutes > 0:
                error = (snap.actual_minutes - snap.estimated_minutes) / snap.estimated_minutes
                planning_errors.append(error)

            # Delay: max(0, actual - estimated)
            delay = max(0.0, snap.actual_minutes - snap.estimated_minutes)
            delays.append(delay)

        # --- Time groupings ---
        if snap.hour is not None:
            by_hour[snap.hour].append(snap)
        by_weekday[snap.weekday].append(snap)
        by_date[snap.plan_date].append(snap)
        by_category[snap.category_name].append(snap)
        by_priority[snap.priority].append(snap)

        # --- Failure reasons ---
        if snap.failure_reason:
            failure_reasons.append(snap.failure_reason)

        # --- Weekday vs Weekend ---
        if snap.weekday < 5:
            weekday_tasks.append(snap)
        else:
            weekend_tasks.append(snap)

        # --- Duration buckets ---
        for low, high in DURATION_BUCKETS:
            if low <= snap.estimated_minutes <= high:
                label = f"{low}-{high}" if high < 9999 else f"{low}+"
                by_duration_bucket[label].append(snap)
                break

    # --- Derived rates ---
    completion_rate = len(completed) / total if total > 0 else 0.0
    failure_rate = len(failed) / total if total > 0 else 0.0

    avg_estimated = total_estimated / total if total > 0 else 0.0
    avg_actual = total_actual / tasks_with_actual if tasks_with_actual > 0 else 0.0

    avg_planning_error = (
        sum(planning_errors) / len(planning_errors)
        if planning_errors else 0.0
    )
    planning_accuracy = max(0.0, min(1.0, 1.0 - abs(avg_planning_error)))

    avg_delay = sum(delays) / len(delays) if delays else 0.0
    max_delay = max(delays) if delays else 0.0

    # --- Failure distribution ---
    failure_counter = Counter(failure_reasons)
    main_failure = (
        failure_counter.most_common(1)[0][0]
        if failure_counter else None
    )

    # --- Date coverage ---
    unique_dates_sorted = sorted(set(snap.plan_date for snap in snapshots))
    observation_days = len(unique_dates_sorted)

    # --- Finalised groupings ---
    by_date_dict = dict(by_date)

    # --- Active days: days with >= 1 completed task ---
    active_days = sum(
        1 for tasks in by_date_dict.values()
        if any(t.status == "completed" for t in tasks)
    )

    # --- Streaks (computed once, shared by all calculators) ---
    current_streak, longest_streak = _compute_streaks_from_dates(
        unique_dates_sorted, by_date_dict,
    )

    # --- Trend score (computed once, shared by all calculators) ---
    trend_score = _compute_trend_score_from_dates(
        unique_dates_sorted, by_date_dict, observation_days,
    )

    return IntermediateStats(
        total_tasks=total,
        total_completed=len(completed),
        total_failed=len(failed),
        total_pending=pending_count,
        total_in_progress=in_progress_count,
        completion_rate=completion_rate,
        failure_rate=failure_rate,
        total_estimated_minutes=total_estimated,
        total_actual_minutes=total_actual,
        tasks_with_actual=tasks_with_actual,
        avg_estimated_minutes=avg_estimated,
        avg_actual_minutes=avg_actual,
        planning_errors=planning_errors,
        avg_planning_error=avg_planning_error,
        planning_accuracy=planning_accuracy,
        delays=delays,
        avg_delay_minutes=avg_delay,
        max_delay_minutes=max_delay,
        tasks_by_hour=dict(by_hour),
        tasks_by_weekday=dict(by_weekday),
        tasks_by_date=by_date_dict,
        tasks_by_category=dict(by_category),
        tasks_by_priority=dict(by_priority),
        completed_tasks=completed,
        failed_tasks=failed,
        failure_reason_counts=dict(failure_counter),
        main_failure_reason=main_failure,
        unique_dates=unique_dates_sorted,
        observation_days=observation_days,
        window_days=window_days,
        tasks_by_duration_bucket=dict(by_duration_bucket),
        weekday_tasks=weekday_tasks,
        weekend_tasks=weekend_tasks,
        current_streak=current_streak,
        longest_streak=longest_streak,
        trend_score=trend_score,
        active_days=active_days,
        all_snapshots=tuple(snapshots),
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONFIDENCE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class ConfidenceLevel(str, Enum):
    """
    Qualitative confidence label for a metric.

    Definition:
        An enum representing how trustworthy a metric is based on
        the sample size and observation coverage.

    Values:
        insufficient → too few observations to report.
        low          → preliminary estimate.
        medium       → reasonable estimate.
        high         → statistically robust.
    """
    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MetricConfidence(BaseModel):
    """
    Confidence metadata attached to an important metric.

    Definition:
        Carries the sample size, observation window, and qualitative
        confidence label for any metric that exposes confidence.

    Fields:
        level:            ConfidenceLevel enum value.
        sample_size:      Number of data points used.
        observation_days: Number of distinct days observed.
        insufficient_data: True when sample is too small.
    """
    level: str = Field(
        default="insufficient",
        description="Qualitative confidence: insufficient, low, medium, high.",
    )
    sample_size: int = Field(
        default=0,
        description="Number of data points underlying this metric.",
    )
    observation_days: int = Field(
        default=0,
        description="Number of distinct days with observations.",
    )
    insufficient_data: bool = Field(
        default=True,
        description="True when the sample is too small to trust the metric.",
    )


def compute_confidence(
    sample_size: int,
    observation_days: int,
) -> MetricConfidence:
    """
    Compute a confidence assessment for a metric.

    Definition:
        Maps sample_size and observation_days to a qualitative
        confidence level.

    Formula:
        if sample_size < MIN_TASKS_FOR_BASIC_CONFIDENCE → insufficient
        elif sample_size < MIN_TASKS_FOR_MEDIUM_CONFIDENCE → low
        elif sample_size < MIN_TASKS_FOR_HIGH_CONFIDENCE
             or observation_days < MIN_DAYS_FOR_HIGH_CONFIDENCE → medium
        else → high

    Input Fields:
        sample_size — number of observations.
        observation_days — number of distinct days.

    Output Range:
        MetricConfidence with level in {insufficient, low, medium, high}.

    Edge Cases:
        - sample_size=0 → insufficient, insufficient_data=True.
        - sample_size=5, observation_days=1 → low.

    Examples:
        >>> compute_confidence(50, 20)
        MetricConfidence(level='high', sample_size=50, ...)
    """
    if sample_size < MIN_TASKS_FOR_BASIC_CONFIDENCE:
        return MetricConfidence(
            level=ConfidenceLevel.INSUFFICIENT.value,
            sample_size=sample_size,
            observation_days=observation_days,
            insufficient_data=True,
        )
    if sample_size < MIN_TASKS_FOR_MEDIUM_CONFIDENCE:
        return MetricConfidence(
            level=ConfidenceLevel.LOW.value,
            sample_size=sample_size,
            observation_days=observation_days,
            insufficient_data=False,
        )
    if (
        sample_size < MIN_TASKS_FOR_HIGH_CONFIDENCE
        or observation_days < MIN_DAYS_FOR_HIGH_CONFIDENCE
    ):
        return MetricConfidence(
            level=ConfidenceLevel.MEDIUM.value,
            sample_size=sample_size,
            observation_days=observation_days,
            insufficient_data=False,
        )
    return MetricConfidence(
        level=ConfidenceLevel.HIGH.value,
        sample_size=sample_size,
        observation_days=observation_days,
        insufficient_data=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS — Calculator Outputs
# ═══════════════════════════════════════════════════════════════════════════

class ProductivityResult(BaseModel):
    """Weighted composite productivity score with component breakdown."""
    score: float = Field(
        default=0.0,
        description=(
            "Composite productivity score, 0–100.  "
            "Weighted sum of normalised components."
        ),
    )
    components: dict[str, float] = Field(
        default_factory=dict,
        description="Individual normalised component scores (0.0–1.0 each).",
    )
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Weights used for each component.",
    )
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class PriorityAnalysis(BaseModel):
    """Per-priority breakdown of completion, failure, delay, risk."""
    priority: int = Field(description="Priority level (1–5).")
    total: int = 0
    completed: int = 0
    failed: int = 0
    completion_rate: float = 0.0
    failure_rate: float = 0.0
    avg_delay: float = 0.0
    avg_actual_duration: float = 0.0
    planning_error: float = 0.0
    risk_score: float = Field(
        default=0.0,
        description=(
            "Risk score = failure_rate × (1 + normalised_delay).  "
            "Higher means this priority level is more problematic."
        ),
    )


class PriorityResult(BaseModel):
    """Full priority analysis across all levels."""
    per_priority: list[PriorityAnalysis] = Field(default_factory=list)
    highest_completed_priority: Optional[int] = None
    highest_failed_priority: Optional[int] = None
    priority_trend: str = Field(
        default="stable",
        description="'improving', 'declining', or 'stable'.",
    )
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class PlanningResult(BaseModel):
    """Planning accuracy and bias detection."""
    planning_accuracy: float = Field(
        default=0.0,
        description="1.0 - abs(avg_planning_error), clamped 0–1.",
    )
    avg_planning_error: float = 0.0
    overestimation_rate: float = Field(
        default=0.0,
        description="Fraction of tasks where estimated > actual.",
    )
    underestimation_rate: float = Field(
        default=0.0,
        description="Fraction of tasks where estimated < actual.",
    )
    bias_direction: str = Field(
        default="neutral",
        description="'overestimation', 'underestimation', or 'neutral'.",
    )
    bias_severity: float = Field(
        default=0.0,
        description="Magnitude of the average planning error (abs value).",
    )
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class CategoryAnalysis(BaseModel):
    """Per-category performance metrics."""
    category: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    completion_rate: float = 0.0
    failure_rate: float = 0.0
    avg_delay: float = 0.0
    trend: str = "stable"
    habit_score: float = Field(
        default=0.0,
        description="0–100 habit consistency score for this category.",
    )


class CategoryResult(BaseModel):
    """Full category analysis."""
    per_category: list[CategoryAnalysis] = Field(default_factory=list)
    favorite_category: Optional[str] = None
    weakest_category: Optional[str] = None
    strongest_category: Optional[str] = None
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class FailureResult(BaseModel):
    """Failure reason distribution and top reason."""
    failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    main_failure_reason: Optional[str] = None
    total_failed: int = 0
    failure_rate: float = 0.0


class ConsistencyResult(BaseModel):
    """Streak and consistency metrics."""
    current_streak: int = 0
    longest_streak: int = 0
    consistency_score: float = Field(
        default=0.0,
        description=(
            "Fraction of observed days where the user completed at "
            "least one task.  0.0–1.0."
        ),
    )
    active_days: int = 0
    total_observation_days: int = 0


class TrendResult(BaseModel):
    """Trend detection over the observation window."""
    trend_direction: str = Field(
        default="stable",
        description="'improving', 'declining', or 'stable'.",
    )
    trend_score: float = Field(
        default=0.5,
        description="0.0–1.0 normalised trend.  0.5 = stable.",
    )
    daily_completion_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Date → completion_rate for each observed day.",
    )
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class PatternEntry(BaseModel):
    """A single detected behavioural pattern."""
    pattern_name: str
    pattern_type: str = ""
    observation: str
    evidence: str
    confidence: str = "low"
    observation_window: int = 0
    affected_categories: list[str] = Field(default_factory=list)
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)


class PatternResult(BaseModel):
    """All detected patterns."""
    patterns: list[PatternEntry] = Field(default_factory=list)


class InsightEntry(BaseModel):
    """A single actionable insight (distinct from patterns and recommendations)."""
    insight_type: str = ""
    observation: str
    evidence: str
    confidence: str = "low"


class InsightResult(BaseModel):
    """All generated insights."""
    insights: list[InsightEntry] = Field(default_factory=list)


class CorrelationEntry(BaseModel):
    """A single deterministic correlation."""
    name: str
    value: float = Field(
        description=(
            "Correlation coefficient or comparison ratio.  "
            "Positive = positive association."
        ),
    )
    description: str = ""


class CorrelationResult(BaseModel):
    """All computed correlations."""
    correlations: list[CorrelationEntry] = Field(default_factory=list)


class HeatmapCell(BaseModel):
    """A single cell in a heatmap grid."""
    x: int = Field(description="X-axis value (e.g. hour).")
    y: int | str = Field(description="Y-axis value (e.g. weekday or category).")
    value: float = Field(description="Metric value for this cell.")
    count: int = Field(
        default=0,
        description="Number of observations in this cell.",
    )


class HeatmapData(BaseModel):
    """A complete heatmap grid."""
    name: str
    x_label: str
    y_label: str
    cells: list[HeatmapCell] = Field(default_factory=list)


class HeatmapResult(BaseModel):
    """All generated heatmaps."""
    heatmaps: list[HeatmapData] = Field(default_factory=list)


class DurationBucketAnalysis(BaseModel):
    """Analysis for a single duration bucket."""
    bucket: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    completion_rate: float = 0.0
    failure_rate: float = 0.0
    planning_accuracy: float = 0.0
    avg_delay: float = 0.0
    focus_quality: float = 0.0


class DurationResult(BaseModel):
    """Full duration bucket analysis."""
    buckets: list[DurationBucketAnalysis] = Field(default_factory=list)
    best_duration: Optional[str] = None
    worst_duration: Optional[str] = None


class WeekdayWeekendComparison(BaseModel):
    """Weekday vs weekend comparison metrics."""
    weekday_completion_rate: float = 0.0
    weekend_completion_rate: float = 0.0
    weekday_failure_rate: float = 0.0
    weekend_failure_rate: float = 0.0
    weekday_avg_delay: float = 0.0
    weekend_avg_delay: float = 0.0
    weekday_focus: float = 0.0
    weekend_focus: float = 0.0
    weekday_productivity: float = 0.0
    weekend_productivity: float = 0.0
    weekday_count: int = 0
    weekend_count: int = 0
    stronger_period: str = Field(
        default="equal",
        description="'weekday', 'weekend', or 'equal'.",
    )


class HabitScores(BaseModel):
    """Overall and per-habit scores."""
    overall_habit_score: float = Field(
        default=0.0,
        description="Overall habit consistency, 0–100.",
    )
    study_habit: float = 0.0
    workout_habit: float = 0.0
    reading_habit: float = 0.0
    morning_habit: float = 0.0
    evening_habit: float = 0.0
    category_habit_scores: dict[str, float] = Field(default_factory=dict)


class BurnoutAnalysis(BaseModel):
    """Burnout risk and related sustainability metrics."""
    burnout_risk: float = Field(
        default=0.0,
        description="0–100 burnout risk score.  Higher = more at risk.",
    )
    planning_reliability: float = 0.0
    schedule_density: float = Field(
        default=0.0,
        description=(
            "Average total estimated minutes per day.  "
            "High density signals potential overload."
        ),
    )
    recovery_time: float = Field(
        default=0.0,
        description="Average gap (minutes) between tasks in a day.",
    )
    deep_work_score: float = Field(
        default=0.0,
        description=(
            "Fraction of tasks >= 60 min that were completed.  "
            "0.0–1.0."
        ),
    )
    context_switching_score: float = Field(
        default=0.0,
        description=(
            "Average number of category switches per day.  "
            "Higher = more switching."
        ),
    )
    time_fragmentation: float = Field(
        default=0.0,
        description=(
            "Fraction of tasks <= 15 min.  Higher = more fragmented."
        ),
    )
    habit_stability: float = Field(
        default=0.0,
        description="Stability of daily task count.  0.0–1.0 (1.0 = perfectly stable).",
    )


class BestHourResult(BaseModel):
    """Best productivity hour with confidence."""
    best_hour: Optional[int] = None
    completion_rate_at_best: float = 0.0
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


class CapacityResult(BaseModel):
    """
    Realistic daily capacity — how much a user can actually plan in a
    single day and still finish most of it, derived from their own
    history rather than from what they merely intend.

    This is deliberately distinct from ``BurnoutAnalysis.schedule_density``
    (a plain average of planned minutes/day, regardless of outcome).
    Capacity instead looks at *which* days actually went well and asks
    "how much did I plan on those days?" — so a user who habitually
    over-plans and under-delivers doesn't get their bad habit reflected
    back as their "capacity".
    """
    recommended_daily_minutes: float = Field(
        default=CAPACITY_FALLBACK_MINUTES,
        description=(
            "Recommended total planned minutes for a single day, based "
            "on the days this user historically completed most of what "
            "they planned. Falls back to a generic default when there "
            "isn't enough history yet (see basis)."
        ),
    )
    light_day_completion_rate: float = Field(
        default=0.0,
        description=(
            "Average completion rate on days planned at or below the "
            "recommended capacity."
        ),
    )
    heavy_day_completion_rate: float = Field(
        default=0.0,
        description=(
            "Average completion rate on days planned above the "
            "recommended capacity. Meaningfully lower than "
            "light_day_completion_rate is the evidence that overloading "
            "actually hurts this specific user."
        ),
    )
    successful_day_count: int = Field(
        default=0,
        description=(
            "Number of observed days with completion_rate >= "
            "CAPACITY_SUCCESS_DAY_THRESHOLD, used to derive the "
            "recommendation when basis='historical_success_days'."
        ),
    )
    sample_days: int = Field(
        default=0,
        description="Total distinct days observed in the window.",
    )
    basis: str = Field(
        default="insufficient_data",
        description=(
            "How recommended_daily_minutes was derived: "
            "'historical_success_days' (enough clearly-successful days "
            "to trust), 'overall_median' (some history, but not enough "
            "clearly-successful days — median of all observed days "
            "used instead), or 'insufficient_data' (fallback default)."
        ),
    )
    confidence: MetricConfidence = Field(default_factory=MetricConfidence)


# ═══════════════════════════════════════════════════════════════════════════
# CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════

# Each calculator:
# - Receives IntermediateStats ONLY (no Database, no other calculator).
# - Returns a typed result model.
# - Never mutates IntermediateStats.
# - Has no side effects.


class ProductivityCalculator:
    """
    Computes a configurable weighted composite productivity score.

    Definition:
        Combines multiple normalised sub-metrics (each 0.0–1.0) into
        a single 0–100 score using configurable weights.

    Formula:
        score = 100 × Σ(weight_i × component_i)
        where each component_i is normalised to 0.0–1.0.

    Input Fields:
        IntermediateStats — completion_rate, failure_rate,
        planning_accuracy, avg_delay_minutes, current_streak,
        longest_streak, trend_score, active_days, etc.

    Output Range:
        ProductivityResult with score in 0.0–100.0.

    Edge Cases:
        - No tasks → score = 0, insufficient confidence.
        - No tasks with actual_minutes → planning/delay components = 0.
        - All weights zero → score = 0 (degenerate but handled).

    Examples:
        >>> calc = ProductivityCalculator()
        >>> result = calc.compute(stats)
        >>> 0 <= result.score <= 100
        True
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
    ) -> None:
        self._weights = weights or PRODUCTIVITY_WEIGHTS.copy()

    def _normalise_delay(self, avg_delay: float) -> float:
        """
        Convert average delay to a 0–1 score where 1.0 = no delay.

        Formula: max(0, 1.0 - avg_delay / 60.0)
        """
        return max(0.0, 1.0 - avg_delay / 60.0)

    def _normalise_streak(self, streak: int, max_possible: int) -> float:
        """
        Normalise a streak value to 0–1.

        Formula: min(1.0, streak / max(1, max_possible))
        """
        return min(1.0, streak / max(1, max_possible))

    def _compute_focus_quality(self, stats: IntermediateStats) -> float:
        """
        Focus quality: fraction of completed tasks that had actual_minutes
        within ±25% of estimated_minutes.

        Definition:
            Measures how well the user's actual work time matches
            their estimated time — a proxy for focus and planning.

        Formula:
            focused_count / total_with_actual
            where focused = abs(actual - estimated) / estimated <= 0.25

        Input Fields:
            IntermediateStats.all_snapshots, .tasks_with_actual.

        Output Range:
            0.0–1.0.

        Edge Cases:
            - No tasks with actual_minutes → 0.0.
            - estimated_minutes = 0 → task excluded.
        """
        if stats.tasks_with_actual == 0:
            return 0.0

        focused = 0
        total_measured = 0
        for snap in stats.all_snapshots:
            if snap.actual_minutes is not None and snap.estimated_minutes > 0:
                total_measured += 1
                error = abs(snap.actual_minutes - snap.estimated_minutes) / snap.estimated_minutes
                if error <= 0.25:
                    focused += 1

        return focused / total_measured if total_measured > 0 else 0.0

    def _compute_task_stability(self, stats: IntermediateStats) -> float:
        """
        Task completion stability: 1 - CV of daily completion rates.

        Definition:
            Coefficient of variation of daily completion rates,
            inverted so 1.0 = perfectly stable.

        Formula:
            stability = 1.0 - min(1.0, stdev(daily_rates) / mean(daily_rates))

        Output Range: 0.0–1.0.
        Edge Cases: < 2 days → 1.0 (assume stable).
        """
        if stats.observation_days < 2:
            return 1.0

        daily_rates: list[float] = []
        for tasks in stats.tasks_by_date.values():
            total = len(tasks)
            done = sum(1 for t in tasks if t.status == "completed")
            daily_rates.append(done / total if total > 0 else 0.0)

        if len(daily_rates) < 2:
            return 1.0

        mean_rate = sum(daily_rates) / len(daily_rates)
        if mean_rate == 0.0:
            return 0.0

        stdev = statistics.stdev(daily_rates)
        cv = stdev / mean_rate
        return max(0.0, 1.0 - min(1.0, cv))

    def _compute_high_priority_completion(
        self, stats: IntermediateStats,
    ) -> float:
        """
        Completion rate of high-priority tasks (priority 1 or 2).

        Formula: completed_hp / total_hp
        Output Range: 0.0–1.0.
        Edge Cases: No HP tasks → 0.0.
        """
        hp_tasks = []
        for p in [1, 2]:
            hp_tasks.extend(stats.tasks_by_priority.get(p, []))

        if not hp_tasks:
            return 0.0

        done = sum(1 for t in hp_tasks if t.status == "completed")
        return done / len(hp_tasks)

    def compute(self, stats: IntermediateStats) -> ProductivityResult:
        """Compute the weighted composite productivity score."""
        if stats.total_tasks == 0:
            return ProductivityResult(
                score=0.0,
                components={},
                weights=self._weights,
                confidence=compute_confidence(0, 0),
            )

        # All shared metrics consumed from IntermediateStats
        focus_quality = self._compute_focus_quality(stats)
        stability = self._compute_task_stability(stats)
        hp_completion = self._compute_high_priority_completion(stats)

        components: dict[str, float] = {
            "completion_rate": stats.completion_rate,
            "failure_rate_inverse": 1.0 - stats.failure_rate,
            "planning_accuracy": stats.planning_accuracy,
            "avg_delay_penalty": self._normalise_delay(stats.avg_delay_minutes),
            "consistency": stats.active_days / max(1, stats.window_days),
            "current_streak": self._normalise_streak(stats.current_streak, stats.window_days),
            "longest_streak": self._normalise_streak(stats.longest_streak, stats.window_days),
            "high_priority_completion": hp_completion,
            "focus_quality": focus_quality,
            "task_completion_stability": stability,
            "trend_score": stats.trend_score,
        }

        score = 0.0
        for key, weight in self._weights.items():
            score += weight * components.get(key, 0.0)

        score = max(0.0, min(100.0, score * 100.0))

        return ProductivityResult(
            score=round(score, 2),
            components={k: round(v, 4) for k, v in components.items()},
            weights=self._weights,
            confidence=compute_confidence(
                stats.total_tasks, stats.observation_days,
            ),
        )


class PriorityCalculator:
    """
    Per-priority performance analysis.

    Definition:
        Breaks down completion, failure, delay, and risk metrics for
        each priority level (1–5).

    Formula:
        Per priority p:
            completion_rate_p = completed_p / total_p
            failure_rate_p    = failed_p / total_p
            risk_score_p      = failure_rate_p × (1 + normalised_delay_p)

    Input Fields:
        IntermediateStats.tasks_by_priority.

    Output:
        PriorityResult with per_priority list.

    Edge Cases:
        - Priority level has 0 tasks → all zeros.
        - No tasks at all → empty per_priority, None highest_*.
    """

    def compute(self, stats: IntermediateStats) -> PriorityResult:
        if stats.total_tasks == 0:
            return PriorityResult(
                confidence=compute_confidence(0, 0),
            )

        analyses: list[PriorityAnalysis] = []
        highest_completed: Optional[int] = None
        highest_failed: Optional[int] = None

        for p in range(1, 6):
            tasks = stats.tasks_by_priority.get(p, [])
            total = len(tasks)
            if total == 0:
                analyses.append(PriorityAnalysis(priority=p))
                continue

            done = sum(1 for t in tasks if t.status == "completed")
            fail = sum(1 for t in tasks if t.status == "failed")
            comp_rate = done / total
            fail_rate = fail / total

            # Delay for this priority
            p_delays = []
            p_actuals = []
            p_errors = []
            for t in tasks:
                if t.actual_minutes is not None:
                    p_actuals.append(t.actual_minutes)
                    delay = max(0.0, t.actual_minutes - t.estimated_minutes)
                    p_delays.append(delay)
                    if t.estimated_minutes > 0:
                        error = (
                            (t.actual_minutes - t.estimated_minutes)
                            / t.estimated_minutes
                        )
                        p_errors.append(error)

            avg_delay_p = sum(p_delays) / len(p_delays) if p_delays else 0.0
            avg_actual_p = sum(p_actuals) / len(p_actuals) if p_actuals else 0.0
            planning_error_p = (
                sum(p_errors) / len(p_errors) if p_errors else 0.0
            )

            normalised_delay = min(1.0, avg_delay_p / 60.0)
            risk = fail_rate * (1.0 + normalised_delay)

            analyses.append(PriorityAnalysis(
                priority=p,
                total=total,
                completed=done,
                failed=fail,
                completion_rate=round(comp_rate, 4),
                failure_rate=round(fail_rate, 4),
                avg_delay=round(avg_delay_p, 2),
                avg_actual_duration=round(avg_actual_p, 2),
                planning_error=round(planning_error_p, 4),
                risk_score=round(risk, 4),
            ))

            # Track highest completed / failed (priority 1 = highest)
            if done > 0:
                if highest_completed is None or p < highest_completed:
                    highest_completed = p
            if fail > 0:
                if highest_failed is None or p < highest_failed:
                    highest_failed = p

        # Priority trend: compare first-half vs second-half HP completion
        priority_trend = self._compute_priority_trend(stats)

        return PriorityResult(
            per_priority=analyses,
            highest_completed_priority=highest_completed,
            highest_failed_priority=highest_failed,
            priority_trend=priority_trend,
            confidence=compute_confidence(
                stats.total_tasks, stats.observation_days,
            ),
        )

    @staticmethod
    def _compute_priority_trend(stats: IntermediateStats) -> str:
        """Compare high-priority completion in first vs second half of window."""
        if stats.observation_days < MIN_DAYS_FOR_TREND:
            return "stable"

        dates = stats.unique_dates
        mid = len(dates) // 2
        first_half_dates = set(dates[:mid])
        second_half_dates = set(dates[mid:])

        def hp_rate(date_set: set) -> float:
            hp = [
                t for t in stats.all_snapshots
                if t.priority <= 2 and t.plan_date in date_set
            ]
            if not hp:
                return 0.0
            return sum(1 for t in hp if t.status == "completed") / len(hp)

        first_rate = hp_rate(first_half_dates)
        second_rate = hp_rate(second_half_dates)

        diff = second_rate - first_rate
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        return "stable"


class PlanningCalculator:
    """
    Planning accuracy and bias detection.

    Definition:
        Analyses the gap between estimated and actual minutes to detect
        systematic over- or under-estimation.

    Formula:
        planning_accuracy = 1.0 - abs(avg_planning_error)
        overestimation_rate = count(estimated > actual) / total_with_actual
        underestimation_rate = count(estimated < actual) / total_with_actual
        bias_direction = 'overestimation' if avg_error < -0.1 else
                         'underestimation' if avg_error > 0.1 else 'neutral'
        bias_severity = abs(avg_planning_error)

    Input Fields:
        IntermediateStats.planning_errors, .tasks_with_actual,
        .planning_accuracy, .avg_planning_error.

    Output Range:
        PlanningResult with values in documented ranges.

    Edge Cases:
        - No tasks with actual_minutes → neutral bias, accuracy 0.0.
        - All perfect estimates → accuracy 1.0, neutral.
    """

    def compute(self, stats: IntermediateStats) -> PlanningResult:
        if stats.tasks_with_actual == 0:
            return PlanningResult(
                confidence=compute_confidence(0, stats.observation_days),
            )

        over = sum(1 for e in stats.planning_errors if e < 0)
        under = sum(1 for e in stats.planning_errors if e > 0)
        total = len(stats.planning_errors)

        over_rate = over / total if total > 0 else 0.0
        under_rate = under / total if total > 0 else 0.0

        avg_err = stats.avg_planning_error
        if avg_err < -0.1:
            direction = "overestimation"
        elif avg_err > 0.1:
            direction = "underestimation"
        else:
            direction = "neutral"

        return PlanningResult(
            planning_accuracy=round(stats.planning_accuracy, 4),
            avg_planning_error=round(avg_err, 4),
            overestimation_rate=round(over_rate, 4),
            underestimation_rate=round(under_rate, 4),
            bias_direction=direction,
            bias_severity=round(abs(avg_err), 4),
            confidence=compute_confidence(
                stats.tasks_with_actual, stats.observation_days,
            ),
        )


class CategoryCalculator:
    """
    Per-category performance analysis.

    Definition:
        Computes completion, failure, delay, trend, and habit score
        for each category the user has used.

    Formula:
        Per category c:
            completion_rate_c = completed_c / total_c
            failure_rate_c    = failed_c / total_c
            habit_score_c     = 100 × (frequency × recency × completion_rate_c)

    Input Fields:
        IntermediateStats.tasks_by_category, .unique_dates.

    Output:
        CategoryResult with per_category list.

    Edge Cases:
        - No tasks → empty per_category.
        - Single-category user → that category is both strongest & favorite.
    """

    def compute(self, stats: IntermediateStats) -> CategoryResult:
        if stats.total_tasks == 0:
            return CategoryResult(
                confidence=compute_confidence(0, 0),
            )

        analyses: list[CategoryAnalysis] = []
        best_rate = -1.0
        worst_rate = 2.0
        best_cat: Optional[str] = None
        worst_cat: Optional[str] = None
        most_tasks = 0
        favorite: Optional[str] = None

        for cat, tasks in stats.tasks_by_category.items():
            total = len(tasks)
            done = sum(1 for t in tasks if t.status == "completed")
            fail = sum(1 for t in tasks if t.status == "failed")
            comp_rate = done / total if total > 0 else 0.0
            fail_rate = fail / total if total > 0 else 0.0

            # Delay
            cat_delays = [
                max(0.0, t.actual_minutes - t.estimated_minutes)
                for t in tasks
                if t.actual_minutes is not None
            ]
            avg_delay = (
                sum(cat_delays) / len(cat_delays) if cat_delays else 0.0
            )

            # Trend per category
            trend = self._category_trend(tasks, stats.unique_dates)

            # Habit score
            habit = self._category_habit_score(
                tasks, stats.unique_dates, comp_rate,
            )

            analyses.append(CategoryAnalysis(
                category=cat,
                total=total,
                completed=done,
                failed=fail,
                completion_rate=round(comp_rate, 4),
                failure_rate=round(fail_rate, 4),
                avg_delay=round(avg_delay, 2),
                trend=trend,
                habit_score=round(habit, 2),
            ))

            if total > most_tasks:
                most_tasks = total
                favorite = cat
            if comp_rate > best_rate:
                best_rate = comp_rate
                best_cat = cat
            if comp_rate < worst_rate:
                worst_rate = comp_rate
                worst_cat = cat

        return CategoryResult(
            per_category=analyses,
            favorite_category=favorite,
            weakest_category=worst_cat,
            strongest_category=best_cat,
            confidence=compute_confidence(
                stats.total_tasks, stats.observation_days,
            ),
        )

    @staticmethod
    def _category_trend(
        tasks: list[TaskSnapshot],
        all_dates: list[str],
    ) -> str:
        """Compare completion rate in first vs second half."""
        if len(all_dates) < MIN_DAYS_FOR_TREND:
            return "stable"
        mid = len(all_dates) // 2
        first_set = set(all_dates[:mid])
        second_set = set(all_dates[mid:])

        def rate(date_set: set) -> float:
            subset = [t for t in tasks if t.plan_date in date_set]
            if not subset:
                return 0.0
            return sum(1 for t in subset if t.status == "completed") / len(subset)

        diff = rate(second_set) - rate(first_set)
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        return "stable"

    @staticmethod
    def _category_habit_score(
        tasks: list[TaskSnapshot],
        all_dates: list[str],
        completion_rate: float,
    ) -> float:
        """
        Habit score = 100 × frequency × recency × completion_rate.

        frequency = unique_dates_with_category / observation_days
        recency   = 1.0 if last use within HABIT_DECAY_DAYS, else decayed
        """
        if not all_dates or not tasks:
            return 0.0

        cat_dates = set(t.plan_date for t in tasks)
        frequency = len(cat_dates) / len(all_dates)

        # Recency: days since last use
        last_date_str = max(cat_dates)
        try:
            last_date = date.fromisoformat(last_date_str)
            days_since = (date.today() - last_date).days
        except (ValueError, TypeError):
            days_since = 999

        if days_since <= HABIT_DECAY_DAYS:
            recency = 1.0
        else:
            recency = max(0.0, 1.0 - (days_since - HABIT_DECAY_DAYS) / 30.0)

        return 100.0 * frequency * recency * completion_rate


class FailureCalculator:
    """
    Failure reason distribution analysis.

    Definition:
        Reports failure_reason counts and the dominant reason.

    Formula:
        failure_reason_counts = Counter of failure_reason values.
        main_failure_reason = mode of failure_reason.

    Input Fields:
        IntermediateStats.failure_reason_counts, .main_failure_reason,
        .total_failed, .failure_rate.

    Output: FailureResult.
    Edge Cases: No failures → empty dict, None main reason.
    """

    def compute(self, stats: IntermediateStats) -> FailureResult:
        return FailureResult(
            failure_reason_counts=stats.failure_reason_counts,
            main_failure_reason=stats.main_failure_reason,
            total_failed=stats.total_failed,
            failure_rate=round(stats.failure_rate, 4),
        )


class ConsistencyCalculator:
    """
    Streak and daily consistency analysis.

    Definition:
        Reads precomputed current_streak, longest_streak, and active_days
        from IntermediateStats.  Computes consistency_score as the ratio
        of active_days to observation_days.

    Formula:
        consistency_score = active_days / observation_days

    Input Fields:
        IntermediateStats.current_streak, .longest_streak, .active_days,
        .observation_days.

    Output: ConsistencyResult.
    Edge Cases: No dates → all zeros.
    """

    def compute(self, stats: IntermediateStats) -> ConsistencyResult:
        if stats.observation_days == 0:
            return ConsistencyResult()

        consistency = stats.active_days / stats.observation_days

        return ConsistencyResult(
            current_streak=stats.current_streak,
            longest_streak=stats.longest_streak,
            consistency_score=round(consistency, 4),
            active_days=stats.active_days,
            total_observation_days=stats.observation_days,
        )


class TrendCalculator:
    """
    Trend detection over the observation window.

    Definition:
        Reads precomputed trend_score from IntermediateStats and
        produces daily completion rates plus trend direction.

    Formula:
        trend_direction = 'improving' if diff > 0.05 else
                          'declining' if diff < -0.05 else 'stable'
        where diff = (trend_score - 0.5) * 2

    Input Fields:
        IntermediateStats.trend_score, .tasks_by_date, .observation_days.

    Output: TrendResult.
    Edge Cases: < MIN_DAYS_FOR_TREND → stable, score = 0.5.
    """

    def compute(self, stats: IntermediateStats) -> TrendResult:
        daily_rates: dict[str, float] = {}
        for dt, tasks in stats.tasks_by_date.items():
            total = len(tasks)
            done = sum(1 for t in tasks if t.status == "completed")
            daily_rates[dt] = round(done / total, 4) if total > 0 else 0.0

        if stats.observation_days < MIN_DAYS_FOR_TREND:
            return TrendResult(
                trend_direction="stable",
                trend_score=0.5,
                daily_completion_rates=daily_rates,
                confidence=compute_confidence(
                    stats.total_tasks, stats.observation_days,
                ),
            )

        diff = (stats.trend_score - 0.5) * 2  # map back to -1..1
        if diff > 0.05:
            direction = "improving"
        elif diff < -0.05:
            direction = "declining"
        else:
            direction = "stable"

        return TrendResult(
            trend_direction=direction,
            trend_score=round(stats.trend_score, 4),
            daily_completion_rates=daily_rates,
            confidence=compute_confidence(
                stats.total_tasks, stats.observation_days,
            ),
        )


class PatternCalculator:
    """
    Deterministic behavioural pattern detection.

    Definition:
        Scans IntermediateStats for recurring, evidence-backed patterns
        such as time-of-day clustering, category-failure clustering,
        and failure-reason clustering.

    Formula:
        Each pattern is detected by comparing rates/counts against
        thresholds.  No ML or AI — purely rule-based.

    Input Fields:
        IntermediateStats (various groupings).

    Output: PatternResult.
    Edge Cases: Insufficient data → no patterns emitted.
    """

    def compute(self, stats: IntermediateStats, language: str = "en") -> PatternResult:
        patterns: list[PatternEntry] = []

        if stats.total_tasks < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return PatternResult(patterns=[])

        self._detect_failure_time_cluster(stats, patterns, language)
        self._detect_category_failure_pattern(stats, patterns, language)
        self._detect_failure_reason_pattern(stats, patterns, language)
        self._detect_high_priority_struggle(stats, patterns, language)
        self._detect_late_day_overload(stats, patterns, language)
        self._detect_duration_sweet_spot(stats, patterns, language)

        return PatternResult(patterns=patterns)

    def _detect_failure_time_cluster(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect if failures cluster around specific hours."""
        if stats.total_failed < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return

        hour_failure: dict[int, int] = defaultdict(int)
        for t in stats.failed_tasks:
            if t.hour is not None:
                hour_failure[t.hour] += 1

        if not hour_failure:
            return

        worst_hour = max(hour_failure, key=hour_failure.get)
        worst_count = hour_failure[worst_hour]
        total_at_hour = len(stats.tasks_by_hour.get(worst_hour, []))

        if total_at_hour < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return

        fail_rate_at_hour = worst_count / total_at_hour
        if fail_rate_at_hour > stats.failure_rate * 1.5 and worst_count >= 3:
            conf = compute_confidence(total_at_hour, stats.observation_days)
            data = {
                "hour": worst_hour,
                "failure_rate_at_hour": round(fail_rate_at_hour, 4),
                "overall_failure_rate": round(stats.failure_rate, 4),
                "worst_count": worst_count,
                "total_at_hour": total_at_hour,
            }
            observation, evidence = render_pattern("failure_time_cluster", data, language)
            patterns.append(PatternEntry(
                pattern_name="Failure Time Cluster",
                pattern_type="failure_time_cluster",
                observation=observation,
                evidence=evidence,
                confidence=conf.level,
                observation_window=stats.window_days,
                supporting_metrics=data,
            ))

    def _detect_category_failure_pattern(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect categories with disproportionately high failure rates."""
        for cat, tasks in stats.tasks_by_category.items():
            if len(tasks) < MIN_TASKS_FOR_BASIC_CONFIDENCE:
                continue

            fail_count = sum(1 for t in tasks if t.status == "failed")
            cat_fail_rate = fail_count / len(tasks)

            if cat_fail_rate > stats.failure_rate * 1.5 and fail_count >= 3:
                conf = compute_confidence(len(tasks), stats.observation_days)
                data = {
                    "category": cat,
                    "category_failure_rate": round(cat_fail_rate, 4),
                    "overall_failure_rate": round(stats.failure_rate, 4),
                    "fail_count": fail_count,
                    "total_tasks": len(tasks),
                }
                observation, evidence = render_pattern("category_failure_pattern", data, language)
                patterns.append(PatternEntry(
                    pattern_name="Category Failure Pattern",
                    pattern_type="category_failure_pattern",
                    observation=observation,
                    evidence=evidence,
                    confidence=conf.level,
                    observation_window=stats.window_days,
                    affected_categories=[cat],
                    supporting_metrics=data,
                ))

    def _detect_failure_reason_pattern(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect dominant failure reasons."""
        if stats.total_failed < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return

        for reason, count in stats.failure_reason_counts.items():
            ratio = count / stats.total_failed
            if ratio >= 0.4 and count >= 3:
                conf = compute_confidence(
                    stats.total_failed, stats.observation_days,
                )
                data = {
                    "reason": translate_reason(reason, language),
                    "count": count,
                    "total_failed": stats.total_failed,
                    "ratio": round(ratio, 4),
                }
                observation, evidence = render_pattern("dominant_failure_reason", data, language)
                patterns.append(PatternEntry(
                    pattern_name="Dominant Failure Reason",
                    pattern_type="dominant_failure_reason",
                    observation=observation,
                    evidence=evidence,
                    confidence=conf.level,
                    observation_window=stats.window_days,
                    supporting_metrics={**data, "reason": reason},
                ))

    def _detect_high_priority_struggle(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect struggling with high-priority tasks."""
        hp_tasks = []
        for p in [1, 2]:
            hp_tasks.extend(stats.tasks_by_priority.get(p, []))

        if len(hp_tasks) < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return

        hp_fail = sum(1 for t in hp_tasks if t.status == "failed")
        hp_fail_rate = hp_fail / len(hp_tasks)

        if hp_fail_rate > 0.3 and hp_fail >= 3:
            conf = compute_confidence(len(hp_tasks), stats.observation_days)
            data = {
                "hp_failure_rate": round(hp_fail_rate, 4),
                "hp_fail": hp_fail,
                "hp_total": len(hp_tasks),
            }
            observation, evidence = render_pattern("high_priority_struggle", data, language)
            patterns.append(PatternEntry(
                pattern_name="High Priority Struggle",
                pattern_type="high_priority_struggle",
                observation=observation,
                evidence=evidence,
                confidence=conf.level,
                observation_window=stats.window_days,
                supporting_metrics=data,
            ))

    def _detect_late_day_overload(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect if afternoon/evening tasks have significantly higher failure."""
        afternoon_tasks = []
        for h in range(14, 24):
            afternoon_tasks.extend(stats.tasks_by_hour.get(h, []))

        if len(afternoon_tasks) < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return

        pm_fail = sum(1 for t in afternoon_tasks if t.status == "failed")
        pm_fail_rate = pm_fail / len(afternoon_tasks)

        if pm_fail_rate > stats.failure_rate * 1.3 and pm_fail >= 3:
            conf = compute_confidence(
                len(afternoon_tasks), stats.observation_days,
            )
            data = {
                "afternoon_failure_rate": round(pm_fail_rate, 4),
                "overall_failure_rate": round(stats.failure_rate, 4),
                "pm_fail": pm_fail,
                "afternoon_total": len(afternoon_tasks),
            }
            observation, evidence = render_pattern("late_day_overload", data, language)
            patterns.append(PatternEntry(
                pattern_name="Late Day Overload",
                pattern_type="late_day_overload",
                observation=observation,
                evidence=evidence,
                confidence=conf.level,
                observation_window=stats.window_days,
                supporting_metrics=data,
            ))

    def _detect_duration_sweet_spot(
        self,
        stats: IntermediateStats,
        patterns: list[PatternEntry],
        language: str = "en",
    ) -> None:
        """Detect duration ranges with notably better completion."""
        best_bucket: Optional[str] = None
        best_rate = 0.0

        for bucket, tasks in stats.tasks_by_duration_bucket.items():
            if len(tasks) < MIN_TASKS_FOR_BASIC_CONFIDENCE:
                continue
            done = sum(1 for t in tasks if t.status == "completed")
            rate = done / len(tasks)
            if rate > best_rate:
                best_rate = rate
                best_bucket = bucket

        if (
            best_bucket
            and best_rate > stats.completion_rate + 0.1
            and best_rate > 0.5
        ):
            bucket_total = len(stats.tasks_by_duration_bucket.get(best_bucket, []))
            conf = compute_confidence(bucket_total, stats.observation_days)
            data = {
                "bucket": best_bucket,
                "bucket_completion_rate": round(best_rate, 4),
                "overall_completion_rate": round(stats.completion_rate, 4),
                "bucket_total": bucket_total,
            }
            observation, evidence = render_pattern("duration_sweet_spot", data, language)
            patterns.append(PatternEntry(
                pattern_name="Duration Sweet Spot",
                pattern_type="duration_sweet_spot",
                observation=observation,
                evidence=evidence,
                confidence=conf.level,
                observation_window=stats.window_days,
                supporting_metrics=data,
            ))


class InsightCalculator:
    """
    Generates actionable insights distinct from patterns.

    Definition:
        Patterns detect *what* is happening.
        Insights explain *why it matters*.
        Recommendations (from the LLM) tell *what to do*.

    Insights are generated from IntermediateStats without duplication
    of pattern detection logic.

    Output: InsightResult.
    Edge Cases: Insufficient data → no insights emitted.
    """

    def compute(self, stats: IntermediateStats, language: str = "en") -> InsightResult:
        insights: list[InsightEntry] = []

        if stats.total_tasks < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return InsightResult(insights=[])

        self._insight_completion_rate(stats, insights, language)
        self._insight_planning_bias(stats, insights, language)
        self._insight_consistency(stats, insights, language)
        self._insight_category_diversity(stats, insights, language)
        self._insight_failure_concentration(stats, insights, language)

        return InsightResult(insights=insights)

    def _insight_completion_rate(
        self, stats: IntermediateStats, insights: list[InsightEntry], language: str = "en",
    ) -> None:
        """Insight about overall completion rate significance."""
        if stats.completion_rate >= 0.8:
            data = {
                "completion_rate": round(stats.completion_rate, 4),
                "total_completed": stats.total_completed,
                "total_tasks": stats.total_tasks,
            }
            observation, evidence = render_insight("completion_rate_excellent", data, language)
            insights.append(InsightEntry(
                insight_type="completion_rate_excellent",
                observation=observation,
                evidence=evidence,
                confidence=compute_confidence(
                    stats.total_tasks, stats.observation_days,
                ).level,
            ))
        elif stats.completion_rate < 0.5 and stats.total_tasks >= MIN_TASKS_FOR_BASIC_CONFIDENCE:
            data = {
                "completion_rate": round(stats.completion_rate, 4),
                "total_completed": stats.total_completed,
                "total_tasks": stats.total_tasks,
            }
            observation, evidence = render_insight("completion_rate_low", data, language)
            insights.append(InsightEntry(
                insight_type="completion_rate_low",
                observation=observation,
                evidence=evidence,
                confidence=compute_confidence(
                    stats.total_tasks, stats.observation_days,
                ).level,
            ))

    def _insight_planning_bias(
        self, stats: IntermediateStats, insights: list[InsightEntry], language: str = "en",
    ) -> None:
        """Insight about planning bias significance."""
        if abs(stats.avg_planning_error) > 0.3 and stats.tasks_with_actual >= MIN_TASKS_FOR_BASIC_CONFIDENCE:
            raw_direction = "underestimates" if stats.avg_planning_error > 0 else "overestimates"
            data = {
                "direction": translate_direction(raw_direction, language),
                "planning_error": round(abs(stats.avg_planning_error), 4),
                "avg_planning_error": round(stats.avg_planning_error, 4),
                "tasks_with_actual": stats.tasks_with_actual,
            }
            observation, evidence = render_insight("planning_bias", data, language)
            insights.append(InsightEntry(
                insight_type="planning_bias",
                observation=observation,
                evidence=evidence,
                confidence=compute_confidence(
                    stats.tasks_with_actual, stats.observation_days,
                ).level,
            ))

    def _insight_consistency(
        self, stats: IntermediateStats, insights: list[InsightEntry], language: str = "en",
    ) -> None:
        """Insight about consistency significance."""
        if stats.observation_days > 0:
            consistency = stats.active_days / stats.observation_days
            if consistency >= 0.85:
                data = {
                    "consistency": round(consistency, 4),
                    "active_days": stats.active_days,
                    "observation_days": stats.observation_days,
                }
                observation, evidence = render_insight("consistency_strong", data, language)
                insights.append(InsightEntry(
                    insight_type="consistency_strong",
                    observation=observation,
                    evidence=evidence,
                    confidence=compute_confidence(
                        stats.total_tasks, stats.observation_days,
                    ).level,
                ))

    def _insight_category_diversity(
        self, stats: IntermediateStats, insights: list[InsightEntry], language: str = "en",
    ) -> None:
        """Insight about category diversity."""
        num_categories = len(stats.tasks_by_category)
        if num_categories >= 4:
            data = {
                "num_categories": num_categories,
                "categories_list": ", ".join(stats.tasks_by_category.keys()),
            }
            observation, evidence = render_insight("category_diversity", data, language)
            insights.append(InsightEntry(
                insight_type="category_diversity",
                observation=observation,
                evidence=evidence,
                confidence=compute_confidence(
                    stats.total_tasks, stats.observation_days,
                ).level,
            ))

    def _insight_failure_concentration(
        self, stats: IntermediateStats, insights: list[InsightEntry], language: str = "en",
    ) -> None:
        """Insight about failure concentration in specific areas."""
        if not stats.failure_reason_counts:
            return

        total_reasons = sum(stats.failure_reason_counts.values())
        top_reason, top_count = max(
            stats.failure_reason_counts.items(), key=lambda x: x[1],
        )
        concentration = top_count / total_reasons if total_reasons > 0 else 0

        if concentration >= 0.6 and top_count >= 3:
            data = {
                "top_reason": translate_reason(top_reason, language),
                "top_count": top_count,
                "total_reasons": total_reasons,
                "concentration": round(concentration, 4),
            }
            observation, evidence = render_insight("failure_concentration", data, language)
            insights.append(InsightEntry(
                insight_type="failure_concentration",
                observation=observation,
                evidence=evidence,
                confidence=compute_confidence(
                    stats.total_failed, stats.observation_days,
                ).level,
            ))


class CorrelationCalculator:
    """
    Deterministic correlation analysis.

    Definition:
        Computes simple associations between task attributes and
        outcomes using rate comparisons and point-biserial style
        measures.

    Formula:
        For each pair (X, Y):
            Split tasks by X values, compute Y rate for each group,
            report the spread.

    Input Fields:
        IntermediateStats.tasks_by_priority, .tasks_by_duration_bucket,
        .all_snapshots.

    Output: CorrelationResult.
    Edge Cases: Insufficient data → correlation = 0.
    """

    def compute(self, stats: IntermediateStats, language: str = "en") -> CorrelationResult:
        correlations: list[CorrelationEntry] = []

        if stats.total_tasks < MIN_TASKS_FOR_BASIC_CONFIDENCE:
            return CorrelationResult(correlations=[])

        self._priority_vs_completion(stats, correlations, language)
        self._priority_vs_failure(stats, correlations, language)
        self._duration_vs_completion(stats, correlations, language)
        self._duration_vs_failure(stats, correlations, language)
        self._delay_vs_failure(stats, correlations, language)
        self._category_vs_delay(stats, correlations, language)

        return CorrelationResult(correlations=correlations)

    def _priority_vs_completion(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Higher priority (lower number) → higher completion?"""
        rates = []
        for p in range(1, 6):
            tasks = stats.tasks_by_priority.get(p, [])
            if tasks:
                rate = sum(1 for t in tasks if t.status == "completed") / len(tasks)
                rates.append((p, rate))

        if len(rates) < 2:
            out.append(CorrelationEntry(
                name="priority_vs_completion", value=0.0,
                description=render_correlation("priority_vs_completion_insufficient", {}, language),
            ))
            return

        # Simple direction: is P1 rate > P5 rate?
        direction = rates[0][1] - rates[-1][1]
        data = {
            "top_priority": rates[0][0], "top_rate": rates[0][1],
            "bottom_priority": rates[-1][0], "bottom_rate": rates[-1][1],
        }
        out.append(CorrelationEntry(
            name="priority_vs_completion",
            value=round(direction, 4),
            description=render_correlation("priority_vs_completion", data, language),
        ))

    def _priority_vs_failure(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Higher priority → higher failure?"""
        rates = []
        for p in range(1, 6):
            tasks = stats.tasks_by_priority.get(p, [])
            if tasks:
                rate = sum(1 for t in tasks if t.status == "failed") / len(tasks)
                rates.append((p, rate))

        if len(rates) < 2:
            out.append(CorrelationEntry(
                name="priority_vs_failure", value=0.0,
                description=render_correlation("priority_vs_failure_insufficient", {}, language),
            ))
            return

        direction = rates[0][1] - rates[-1][1]
        data = {
            "top_priority": rates[0][0], "top_rate": rates[0][1],
            "bottom_priority": rates[-1][0], "bottom_rate": rates[-1][1],
        }
        out.append(CorrelationEntry(
            name="priority_vs_failure",
            value=round(direction, 4),
            description=render_correlation("priority_vs_failure", data, language),
        ))

    def _duration_vs_completion(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Short tasks vs long tasks — completion rate."""
        bucket_rates: dict[str, float] = {}
        for bucket, tasks in stats.tasks_by_duration_bucket.items():
            if tasks:
                rate = sum(1 for t in tasks if t.status == "completed") / len(tasks)
                bucket_rates[bucket] = rate

        if len(bucket_rates) < 2:
            out.append(CorrelationEntry(
                name="duration_vs_completion", value=0.0,
                description=render_correlation("duration_vs_completion_insufficient", {}, language),
            ))
            return

        sorted_rates = sorted(bucket_rates.items())
        direction = sorted_rates[0][1] - sorted_rates[-1][1]
        data = {"shortest_rate": sorted_rates[0][1], "longest_rate": sorted_rates[-1][1]}
        out.append(CorrelationEntry(
            name="duration_vs_completion",
            value=round(direction, 4),
            description=render_correlation("duration_vs_completion", data, language),
        ))

    def _duration_vs_failure(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Short tasks vs long tasks — failure rate."""
        bucket_rates: dict[str, float] = {}
        for bucket, tasks in stats.tasks_by_duration_bucket.items():
            if tasks:
                rate = sum(1 for t in tasks if t.status == "failed") / len(tasks)
                bucket_rates[bucket] = rate

        if len(bucket_rates) < 2:
            out.append(CorrelationEntry(
                name="duration_vs_failure", value=0.0,
                description=render_correlation("duration_vs_failure_insufficient", {}, language),
            ))
            return

        sorted_rates = sorted(bucket_rates.items())
        direction = sorted_rates[-1][1] - sorted_rates[0][1]
        data = {"shortest_rate": sorted_rates[0][1], "longest_rate": sorted_rates[-1][1]}
        out.append(CorrelationEntry(
            name="duration_vs_failure",
            value=round(direction, 4),
            description=render_correlation("duration_vs_failure", data, language),
        ))

    def _delay_vs_failure(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Do tasks with delays fail more?"""
        delayed = [
            t for t in stats.all_snapshots
            if t.actual_minutes is not None
            and t.actual_minutes > t.estimated_minutes
        ]
        non_delayed = [
            t for t in stats.all_snapshots
            if t.actual_minutes is not None
            and t.actual_minutes <= t.estimated_minutes
        ]

        if not delayed or not non_delayed:
            out.append(CorrelationEntry(
                name="delay_vs_failure", value=0.0,
                description=render_correlation("delay_vs_failure_insufficient", {}, language),
            ))
            return

        delayed_fail = sum(1 for t in delayed if t.status == "failed") / len(delayed)
        non_delayed_fail = sum(1 for t in non_delayed if t.status == "failed") / len(non_delayed)

        data = {"delayed_fail": delayed_fail, "non_delayed_fail": non_delayed_fail}
        out.append(CorrelationEntry(
            name="delay_vs_failure",
            value=round(delayed_fail - non_delayed_fail, 4),
            description=render_correlation("delay_vs_failure", data, language),
        ))

    def _category_vs_delay(
        self, stats: IntermediateStats, out: list[CorrelationEntry], language: str = "en",
    ) -> None:
        """Average delay per category — max spread."""
        cat_delays: dict[str, float] = {}
        for cat, tasks in stats.tasks_by_category.items():
            delays = [
                max(0.0, t.actual_minutes - t.estimated_minutes)
                for t in tasks
                if t.actual_minutes is not None
            ]
            if delays:
                cat_delays[cat] = sum(delays) / len(delays)

        if len(cat_delays) < 2:
            out.append(CorrelationEntry(
                name="category_vs_delay", value=0.0,
                description=render_correlation("category_vs_delay_insufficient", {}, language),
            ))
            return

        max_delay_cat = max(cat_delays, key=cat_delays.get)
        min_delay_cat = min(cat_delays, key=cat_delays.get)
        spread = cat_delays[max_delay_cat] - cat_delays[min_delay_cat]

        data = {
            "max_delay_cat": max_delay_cat, "max_delay_value": cat_delays[max_delay_cat],
            "min_delay_cat": min_delay_cat, "min_delay_value": cat_delays[min_delay_cat],
        }
        out.append(CorrelationEntry(
            name="category_vs_delay",
            value=round(spread, 2),
            description=render_correlation("category_vs_delay", data, language),
        ))


class HeatmapCalculator:
    """
    Generates reusable heatmap grids for dashboard visualisation.

    Definition:
        Produces Hour×Weekday, Hour×Failure, Hour×Category heatmaps
        using completion rates and counts from IntermediateStats.

    Output: HeatmapResult.
    Edge Cases: No tasks with hour data → empty heatmaps.
    """

    WEEKDAY_NAMES = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    ]

    def compute(self, stats: IntermediateStats) -> HeatmapResult:
        heatmaps: list[HeatmapData] = []

        self._hour_weekday_heatmap(stats, heatmaps)
        self._hour_failure_heatmap(stats, heatmaps)
        self._hour_category_heatmap(stats, heatmaps)

        return HeatmapResult(heatmaps=heatmaps)

    def _hour_weekday_heatmap(
        self, stats: IntermediateStats, out: list[HeatmapData],
    ) -> None:
        """Hour × Weekday completion rate heatmap — O(n) implementation."""
        # Pre-group: build (hour, weekday) → (completed, total) counters
        counts: dict[tuple[int, int], list[int]] = {}
        for h in range(24):
            for wd in range(7):
                counts[(h, wd)] = [0, 0]  # [completed, total]

        for snap in stats.all_snapshots:
            if snap.hour is not None:
                key = (snap.hour, snap.weekday)
                counts[key][1] += 1
                if snap.status == "completed":
                    counts[key][0] += 1

        cells: list[HeatmapCell] = []
        for h in range(24):
            for wd in range(7):
                completed, total = counts[(h, wd)]
                rate = completed / total if total > 0 else 0.0
                cells.append(HeatmapCell(
                    x=h, y=self.WEEKDAY_NAMES[wd],
                    value=round(rate, 4), count=total,
                ))

        out.append(HeatmapData(
            name="hour_weekday_completion",
            x_label="Hour",
            y_label="Weekday",
            cells=cells,
        ))

    def _hour_failure_heatmap(
        self, stats: IntermediateStats, out: list[HeatmapData],
    ) -> None:
        """Hour × Failure count heatmap."""
        cells: list[HeatmapCell] = []
        for h in range(24):
            tasks = stats.tasks_by_hour.get(h, [])
            fail_count = sum(1 for t in tasks if t.status == "failed")
            total = len(tasks)
            rate = fail_count / total if total > 0 else 0.0
            cells.append(HeatmapCell(
                x=h, y="failures",
                value=round(rate, 4), count=fail_count,
            ))

        out.append(HeatmapData(
            name="hour_failure",
            x_label="Hour",
            y_label="Failures",
            cells=cells,
        ))

    def _hour_category_heatmap(
        self, stats: IntermediateStats, out: list[HeatmapData],
    ) -> None:
        """Hour × Category task count heatmap — O(n) implementation."""
        categories = list(stats.tasks_by_category.keys())

        # Pre-group: build (hour, category) → count
        counts: dict[tuple[int, str], int] = {}
        for h in range(24):
            for cat in categories:
                counts[(h, cat)] = 0

        for snap in stats.all_snapshots:
            if snap.hour is not None and snap.category_name in stats.tasks_by_category:
                key = (snap.hour, snap.category_name)
                if key in counts:
                    counts[key] += 1

        cells: list[HeatmapCell] = []
        for h in range(24):
            for cat in categories:
                count = counts[(h, cat)]
                cells.append(HeatmapCell(
                    x=h, y=cat, value=float(count), count=count,
                ))

        out.append(HeatmapData(
            name="hour_category",
            x_label="Hour",
            y_label="Category",
            cells=cells,
        ))


# ═══════════════════════════════════════════════════════════════════════════
# ADDITIONAL CALCULATORS (Burnout, Habits, Duration, Weekday/Weekend, BestHour)
# ═══════════════════════════════════════════════════════════════════════════

class BurnoutCalculator:
    """
    Burnout risk and sustainability metrics.

    Definition:
        Combines schedule density, failure rate, planning unreliability,
        context switching, and time fragmentation into a burnout risk
        score.

    Formula:
        burnout_risk = 100 × mean(
            normalised_density,
            failure_rate,
            1 - planning_reliability,
            normalised_switching,
            time_fragmentation,
            1 - habit_stability,
        )

    Input Fields:
        IntermediateStats — various.

    Output Range:
        BurnoutAnalysis with burnout_risk 0–100.

    Edge Cases:
        - No tasks → burnout_risk = 0.
        - Single day → limited metrics.
    """

    def compute(self, stats: IntermediateStats) -> BurnoutAnalysis:
        if stats.total_tasks == 0:
            return BurnoutAnalysis()

        # Schedule density: avg total estimated minutes per day
        daily_load: list[float] = []
        for tasks in stats.tasks_by_date.values():
            daily_load.append(sum(t.estimated_minutes for t in tasks))
        schedule_density = (
            sum(daily_load) / len(daily_load) if daily_load else 0.0
        )

        # Planning reliability = planning_accuracy
        planning_reliability = stats.planning_accuracy

        # Recovery time: average gap between tasks per day
        recovery = self._compute_recovery_time(stats)

        # Deep work score: completion rate of tasks >= 60 min
        deep_tasks = [
            t for t in stats.all_snapshots if t.estimated_minutes >= 60
        ]
        if deep_tasks:
            deep_done = sum(1 for t in deep_tasks if t.status == "completed")
            deep_work_score = deep_done / len(deep_tasks)
        else:
            deep_work_score = 0.0

        # Context switching: avg category switches per day
        switching = self._compute_context_switching(stats)

        # Time fragmentation: fraction of tasks <= 15 min
        short_tasks = sum(
            1 for t in stats.all_snapshots if t.estimated_minutes <= 15
        )
        time_fragmentation = short_tasks / stats.total_tasks

        # Habit stability: 1 - CV of daily task counts
        daily_counts = [len(tasks) for tasks in stats.tasks_by_date.values()]
        if len(daily_counts) >= 2:
            mean_count = sum(daily_counts) / len(daily_counts)
            if mean_count > 0:
                stdev = statistics.stdev(daily_counts)
                habit_stability = max(0.0, 1.0 - min(1.0, stdev / mean_count))
            else:
                habit_stability = 0.0
        else:
            habit_stability = 1.0

        # Burnout risk composite
        norm_density = min(1.0, schedule_density / 480.0)  # 8 hours = high
        norm_switching = min(1.0, switching / 10.0)

        factors = [
            norm_density,
            stats.failure_rate,
            1.0 - planning_reliability,
            norm_switching,
            time_fragmentation,
            1.0 - habit_stability,
        ]
        burnout_risk = 100.0 * (sum(factors) / len(factors))

        return BurnoutAnalysis(
            burnout_risk=round(burnout_risk, 2),
            planning_reliability=round(planning_reliability, 4),
            schedule_density=round(schedule_density, 2),
            recovery_time=round(recovery, 2),
            deep_work_score=round(deep_work_score, 4),
            context_switching_score=round(switching, 2),
            time_fragmentation=round(time_fragmentation, 4),
            habit_stability=round(habit_stability, 4),
        )

    @staticmethod
    def _compute_recovery_time(stats: IntermediateStats) -> float:
        """
        Average gap between consecutive tasks in a day (minutes).

        Uses scheduled_start/end times.  If unavailable, returns 0.
        """
        gaps: list[float] = []
        for tasks in stats.tasks_by_date.values():
            sorted_tasks = sorted(
                [t for t in tasks if t.scheduled_start and t.scheduled_end],
                key=lambda t: t.scheduled_start,
            )
            for i in range(1, len(sorted_tasks)):
                prev_end = sorted_tasks[i - 1].scheduled_end
                curr_start = sorted_tasks[i].scheduled_start
                if prev_end and curr_start:
                    try:
                        end_parts = prev_end.split(":")
                        start_parts = curr_start.split(":")
                        end_min = int(end_parts[0]) * 60 + int(end_parts[1])
                        start_min = int(start_parts[0]) * 60 + int(start_parts[1])
                        gap = start_min - end_min
                        if gap >= 0:
                            gaps.append(gap)
                    except (ValueError, IndexError):
                        continue
        return sum(gaps) / len(gaps) if gaps else 0.0

    @staticmethod
    def _compute_context_switching(stats: IntermediateStats) -> float:
        """Average number of category switches per day."""
        switches: list[int] = []
        for tasks in stats.tasks_by_date.values():
            sorted_tasks = sorted(
                tasks,
                key=lambda t: t.scheduled_start or "",
            )
            day_switches = 0
            for i in range(1, len(sorted_tasks)):
                if sorted_tasks[i].category_name != sorted_tasks[i - 1].category_name:
                    day_switches += 1
            switches.append(day_switches)
        return sum(switches) / len(switches) if switches else 0.0


class HabitCalculator:
    """
    Overall and per-activity habit scores.

    Definition:
        Computes habit consistency for known activity types by
        matching category names to common patterns (study, workout,
        reading) and time-of-day habits (morning, evening).

    Formula:
        habit_score = 100 × frequency × recency × completion_rate
        (same formula as CategoryCalculator._category_habit_score)

    Input Fields:
        IntermediateStats.tasks_by_category, .tasks_by_hour, .unique_dates.

    Output: HabitScores.
    Edge Cases: No matching categories → score = 0.
    """

    # Keyword matchers for common habit types
    _STUDY_KEYWORDS = {"study", "studying", "learn", "learning", "lecture", "homework", "exam", "revision"}
    _WORKOUT_KEYWORDS = {"workout", "exercise", "gym", "fitness", "run", "running", "sport", "training"}
    _READING_KEYWORDS = {"read", "reading", "book", "literature"}

    def compute(self, stats: IntermediateStats) -> HabitScores:
        if stats.total_tasks == 0:
            return HabitScores()

        # Category habit scores
        cat_habits: dict[str, float] = {}
        for cat, tasks in stats.tasks_by_category.items():
            total = len(tasks)
            done = sum(1 for t in tasks if t.status == "completed")
            comp_rate = done / total if total > 0 else 0.0
            score = CategoryCalculator._category_habit_score(
                tasks, stats.unique_dates, comp_rate,
            )
            cat_habits[cat] = round(score, 2)

        # Activity-specific habits
        study = self._activity_habit(stats, self._STUDY_KEYWORDS)
        workout = self._activity_habit(stats, self._WORKOUT_KEYWORDS)
        reading = self._activity_habit(stats, self._READING_KEYWORDS)

        # Time-of-day habits
        morning = self._time_habit(stats, range(5, 12))
        evening = self._time_habit(stats, range(18, 24))

        # Overall
        all_scores = list(cat_habits.values())
        overall = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return HabitScores(
            overall_habit_score=round(overall, 2),
            study_habit=round(study, 2),
            workout_habit=round(workout, 2),
            reading_habit=round(reading, 2),
            morning_habit=round(morning, 2),
            evening_habit=round(evening, 2),
            category_habit_scores=cat_habits,
        )

    def _activity_habit(
        self,
        stats: IntermediateStats,
        keywords: set[str],
    ) -> float:
        """Habit score for tasks matching any keyword in their category or title."""
        matching = [
            t for t in stats.all_snapshots
            if (
                t.category_name.lower() in keywords
                or any(kw in t.title.lower() for kw in keywords)
            )
        ]
        if not matching:
            return 0.0

        done = sum(1 for t in matching if t.status == "completed")
        comp_rate = done / len(matching)
        return CategoryCalculator._category_habit_score(
            matching, stats.unique_dates, comp_rate,
        )

    def _time_habit(
        self,
        stats: IntermediateStats,
        hours: range,
    ) -> float:
        """Habit score for tasks scheduled in the given hour range."""
        matching = [
            t for t in stats.all_snapshots
            if t.hour is not None and t.hour in hours
        ]
        if not matching:
            return 0.0

        done = sum(1 for t in matching if t.status == "completed")
        comp_rate = done / len(matching)
        return CategoryCalculator._category_habit_score(
            matching, stats.unique_dates, comp_rate,
        )


class DurationCalculator:
    """
    Duration bucket analysis.

    Definition:
        Groups tasks into duration buckets and analyses completion,
        failure, delay, and focus quality per bucket.

    Output: DurationResult.
    Edge Cases: Empty buckets → skipped.
    """

    def compute(self, stats: IntermediateStats) -> DurationResult:
        if stats.total_tasks == 0:
            return DurationResult()

        buckets: list[DurationBucketAnalysis] = []
        best_bucket: Optional[str] = None
        worst_bucket: Optional[str] = None
        best_rate = -1.0
        worst_rate = 2.0

        for (low, high) in DURATION_BUCKETS:
            label = f"{low}-{high}" if high < 9999 else f"{low}+"
            tasks = stats.tasks_by_duration_bucket.get(label, [])
            total = len(tasks)

            if total == 0:
                buckets.append(DurationBucketAnalysis(bucket=label))
                continue

            done = sum(1 for t in tasks if t.status == "completed")
            fail = sum(1 for t in tasks if t.status == "failed")
            comp_rate = done / total
            fail_rate = fail / total

            # Planning accuracy for this bucket
            errors = []
            bucket_delays = []
            for t in tasks:
                if t.actual_minutes is not None and t.estimated_minutes > 0:
                    err = (t.actual_minutes - t.estimated_minutes) / t.estimated_minutes
                    errors.append(err)
                    bucket_delays.append(
                        max(0.0, t.actual_minutes - t.estimated_minutes)
                    )

            plan_acc = (
                max(0.0, min(1.0, 1.0 - abs(sum(errors) / len(errors))))
                if errors else 0.0
            )
            avg_delay = sum(bucket_delays) / len(bucket_delays) if bucket_delays else 0.0

            # Focus quality for this bucket
            measured = [
                t for t in tasks
                if t.actual_minutes is not None and t.estimated_minutes > 0
            ]
            if measured:
                focused = sum(
                    1 for t in measured
                    if abs(t.actual_minutes - t.estimated_minutes) / t.estimated_minutes <= 0.25
                )
                focus = focused / len(measured)
            else:
                focus = 0.0

            buckets.append(DurationBucketAnalysis(
                bucket=label,
                total=total,
                completed=done,
                failed=fail,
                completion_rate=round(comp_rate, 4),
                failure_rate=round(fail_rate, 4),
                planning_accuracy=round(plan_acc, 4),
                avg_delay=round(avg_delay, 2),
                focus_quality=round(focus, 4),
            ))

            if comp_rate > best_rate:
                best_rate = comp_rate
                best_bucket = label
            if comp_rate < worst_rate:
                worst_rate = comp_rate
                worst_bucket = label

        return DurationResult(
            buckets=buckets,
            best_duration=best_bucket,
            worst_duration=worst_bucket,
        )


class WeekdayWeekendCalculator:
    """
    Weekday vs Weekend comparison.

    Definition:
        Compares completion, failure, delay, focus, and simple
        productivity proxy between weekdays (Mon-Fri) and weekends
        (Sat-Sun).

    Output: WeekdayWeekendComparison.
    Edge Cases: No weekend or no weekday tasks → zeroes for that side.
    """

    def compute(self, stats: IntermediateStats) -> WeekdayWeekendComparison:
        wd = self._group_metrics(stats.weekday_tasks)
        we = self._group_metrics(stats.weekend_tasks)

        if wd["completion_rate"] > we["completion_rate"]:
            stronger = "weekday"
        elif we["completion_rate"] > wd["completion_rate"]:
            stronger = "weekend"
        else:
            stronger = "equal"

        return WeekdayWeekendComparison(
            weekday_completion_rate=wd["completion_rate"],
            weekend_completion_rate=we["completion_rate"],
            weekday_failure_rate=wd["failure_rate"],
            weekend_failure_rate=we["failure_rate"],
            weekday_avg_delay=wd["avg_delay"],
            weekend_avg_delay=we["avg_delay"],
            weekday_focus=wd["focus"],
            weekend_focus=we["focus"],
            weekday_productivity=wd["productivity"],
            weekend_productivity=we["productivity"],
            weekday_count=wd["count"],
            weekend_count=we["count"],
            stronger_period=stronger,
        )

    @staticmethod
    def _group_metrics(tasks: list[TaskSnapshot]) -> dict[str, float]:
        """Compute metrics for a group of tasks."""
        total = len(tasks)
        if total == 0:
            return {
                "completion_rate": 0.0, "failure_rate": 0.0,
                "avg_delay": 0.0, "focus": 0.0, "productivity": 0.0,
                "count": 0,
            }

        done = sum(1 for t in tasks if t.status == "completed")
        fail = sum(1 for t in tasks if t.status == "failed")
        comp_rate = done / total
        fail_rate = fail / total

        delays = [
            max(0.0, t.actual_minutes - t.estimated_minutes)
            for t in tasks if t.actual_minutes is not None
        ]
        avg_delay = sum(delays) / len(delays) if delays else 0.0

        measured = [
            t for t in tasks
            if t.actual_minutes is not None and t.estimated_minutes > 0
        ]
        if measured:
            focused = sum(
                1 for t in measured
                if abs(t.actual_minutes - t.estimated_minutes) / t.estimated_minutes <= 0.25
            )
            focus = focused / len(measured)
        else:
            focus = 0.0

        # Simple productivity proxy = completion_rate × (1 - normalised_delay)
        norm_delay = min(1.0, avg_delay / 60.0)
        productivity = comp_rate * (1.0 - norm_delay)

        return {
            "completion_rate": round(comp_rate, 4),
            "failure_rate": round(fail_rate, 4),
            "avg_delay": round(avg_delay, 2),
            "focus": round(focus, 4),
            "productivity": round(productivity, 4),
            "count": total,
        }


class BestHourCalculator:
    """
    Best productivity hour detection.

    Definition:
        Finds the hour with the highest completion rate, subject to
        minimum sample size.

    Formula:
        best_hour = argmax_h(completion_rate_h)
        where count_h >= MIN_TASKS_FOR_BASIC_CONFIDENCE.

    Output: BestHourResult.
    Edge Cases: No hour with sufficient data → None.
    """

    def compute(self, stats: IntermediateStats) -> BestHourResult:
        best_hour: Optional[int] = None
        best_rate = -1.0
        best_count = 0

        for h, tasks in stats.tasks_by_hour.items():
            total = len(tasks)
            if total < MIN_TASKS_FOR_BASIC_CONFIDENCE:
                continue
            done = sum(1 for t in tasks if t.status == "completed")
            rate = done / total
            if rate > best_rate:
                best_rate = rate
                best_hour = h
                best_count = total

        if best_hour is None:
            total_with_hour = sum(len(v) for v in stats.tasks_by_hour.values())
            return BestHourResult(
                confidence=compute_confidence(total_with_hour, stats.observation_days),
            )

        return BestHourResult(
            best_hour=best_hour,
            completion_rate_at_best=round(best_rate, 4),
            confidence=compute_confidence(best_count, stats.observation_days),
        )


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS PROFILE — Full output model
# ═══════════════════════════════════════════════════════════════════════════

class CapacityCalculator:
    """
    Computes a user's Realistic Daily Capacity.

    Definition:
        The typical total planned-minutes load, on days this user
        historically finished most of what they planned, plus evidence
        (completion rate on heavier-than-recommended days vs
        lighter-than-recommended days) showing whether overloading
        actually correlates with worse outcomes for this specific user.

    Formula:
        1. Group all tasks by plan_date (already available via
           IntermediateStats.tasks_by_date).
        2. For each day: planned_minutes = sum(estimated_minutes),
           completion_rate = completed / total.
        3. "Successful" days = completion_rate >= CAPACITY_SUCCESS_DAY_THRESHOLD.
        4. recommended_daily_minutes = median(planned_minutes) over
           successful days (falls back to median over all days if too
           few clearly-successful days exist, or to a generic constant
           if there's not enough history at all).
        5. Evidence: split ALL observed days at the recommended value
           and compare their average completion rates.

    Input Fields:
        stats — IntermediateStats (reads tasks_by_date only).

    Output:
        CapacityResult.

    Edge Cases:
        - Fewer than CAPACITY_MIN_OBSERVATION_DAYS distinct days →
          basis='insufficient_data', generic fallback minutes.
        - Some history but fewer than 3 successful days → basis
          ='overall_median', still a real (if less specific) estimate.
    """

    def compute(self, stats: IntermediateStats) -> CapacityResult:
        day_rows: list[tuple[str, float, float]] = []  # (date, planned_minutes, completion_rate)
        for plan_date, tasks in stats.tasks_by_date.items():
            total = len(tasks)
            if total == 0:
                continue
            planned = float(sum(t.estimated_minutes for t in tasks))
            done = sum(1 for t in tasks if t.status == "completed")
            day_rows.append((plan_date, planned, done / total))

        sample_days = len(day_rows)
        confidence = compute_confidence(stats.total_tasks, sample_days)

        if sample_days < CAPACITY_MIN_OBSERVATION_DAYS:
            return CapacityResult(
                recommended_daily_minutes=CAPACITY_FALLBACK_MINUTES,
                light_day_completion_rate=0.0,
                heavy_day_completion_rate=0.0,
                successful_day_count=0,
                sample_days=sample_days,
                basis="insufficient_data",
                confidence=confidence,
            )

        successful_days = [
            row for row in day_rows
            if row[2] >= CAPACITY_SUCCESS_DAY_THRESHOLD
        ]

        if len(successful_days) >= 3:
            planned_values = [row[1] for row in successful_days]
            recommended = statistics.median(planned_values)
            basis = "historical_success_days"
        else:
            planned_values = [row[1] for row in day_rows]
            recommended = statistics.median(planned_values)
            basis = "overall_median"

        heavy_days = [row for row in day_rows if row[1] > recommended]
        light_days = [row for row in day_rows if row[1] <= recommended]

        heavy_rate = (
            sum(row[2] for row in heavy_days) / len(heavy_days)
            if heavy_days else 0.0
        )
        light_rate = (
            sum(row[2] for row in light_days) / len(light_days)
            if light_days else 0.0
        )

        return CapacityResult(
            recommended_daily_minutes=round(recommended, 1),
            light_day_completion_rate=round(light_rate, 4),
            heavy_day_completion_rate=round(heavy_rate, 4),
            successful_day_count=len(successful_days),
            sample_days=sample_days,
            basis=basis,
            confidence=confidence,
        )


class AnalyticsProfile(BaseModel):
    """
    Complete analytics output for a user.

    Serialisation-ready (Pydantic): supports model_dump(), dict(),
    JSON, REST APIs, and dashboard consumption.

    Definition:
        Top-level container that aggregates outputs from all calculators
        plus metadata about the generation run.

    Fields:
        See inline descriptions.
    """

    # --- Metadata ---
    generated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="ISO timestamp when this profile was generated.",
    )
    window_days: int = Field(
        default=DEFAULT_WINDOW_DAYS,
        description="Number of days in the analysis window.",
    )
    sample_size: int = Field(
        default=0,
        description="Total tasks analysed.",
    )
    profile_version: str = Field(
        default=PROFILE_VERSION,
        description="Schema version of this profile.",
    )
    statistics_version: str = Field(
        default=STATISTICS_VERSION,
        description="Version of the statistics engine.",
    )
    overall_confidence: MetricConfidence = Field(
        default_factory=MetricConfidence,
        description="Overall confidence assessment.",
    )
    analytics_version: str = Field(
        default=ANALYTICS_VERSION,
        description="Version of the analytics engine.",
    )

    # --- Core Metrics ---
    total_tasks: int = 0
    total_completed: int = 0
    total_failed: int = 0
    completion_rate: float = 0.0
    failure_rate: float = 0.0
    avg_delay_minutes: float = 0.0

    # --- Calculator Outputs ---
    productivity: ProductivityResult = Field(
        default_factory=ProductivityResult,
    )
    priority: PriorityResult = Field(default_factory=PriorityResult)
    planning: PlanningResult = Field(default_factory=PlanningResult)
    categories: CategoryResult = Field(default_factory=CategoryResult)
    failure_analysis: FailureResult = Field(default_factory=FailureResult)
    consistency: ConsistencyResult = Field(default_factory=ConsistencyResult)
    trend: TrendResult = Field(default_factory=TrendResult)
    patterns: PatternResult = Field(default_factory=PatternResult)
    insights: InsightResult = Field(default_factory=InsightResult)
    correlations: CorrelationResult = Field(default_factory=CorrelationResult)
    heatmaps: HeatmapResult = Field(default_factory=HeatmapResult)
    duration_analysis: DurationResult = Field(default_factory=DurationResult)
    weekday_weekend: WeekdayWeekendComparison = Field(
        default_factory=WeekdayWeekendComparison,
    )
    habits: HabitScores = Field(default_factory=HabitScores)
    burnout: BurnoutAnalysis = Field(default_factory=BurnoutAnalysis)
    best_hour: BestHourResult = Field(default_factory=BestHourResult)
    capacity: CapacityResult = Field(default_factory=CapacityResult)

    class Config:
        """Pydantic configuration."""
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS FORMATTER — Output formatting (no calculations)
# ═══════════════════════════════════════════════════════════════════════════

class AnalyticsFormatter:
    """
    Converts an AnalyticsProfile into various output formats.

    Responsibilities:
        - Formatting ONLY.
        - Never performs calculations.
        - Never accesses the database.

    Output Formats:
        to_llm_context() → structured text for LLM prompts.
        to_dashboard()   → dict optimised for dashboard rendering.
        to_json()        → JSON string.
        to_dict()        → plain Python dict.
        to_summary()     → short human-readable summary.
    """

    def to_llm_context(self, profile: AnalyticsProfile) -> str:
        """
        Format the analytics profile as structured text for LLM consumption.

        This replaces the manual formatting previously done in
        RecommendationService._format_historical_context.

        The output tells the LLM:
            'Analytics, Patterns and Insights are already precomputed.
             Trust these numbers — do not re-derive them.'
        """
        sections: list[str] = []

        sections.append(
            "=== PRECOMPUTED ANALYTICS (trust these — do not re-derive) ===\n"
        )

        # Core metrics
        sections.append("--- Core Metrics ---")
        sections.append(f"total_tasks: {profile.total_tasks}")
        sections.append(f"total_completed: {profile.total_completed}")
        sections.append(f"total_failed: {profile.total_failed}")
        sections.append(f"completion_rate: {profile.completion_rate:.2%}")
        sections.append(f"failure_rate: {profile.failure_rate:.2%}")
        sections.append(f"avg_delay_minutes: {profile.avg_delay_minutes:.1f}")
        sections.append(f"observation_window: {profile.window_days} days")
        sections.append(f"sample_size: {profile.sample_size}")
        sections.append(
            f"overall_confidence: {profile.overall_confidence.level}"
        )

        # Productivity
        sections.append("\n--- Productivity ---")
        sections.append(f"productivity_score: {profile.productivity.score}/100")
        for comp, val in profile.productivity.components.items():
            sections.append(f"  {comp}: {val:.4f}")

        # Streaks & Consistency
        sections.append("\n--- Consistency ---")
        sections.append(
            f"current_streak: {profile.consistency.current_streak} days"
        )
        sections.append(
            f"longest_streak: {profile.consistency.longest_streak} days"
        )
        sections.append(
            f"consistency_score: {profile.consistency.consistency_score:.2%}"
        )

        # Planning
        sections.append("\n--- Planning Accuracy ---")
        sections.append(
            f"planning_accuracy: {profile.planning.planning_accuracy:.2%}"
        )
        sections.append(
            f"bias_direction: {profile.planning.bias_direction}"
        )
        sections.append(
            f"bias_severity: {profile.planning.bias_severity:.2%}"
        )

        # Best hour
        sections.append("\n--- Best Productivity Hour ---")
        if profile.best_hour.best_hour is not None:
            sections.append(f"best_hour: {profile.best_hour.best_hour}:00")
            sections.append(
                f"completion_rate_at_best: "
                f"{profile.best_hour.completion_rate_at_best:.2%}"
            )
            sections.append(
                f"confidence: {profile.best_hour.confidence.level}"
            )
        else:
            sections.append("best_hour: insufficient data")

        # Failure analysis
        sections.append("\n--- Failure Analysis ---")
        sections.append(
            f"main_failure_reason: "
            f"{profile.failure_analysis.main_failure_reason or 'None'}"
        )
        for reason, count in profile.failure_analysis.failure_reason_counts.items():
            sections.append(f"  {reason}: {count}")

        # Categories
        sections.append("\n--- Category Performance ---")
        sections.append(
            f"favorite_category: {profile.categories.favorite_category or 'None'}"
        )
        sections.append(
            f"strongest_category: {profile.categories.strongest_category or 'None'}"
        )
        sections.append(
            f"weakest_category: {profile.categories.weakest_category or 'None'}"
        )
        for cat_a in profile.categories.per_category:
            sections.append(
                f"  {cat_a.category}: completion={cat_a.completion_rate:.0%}, "
                f"failure={cat_a.failure_rate:.0%}, trend={cat_a.trend}"
            )

        # Priority
        sections.append("\n--- Priority Performance ---")
        sections.append(
            f"highest_completed_priority: "
            f"{profile.priority.highest_completed_priority or 'None'}"
        )
        sections.append(
            f"highest_failed_priority: "
            f"{profile.priority.highest_failed_priority or 'None'}"
        )
        for pa in profile.priority.per_priority:
            if pa.total > 0:
                sections.append(
                    f"  P{pa.priority}: {pa.total} tasks, "
                    f"completion={pa.completion_rate:.0%}, "
                    f"failure={pa.failure_rate:.0%}, "
                    f"risk={pa.risk_score:.2f}"
                )

        # Trend
        sections.append("\n--- Trend ---")
        sections.append(f"trend_direction: {profile.trend.trend_direction}")
        sections.append(f"trend_score: {profile.trend.trend_score:.4f}")

        # Weekday vs Weekend
        sections.append("\n--- Weekday vs Weekend ---")
        ww = profile.weekday_weekend
        sections.append(
            f"weekday_completion: {ww.weekday_completion_rate:.0%} "
            f"({ww.weekday_count} tasks)"
        )
        sections.append(
            f"weekend_completion: {ww.weekend_completion_rate:.0%} "
            f"({ww.weekend_count} tasks)"
        )
        sections.append(f"stronger_period: {ww.stronger_period}")

        # Habits
        sections.append("\n--- Habit Scores ---")
        sections.append(
            f"overall_habit_score: {profile.habits.overall_habit_score:.1f}/100"
        )

        # Burnout
        sections.append("\n--- Burnout Assessment ---")
        sections.append(
            f"burnout_risk: {profile.burnout.burnout_risk:.1f}/100"
        )
        sections.append(
            f"schedule_density: {profile.burnout.schedule_density:.0f} min/day"
        )
        sections.append(
            f"deep_work_score: {profile.burnout.deep_work_score:.2%}"
        )
        sections.append(
            f"context_switching: {profile.burnout.context_switching_score:.1f} switches/day"
        )

        # Realistic Capacity
        sections.append("\n--- Realistic Daily Capacity ---")
        sections.append(
            f"recommended_daily_minutes: "
            f"{profile.capacity.recommended_daily_minutes:.0f} "
            f"(basis: {profile.capacity.basis})"
        )
        if profile.capacity.basis != "insufficient_data":
            sections.append(
                f"completion_rate_on_lighter_days: "
                f"{profile.capacity.light_day_completion_rate:.0%}"
            )
            sections.append(
                f"completion_rate_on_heavier_days: "
                f"{profile.capacity.heavy_day_completion_rate:.0%}"
            )

        # Patterns
        if profile.patterns.patterns:
            sections.append("\n--- Detected Patterns ---")
            for p in profile.patterns.patterns:
                sections.append(
                    f"[{p.pattern_name}] (confidence: {p.confidence}): "
                    f"{p.observation}"
                )

        # Insights
        if profile.insights.insights:
            sections.append("\n--- Insights ---")
            for ins in profile.insights.insights:
                sections.append(
                    f"(confidence: {ins.confidence}): {ins.observation}"
                )

        sections.append(
            "\n=== END PRECOMPUTED ANALYTICS ==="
        )

        return "\n".join(sections)

    def to_dashboard(self, profile: AnalyticsProfile) -> dict[str, Any]:
        """
        Format for dashboard consumption.

        Returns a dict with top-level keys for each dashboard widget.
        """
        return {
            "metadata": {
                "generated_at": profile.generated_at,
                "window_days": profile.window_days,
                "sample_size": profile.sample_size,
                "overall_confidence": profile.overall_confidence.model_dump(),
                "analytics_version": profile.analytics_version,
            },
            "summary": {
                "total_tasks": profile.total_tasks,
                "total_completed": profile.total_completed,
                "total_failed": profile.total_failed,
                "completion_rate": profile.completion_rate,
                "failure_rate": profile.failure_rate,
                "productivity_score": profile.productivity.score,
                "current_streak": profile.consistency.current_streak,
                "longest_streak": profile.consistency.longest_streak,
                "burnout_risk": profile.burnout.burnout_risk,
            },
            "productivity": profile.productivity.model_dump(),
            "priority": profile.priority.model_dump(),
            "planning": profile.planning.model_dump(),
            "categories": profile.categories.model_dump(),
            "failure_analysis": profile.failure_analysis.model_dump(),
            "consistency": profile.consistency.model_dump(),
            "trend": profile.trend.model_dump(),
            "patterns": profile.patterns.model_dump(),
            "insights": profile.insights.model_dump(),
            "correlations": profile.correlations.model_dump(),
            "heatmaps": profile.heatmaps.model_dump(),
            "duration_analysis": profile.duration_analysis.model_dump(),
            "weekday_weekend": profile.weekday_weekend.model_dump(),
            "habits": profile.habits.model_dump(),
            "burnout": profile.burnout.model_dump(),
            "best_hour": profile.best_hour.model_dump(),
        }

    def to_json(self, profile: AnalyticsProfile) -> str:
        """Serialise the full profile to a JSON string."""
        return profile.model_dump_json(indent=2)

    def to_dict(self, profile: AnalyticsProfile) -> dict[str, Any]:
        """Convert the profile to a plain dict."""
        return profile.model_dump()

    def to_summary(self, profile: AnalyticsProfile) -> str:
        """
        Short human-readable summary of key metrics.

        Suitable for notification text, quick status checks, etc.
        """
        lines = [
            f"📊 Analytics Summary ({profile.window_days}-day window, "
            f"{profile.sample_size} tasks)",
            f"",
            f"  Productivity Score: {profile.productivity.score}/100",
            f"  Completion Rate:    {profile.completion_rate:.0%}",
            f"  Failure Rate:       {profile.failure_rate:.0%}",
            f"  Current Streak:     {profile.consistency.current_streak} days",
            f"  Longest Streak:     {profile.consistency.longest_streak} days",
            f"  Trend:              {profile.trend.trend_direction}",
            f"  Burnout Risk:       {profile.burnout.burnout_risk:.0f}/100",
        ]

        if profile.best_hour.best_hour is not None:
            lines.append(
                f"  Best Hour:          {profile.best_hour.best_hour}:00"
            )

        if profile.categories.favorite_category:
            lines.append(
                f"  Favorite Category:  {profile.categories.favorite_category}"
            )

        if profile.failure_analysis.main_failure_reason:
            lines.append(
                f"  Main Failure:       {profile.failure_analysis.main_failure_reason}"
            )

        lines.append(
            f"\n  Confidence: {profile.overall_confidence.level}"
        )

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINE — Orchestrator
# ═══════════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    """
    Orchestrator that wires all analytics components together.

    Flow:
        1. AnalyticsLoader loads TaskSnapshots (single DB query).
        2. build_intermediate_stats() creates immutable IntermediateStats.
        3. Each Calculator reads IntermediateStats and produces a result.
        4. Results are assembled into an AnalyticsProfile.

    Usage:
        engine = AnalyticsEngine(db)
        profile = engine.build_profile(user_id=1, window_days=30)
        context = AnalyticsFormatter().to_llm_context(profile)
    """

    def __init__(self, db: Database) -> None:
        self._loader = AnalyticsLoader(db)

    def build_profile(
        self,
        user_id: int,
        window_days: int = DEFAULT_WINDOW_DAYS,
        language: str = "en",
    ) -> AnalyticsProfile:
        """
        Build a complete AnalyticsProfile for a user.

        Single database load.  Single IntermediateStats build.
        All calculators run in sequence, reading only from stats.

        Args:
            user_id: Target user.
            window_days: Analysis window size in days.
            language: Language for generated observation/evidence/
                description text ("en" or "ar"). Only affects
                PatternCalculator, InsightCalculator, and
                CorrelationCalculator — all other calculators return
                pure numbers with no natural-language text.

        Returns:
            A fully populated AnalyticsProfile.
        """
        # Step 1: Single database load
        snapshots = self._loader.load_snapshots(
            user_id=user_id, window_days=window_days,
        )

        # Step 2: Build immutable intermediate stats
        stats = build_intermediate_stats(snapshots, window_days=window_days)

        # Step 3: Run all calculators
        productivity = ProductivityCalculator().compute(stats)
        priority = PriorityCalculator().compute(stats)
        planning = PlanningCalculator().compute(stats)
        categories = CategoryCalculator().compute(stats)
        failure = FailureCalculator().compute(stats)
        consistency = ConsistencyCalculator().compute(stats)
        trend = TrendCalculator().compute(stats)
        patterns = PatternCalculator().compute(stats, language)
        insights_result = InsightCalculator().compute(stats, language)
        correlations = CorrelationCalculator().compute(stats, language)
        heatmaps = HeatmapCalculator().compute(stats)
        duration = DurationCalculator().compute(stats)
        weekday_weekend = WeekdayWeekendCalculator().compute(stats)
        habits = HabitCalculator().compute(stats)
        burnout = BurnoutCalculator().compute(stats)
        best_hour_result = BestHourCalculator().compute(stats)
        capacity = CapacityCalculator().compute(stats)

        # Step 4: Assemble profile
        overall_confidence = compute_confidence(
            stats.total_tasks, stats.observation_days,
        )

        return AnalyticsProfile(
            generated_at=datetime.now().isoformat(),
            window_days=window_days,
            sample_size=stats.total_tasks,
            overall_confidence=overall_confidence,
            total_tasks=stats.total_tasks,
            total_completed=stats.total_completed,
            total_failed=stats.total_failed,
            completion_rate=round(stats.completion_rate, 4),
            failure_rate=round(stats.failure_rate, 4),
            avg_delay_minutes=round(stats.avg_delay_minutes, 2),
            productivity=productivity,
            priority=priority,
            planning=planning,
            categories=categories,
            failure_analysis=failure,
            consistency=consistency,
            trend=trend,
            patterns=patterns,
            insights=insights_result,
            correlations=correlations,
            heatmaps=heatmaps,
            duration_analysis=duration,
            weekday_weekend=weekday_weekend,
            habits=habits,
            burnout=burnout,
            best_hour=best_hour_result,
            capacity=capacity,
        )