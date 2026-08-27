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

    # --- Fixed-time task support ---
    is_fixed_time: bool = False
    fixed_start: Optional[time] = None
    fixed_end: Optional[time] = None

    # --- Break support ---
    # A break is just a fixed-time task with this flag set — it goes
    # through the exact same placement/blocking logic as any other
    # fixed-time task. The flag only exists so conflict detection can
    # tell "your break collided with a real fixed task" apart from two
    # ordinary fixed tasks colliding with each other.
    is_break: bool = False

    @staticmethod
    def _parse_hhmm(value) -> Optional[time]:
        """Parse an 'HH:MM' string (or pass through a time/None) safely."""
        if value is None or isinstance(value, time):
            return value
        try:
            parts = str(value).split(":")
            return time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return None

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
            is_fixed_time=bool(_get("is_fixed_time", False)),
            fixed_start=cls._parse_hhmm(_get("fixed_start", None)),
            fixed_end=cls._parse_hhmm(_get("fixed_end", None)),
            is_break=bool(_get("is_break", False)),
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


@dataclass
class SchedulingConflict:
    """
    Reports a break that could not be reconciled against a fixed-time
    task because both sides are immovable (neither a break nor a
    fixed-time task is ever shifted by the scheduler).
    """
    break_start: time
    break_end: time
    fixed_task_title: str
    fixed_task_start: time
    fixed_task_end: time


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

    def _next_blocked_overlap(
        self,
        current: datetime,
        duration_minutes: int,
        blocked: list[tuple[datetime, datetime]],
    ) -> Optional[tuple[datetime, datetime]]:
        """
        Return the earliest blocked slot that overlaps the proposed task
        window [current, current + duration_minutes), or None if the
        window is completely free.
        """
        task_end = current + timedelta(minutes=duration_minutes)
        overlapping = [
            (block_start, block_end)
            for block_start, block_end in blocked
            if current < block_end and task_end > block_start
        ]
        if not overlapping:
            return None
        return min(overlapping, key=lambda pair: pair[0])

    def _best_gap_filler(
        self,
        pending: list[ScheduledTask],
        exclude: "ScheduledTask",
        gap_minutes: int,
    ) -> Optional[ScheduledTask]:
        """
        Among the still-unscheduled flexible tasks (other than the one
        currently blocked), find the best candidate that fits entirely
        inside a gap of ``gap_minutes`` before the next blocked slot.

        Selection rule (as agreed with the user):
          1. Highest priority first (1 = highest, 5 = lowest).
          2. Ties broken by whichever duration wastes the least of the
             gap (i.e. closest to gap_minutes without exceeding it).
        """
        candidates = [
            t for t in pending
            if t is not exclude and t.estimated_minutes <= gap_minutes
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t.priority, gap_minutes - t.estimated_minutes))
        return candidates[0]

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

        # ------------------------------------------------------------------
        # Convert every incoming task up front. This lets us know which
        # tasks are fixed-time BEFORE placing any flexible task, so a
        # flexible task earlier in the list can never land on top of a
        # fixed-time task later in the list (or vice versa).
        # ------------------------------------------------------------------
        converted: list[ScheduledTask] = [
            ScheduledTask.from_task(task, order_index=i)
            for i, task in enumerate(tasks)
        ]

        # A single calendar day only has 1440 minutes. Anything at or
        # above that can never actually fit in one day — worse, silently
        # letting it through makes the cursor roll into a LATER day while
        # every scheduled_start/scheduled_end still only stores a bare
        # time-of-day (by design — this is a single-day planner, not a
        # multi-day one), so the extra days quietly vanish and two
        # unrelated tasks can end up displaying the exact same time even
        # though they're actually far apart. Reject it outright instead.
        MAX_TASK_MINUTES = 16 * 60  # 16h — a generous, still same-day cap

        for st in converted:
            if st.estimated_minutes <= 0:
                raise ValueError(
                    f"Task '{st.title}' (index {st.order_index}) has invalid "
                    f"estimated_minutes: {st.estimated_minutes}. Must be positive."
                )
            if st.estimated_minutes >= MAX_TASK_MINUTES:
                raise ValueError(
                    f"Task '{st.title}' (index {st.order_index}) has an "
                    f"estimated_minutes of {st.estimated_minutes} "
                    f"({st.estimated_minutes / 60:.1f}h), which can't fit in a "
                    f"single day. Break it into smaller tasks instead."
                )
            if st.is_fixed_time and st.fixed_start is None:
                raise ValueError(
                    f"Task '{st.title}' (index {st.order_index}) is marked "
                    f"is_fixed_time=True but has no fixed_start."
                )

        # Pre-compute blocked slots as datetime pairs (Google Calendar
        # commitments the user already has today).
        blocked_datetimes: list[tuple[datetime, datetime]] = []
        for slot in (self.preferences.blocked_slots or []):
            if len(slot) >= 2:
                blocked_datetimes.append((
                    self._time_to_datetime(slot[0], base_date),
                    self._time_to_datetime(slot[1], base_date),
                ))

        # Every fixed-time task is ALSO a blocked slot for scheduling
        # purposes — this is what stops a flexible task from ever being
        # placed on top of a fixed task, and what makes the fixed task's
        # own time immune to the work-day-start cursor entirely: fixed
        # tasks are never advanced past, they're just carved out.
        #
        # Fixed-time tasks are split into "break" and "regular" purely
        # for conflict reporting below — a break (is_break=True) is a
        # task the user asked for via the Breaks flow, and is rendered/
        # controlled differently in the UI. Both are placed identically.
        break_fixed_windows: list[tuple[datetime, datetime, ScheduledTask]] = []
        regular_fixed_windows: list[tuple[datetime, datetime, ScheduledTask]] = []
        for st in converted:
            if st.is_fixed_time and st.fixed_start is not None:
                fixed_start_dt = self._time_to_datetime(st.fixed_start, base_date)
                if st.fixed_end is not None:
                    fixed_end_dt = self._time_to_datetime(st.fixed_end, base_date)
                else:
                    fixed_end_dt = fixed_start_dt + timedelta(minutes=st.estimated_minutes)
                blocked_datetimes.append((fixed_start_dt, fixed_end_dt))
                if st.is_break:
                    break_fixed_windows.append((fixed_start_dt, fixed_end_dt, st))
                else:
                    regular_fixed_windows.append((fixed_start_dt, fixed_end_dt, st))

        # Legacy path: a caller may still pass breaks via
        # SchedulingPreferences.user_breaks instead of as real is_break
        # tasks. Fold them in exactly the same way, so old callers keep
        # working unchanged.
        for brk in self._sorted_user_breaks():
            break_start_dt = self._time_to_datetime(brk.start_time, base_date)
            break_end_dt = break_start_dt + timedelta(minutes=brk.duration_minutes)
            blocked_datetimes.append((break_start_dt, break_end_dt))
            break_fixed_windows.append((
                break_start_dt, break_end_dt,
                ScheduledTask(
                    title="Break", category="", estimated_minutes=brk.duration_minutes,
                    priority=1, is_fixed_time=True, fixed_start=brk.start_time,
                    fixed_end=self._datetime_to_time(break_end_dt), is_break=True,
                ),
            ))

        blocked_datetimes.sort(key=lambda pair: pair[0])

        # ------------------------------------------------------------------
        # Detect break <-> fixed-task conflicts. Neither side can move, so
        # this can't be auto-resolved — surface it instead of guessing.
        # ------------------------------------------------------------------
        self.last_conflicts: list[SchedulingConflict] = []
        for break_start_dt, break_end_dt, break_st in break_fixed_windows:
            for fixed_start_dt, fixed_end_dt, fixed_st in regular_fixed_windows:
                if break_start_dt < fixed_end_dt and break_end_dt > fixed_start_dt:
                    self.last_conflicts.append(SchedulingConflict(
                        break_start=self._datetime_to_time(break_start_dt),
                        break_end=self._datetime_to_time(break_end_dt),
                        fixed_task_title=fixed_st.title,
                        fixed_task_start=fixed_st.fixed_start,
                        fixed_task_end=self._datetime_to_time(fixed_end_dt),
                    ))

        scheduled: list[ScheduledTask] = []

        # Fixed-time tasks are placed immediately; their time is
        # authoritative and never recomputed from the cursor.
        for st in converted:
            if st.is_fixed_time and st.fixed_start is not None:
                st.scheduled_start = st.fixed_start
                if st.fixed_end is not None:
                    st.scheduled_end = st.fixed_end
                else:
                    end_dt = self._time_to_datetime(st.fixed_start, base_date) + timedelta(
                        minutes=st.estimated_minutes
                    )
                    st.scheduled_end = self._datetime_to_time(end_dt)
                scheduled.append(st)

        # ------------------------------------------------------------------
        # Flexible tasks: walk the original order, but if the next task
        # in line would collide with a blocked slot, first check whether
        # any *other* still-pending flexible task fits entirely into the
        # gap ahead of that blocked slot. If one does, place it there
        # instead of leaving the gap empty, then retry the original task.
        # Final output is re-sorted by order_index, so a task placed out
        # of turn still appears in its normal spot in the task list.
        # ------------------------------------------------------------------
        pending: list[ScheduledTask] = [
            st for st in converted if not (st.is_fixed_time and st.fixed_start is not None)
        ]

        while pending:
            primary = pending[0]
            overlap = self._next_blocked_overlap(
                current, primary.estimated_minutes, blocked_datetimes
            )

            if overlap is None:
                # Clear runway: place the task right here.
                primary.scheduled_start = self._datetime_to_time(current)
                current += timedelta(minutes=primary.estimated_minutes)
                primary.scheduled_end = self._datetime_to_time(current)
                scheduled.append(primary)
                pending.pop(0)
                continue

            block_start, block_end = overlap
            gap_minutes = int((block_start - current).total_seconds() // 60)

            filler = None
            if gap_minutes > 0:
                filler = self._best_gap_filler(pending, primary, gap_minutes)

            if filler is not None:
                filler.scheduled_start = self._datetime_to_time(current)
                current += timedelta(minutes=filler.estimated_minutes)
                filler.scheduled_end = self._datetime_to_time(current)
                scheduled.append(filler)
                pending.remove(filler)
                # Loop again: cursor advanced, primary is retried next pass.
                continue

            # No task fits the gap — leave it empty and jump past the block.
            current = block_end

        scheduled.sort(key=lambda st: st.order_index)
        return scheduled