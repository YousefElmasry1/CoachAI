from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ScheduledTask:
    title: str
    category: str
    estimated_minutes: int
    priority: int
    description: str = ""
    scheduled_start: Optional[time] = None
    scheduled_end: Optional[time] = None
    order_index: int = 0

    # --- Reserved for future fixed-time task support (not yet active) ---
    is_fixed_time: bool = False
    fixed_start: Optional[time] = None
    fixed_end: Optional[time] = None

    @classmethod
    def from_task(cls, task, order_index: int = 0) -> "ScheduledTask":
        # Support both attribute access (Pydantic) and dict access
        def _get(name: str, default=None):
            if hasattr(task, name):
                return getattr(task, name)
            if isinstance(task, dict):
                return task.get(name, default)
            return default

        return cls(
            title=_get("title", ""),
            category=_get("category", ""),
            estimated_minutes=_get("estimated_minutes", 30),
            priority=_get("priority", 3),
            description=_get("description", ""),
            order_index=order_index,
            # Absent on current TaskOutput/dict shapes -> safe defaults.
            is_fixed_time=_get("is_fixed_time", False),
            fixed_start=_get("fixed_start", None),
            fixed_end=_get("fixed_end", None),
        )


@dataclass
class UserBreak:
    start_time: time
    duration_minutes: int

    def __post_init__(self) -> None:
        """Validate break invariants immediately."""
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive.")


@dataclass
class SchedulingPreferences:
    work_day_start: time
    user_breaks: Optional[list[UserBreak]] = field(default_factory=list)
    blocked_slots: Optional[list[tuple]] = field(default_factory=list)


# Backward-compatible alias. Older callers (e.g. scheduler_service.py) may
# still reference ``BreakConfig`` by name; it is the same model under a new,
# more accurate name — it no longer represents fixed break "policy", just
# optional user preferences.
BreakConfig = SchedulingPreferences


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    def __init__(self, preferences: Optional[SchedulingPreferences] = None) -> None:
        if preferences is None or preferences.work_day_start is None:
            raise ValueError("work_day_start must be provided by the user.")
        self.preferences: SchedulingPreferences = preferences

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _time_to_datetime(self, t: time, base_date: datetime) -> datetime:
        return base_date.replace(
            hour=t.hour,
            minute=t.minute,
            second=0,
            microsecond=0,
        )

    def _datetime_to_time(self, dt: datetime) -> time:
        """Extract the time component from a datetime."""
        return dt.time()

    def _resolve_work_day_start(self) -> time:
        return self.preferences.work_day_start

    def _sorted_user_breaks(self) -> list[UserBreak]:
        if not self.preferences.user_breaks:
            return []
        return sorted(self.preferences.user_breaks, key=lambda b: b.start_time)

    def _due_user_breaks(
        self,
        current: datetime,
        remaining_breaks: list[UserBreak],
        base_date: datetime,
    ) -> tuple[datetime, list[UserBreak]]:
        
        while remaining_breaks:
            next_break = remaining_breaks[0]
            break_dt = self._time_to_datetime(next_break.start_time, base_date)

            if current < break_dt:
                break  # Not due yet

            current = current + timedelta(minutes=next_break.duration_minutes)
            remaining_breaks = remaining_breaks[1:]

        return current, remaining_breaks

    def _skip_blocked_slots(
        self,
        current: datetime,
        duration_minutes: int,
        blocked: list[tuple[datetime, datetime]],
    ) -> datetime:
        """
        If the proposed task window [current, current+duration] overlaps
        any blocked slot, push current past the end of the overlapping
        slot. Repeat until no overlaps remain (handles consecutive or
        overlapping blocks).
        """
        task_end = current + timedelta(minutes=duration_minutes)
        changed = True
        while changed:
            changed = False
            for block_start, block_end in blocked:
                if current < block_end and task_end > block_start:
                    current = block_end
                    task_end = current + timedelta(minutes=duration_minutes)
                    changed = True
        return current

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(
        self,
        tasks,
        plan_date: date,
    ) -> list[ScheduledTask]:
    
        if not isinstance(plan_date, date):
            raise ValueError(
                f"plan_date must be a date instance, got {type(plan_date).__name__}."
            )

        if not tasks:
            raise ValueError("tasks list cannot be empty.")

        # Initialise scheduling cursor at the user's work-day start (or
        # START_OF_DAY if none was given) on the given plan_date.
        base_date = datetime(plan_date.year, plan_date.month, plan_date.day)
        current: datetime = self._time_to_datetime(
            self._resolve_work_day_start(),
            base_date,
        )

        remaining_breaks = self._sorted_user_breaks()
        scheduled: list[ScheduledTask] = []

        # Pre-compute blocked slots as datetime pairs
        blocked_datetimes: list[tuple[datetime, datetime]] = []
        for slot in (self.preferences.blocked_slots or []):
            if len(slot) >= 2:
                blocked_datetimes.append((
                    self._time_to_datetime(slot[0], base_date),
                    self._time_to_datetime(slot[1], base_date),
                ))
        blocked_datetimes.sort(key=lambda pair: pair[0])

        for order_index, task in enumerate(tasks):
            # Convert incoming task to our internal representation
            st = ScheduledTask.from_task(task, order_index=order_index)

            if st.estimated_minutes <= 0:
                raise ValueError(
                    f"Task '{st.title}' (index {order_index}) has invalid "
                    f"estimated_minutes: {st.estimated_minutes}. Must be positive."
                )

            # ------------------------------------------------------------------
            # Insert any user-defined breaks that are now due.
            # No automatic breaks are ever inserted here.
            # ------------------------------------------------------------------
            current, remaining_breaks = self._due_user_breaks(
                current, remaining_breaks, base_date
            )

            # ----------------------------------------------------------
            # Skip past any Google Calendar blocked slots
            # ----------------------------------------------------------
            if blocked_datetimes:
                new_current = self._skip_blocked_slots(
                    current, st.estimated_minutes, blocked_datetimes
                )
                if new_current != current:
                    current = new_current
                    # Re-check breaks after jumping past a blocked slot
                    current, remaining_breaks = self._due_user_breaks(
                        current, remaining_breaks, base_date
                    )

            # ------------------------------------------------------------------
            # Assign time slot to the task
            # ------------------------------------------------------------------
            st.scheduled_start = self._datetime_to_time(current)
            current += timedelta(minutes=st.estimated_minutes)
            st.scheduled_end = self._datetime_to_time(current)

            scheduled.append(st)

        return scheduled