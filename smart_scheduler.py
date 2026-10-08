"""Pure deterministic segmented Smart Scheduling domain logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from config import MAX_SEGMENTS_PER_TASK, MIN_SEGMENT_MINUTES, WORK_DAY_END
from scheduler import (
    FixedTaskConflict,
    ScheduledTask,
    SchedulingConflict,
    SchedulingPreferences,
)
from session_suitability import (
    best_session_lengths,
    build_session_profile,
)


@dataclass(frozen=True)
class TaskSegment:
    segment_index: int
    scheduled_start: time
    scheduled_end: time

    @property
    def duration_minutes(self) -> int:
        anchor = date(2000, 1, 1)
        return int(
            (datetime.combine(anchor, self.scheduled_end)
             - datetime.combine(anchor, self.scheduled_start)).total_seconds() // 60
        )


@dataclass(frozen=True)
class UnplacedTask:
    task_id: Optional[int]
    title: str
    remaining_minutes: int
    reason: str
    message: str


@dataclass
class AllocationResult:
    placed_tasks: list[ScheduledTask] = field(default_factory=list)
    unplaced_tasks: list[UnplacedTask] = field(default_factory=list)
    conflicts: list[SchedulingConflict] = field(default_factory=list)
    fixed_conflicts: list[FixedTaskConflict] = field(default_factory=list)


class SmartScheduler:
    """Allocate whole logical tasks into one-to-three same-day segments."""

    def __init__(self, preferences: SchedulingPreferences) -> None:
        if preferences is None or preferences.work_day_start is None:
            raise ValueError("work_day_start must be provided by the user.")
        self.preferences = preferences

    @staticmethod
    def _dt(base: date, value: time) -> datetime:
        return datetime.combine(base, value.replace(microsecond=0))

    @staticmethod
    def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
        merged: list[list[datetime]] = []
        for start, end in sorted(intervals):
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            elif end > merged[-1][1]:
                merged[-1][1] = end
        return [(start, end) for start, end in merged]

    @staticmethod
    def _next_overlap(
        current: datetime,
        minutes: int,
        occupied: list[tuple[datetime, datetime]],
    ) -> Optional[tuple[datetime, datetime]]:
        end = current + timedelta(minutes=minutes)
        matches = [(start, stop) for start, stop in occupied if current < stop and end > start]
        return min(matches, key=lambda item: item[0]) if matches else None

    @staticmethod
    def _next_free(current: datetime, occupied: list[tuple[datetime, datetime]]) -> datetime:
        moved = True
        while moved:
            moved = False
            for start, end in occupied:
                if start <= current < end:
                    current = end
                    moved = True
                    break
        return current

    @staticmethod
    def _best_gap_filler(
        pending: list[ScheduledTask], exclude: ScheduledTask, gap_minutes: int,
        cursor: datetime, gap_end: datetime, plan_date: date,
        day_start: datetime, boundary: datetime,
    ) -> Optional[ScheduledTask]:
        candidates = [
            task for task in pending
            if task is not exclude and task.estimated_minutes <= gap_minutes
            and SmartScheduler._task_bounds(
                task, plan_date, day_start, boundary,
            )[0] <= cursor
            and cursor + timedelta(minutes=task.estimated_minutes) <= min(
                gap_end,
                SmartScheduler._task_bounds(
                    task, plan_date, day_start, boundary,
                )[1],
            )
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda task: (task.priority, gap_minutes - task.estimated_minutes, task.order_index))

    @staticmethod
    def _task_bounds(
        task: ScheduledTask,
        plan_date: date,
        day_start: datetime,
        boundary: datetime,
    ) -> tuple[datetime, datetime]:
        earliest = day_start
        latest = boundary
        if task.earliest_start is not None:
            if task.earliest_start.date() > plan_date:
                earliest = boundary
            elif task.earliest_start.date() == plan_date:
                earliest = max(earliest, task.earliest_start)
        if task.deadline is not None:
            if task.deadline.date() < plan_date:
                latest = day_start
            elif task.deadline.date() == plan_date:
                latest = min(latest, task.deadline)
        return earliest, latest

    @staticmethod
    def _some_later_window_fits(
        start: datetime,
        minutes: int,
        occupied: list[tuple[datetime, datetime]],
        boundary: datetime,
    ) -> Optional[datetime]:
        cursor = start
        for block_start, block_end in occupied:
            if block_end <= cursor:
                continue
            if block_start > cursor and cursor + timedelta(minutes=minutes) <= block_start:
                return cursor
            cursor = max(cursor, block_end)
        if cursor + timedelta(minutes=minutes) <= boundary:
            return cursor
        return None

    @staticmethod
    def _free_minutes(
        start: datetime, occupied: list[tuple[datetime, datetime]], boundary: datetime,
    ) -> int:
        cursor = start
        free = 0
        for block_start, block_end in occupied:
            if block_end <= cursor:
                continue
            if block_start > cursor:
                free += int((min(block_start, boundary) - cursor).total_seconds() // 60)
            cursor = max(cursor, block_end)
            if cursor >= boundary:
                break
        if cursor < boundary:
            free += int((boundary - cursor).total_seconds() // 60)
        return max(0, free)

    def allocate(self, tasks, plan_date: date) -> AllocationResult:
        if not isinstance(plan_date, date):
            raise ValueError("plan_date must be a date instance.")
        converted = [ScheduledTask.from_task(task, order_index=i) for i, task in enumerate(tasks)]
        result = AllocationResult()
        boundary = self._dt(plan_date, WORK_DAY_END)
        day_start = self._dt(plan_date, self.preferences.work_day_start)
        if day_start >= boundary:
            raise ValueError("work_day_start must be before WORK_DAY_END.")

        external: list[tuple[datetime, datetime]] = []
        for raw in self.preferences.blocked_slots or []:
            if len(raw) < 2:
                continue
            start, end = self._dt(plan_date, raw[0]), self._dt(plan_date, raw[1])
            if end <= start or end > boundary:
                raise ValueError("Blocked intervals must be valid same-day windows before WORK_DAY_END.")
            external.append((start, end))

        fixed_windows: list[tuple[datetime, datetime, ScheduledTask]] = []
        for task in converted:
            if task.estimated_minutes <= 0:
                raise ValueError(f"Task '{task.title}' must have a positive duration.")
            if not task.is_fixed_time:
                continue
            if task.fixed_start is None:
                raise ValueError(f"Fixed task '{task.title}' has no start time.")
            start = self._dt(plan_date, task.fixed_start)
            end = self._dt(plan_date, task.fixed_end) if task.fixed_end else start + timedelta(minutes=task.estimated_minutes)
            if end <= start or end > boundary:
                raise ValueError(f"Fixed task '{task.title}' has an invalid or overnight interval.")
            task.scheduled_start, task.scheduled_end = task.fixed_start, end.time()
            task.segments = [TaskSegment(0, start.time(), end.time())]
            fixed_windows.append((start, end, task))
            result.placed_tasks.append(task)

        breaks = [window for window in fixed_windows if window[2].is_break]
        regular = [window for window in fixed_windows if not window[2].is_break]
        for break_start, break_end, _ in breaks:
            for fixed_start, fixed_end, fixed_task in regular:
                if break_start < fixed_end and break_end > fixed_start:
                    result.conflicts.append(SchedulingConflict(
                        break_start.time(), break_end.time(), fixed_task.title,
                        fixed_start.time(), fixed_end.time(),
                        break_task_id=next((task.task_id for start, end, task in breaks if start == break_start and end == break_end), None),
                        fixed_task_id=fixed_task.task_id,
                    ))
        for index, (a_start, a_end, a_task) in enumerate(regular):
            for b_start, b_end, b_task in regular[index + 1:]:
                if a_start < b_end and a_end > b_start:
                    result.fixed_conflicts.append(FixedTaskConflict(
                        a_task.title, a_start.time(), a_end.time(),
                        b_task.title, b_start.time(), b_end.time(),
                        task_a_id=a_task.task_id, task_b_id=b_task.task_id,
                    ))

        permanent = self._merge(external + [(start, end) for start, end, _ in fixed_windows])
        placed_flexible: list[tuple[datetime, datetime, ScheduledTask]] = []
        pending = [task for task in converted if not task.is_fixed_time]

        def occupied_without(task: ScheduledTask) -> list[tuple[datetime, datetime]]:
            return self._merge(permanent + [
                (start, end) for start, end, owner in placed_flexible if owner is not task
            ])

        def place_whole_filler(task: ScheduledTask, start: datetime) -> None:
            end = start + timedelta(minutes=task.estimated_minutes)
            task.segments = [TaskSegment(0, start.time(), end.time())]
            task.scheduled_start, task.scheduled_end = start.time(), end.time()
            placed_flexible.append((start, end, task))
            result.placed_tasks.append(task)
            pending.remove(task)

        while pending:
            primary = pending[0]
            remaining = primary.estimated_minutes
            tentative: list[tuple[datetime, datetime]] = []
            cursor, task_boundary = self._task_bounds(
                primary, plan_date, day_start, boundary,
            )
            initial_occupied = occupied_without(primary)
            feasible_minutes = self._free_minutes(cursor, initial_occupied, task_boundary)
            urgency = (
                min(1.0, primary.estimated_minutes / max(1, feasible_minutes))
                if primary.deadline is not None else 0.0
            )
            session_profile = build_session_profile(
                primary.estimated_minutes,
                title=primary.title,
                category=primary.category,
                description=primary.description,
                urgency=urgency,
            )
            failure_reason: Optional[str] = None

            while remaining > 0:
                occupied = self._merge(occupied_without(primary) + tentative)
                cursor = self._next_free(cursor, occupied)
                if cursor >= task_boundary:
                    failure_reason = "capacity_shortfall"
                    break
                overlap = self._next_overlap(cursor, remaining, occupied)
                if overlap is None:
                    end = cursor + timedelta(minutes=remaining)
                    if end <= task_boundary and remaining >= MIN_SEGMENT_MINUTES:
                        available_segments = MAX_SEGMENTS_PER_TASK - len(tentative)
                        if available_segments < 1:
                            failure_reason = "fragmentation_segment_cap"
                            break
                        session_lengths = best_session_lengths(
                            remaining, session_profile,
                            max_segments=available_segments,
                        )
                        # If the same uninterrupted window has spare room, a
                        # legal peer may sit between long-task sessions.  This
                        # prevents a long task from monopolising the day while
                        # never risking the primary task's completion.
                        free_end = min(
                            (start for start, _ in occupied if start > cursor),
                            default=task_boundary,
                        )
                        spare = int((free_end - cursor).total_seconds() // 60) - remaining
                        filler = None
                        if len(session_lengths) > 1 and spare >= MIN_SEGMENT_MINUTES:
                            first_end = cursor + timedelta(minutes=session_lengths[0])
                            filler = self._best_gap_filler(
                                pending, primary, spare, first_end, free_end,
                                plan_date, day_start, boundary,
                            )
                        position = cursor
                        for index, minutes in enumerate(session_lengths):
                            session_end = position + timedelta(minutes=minutes)
                            tentative.append((position, session_end))
                            position = session_end
                            if index == 0 and filler is not None:
                                place_whole_filler(filler, position)
                                position += timedelta(minutes=filler.estimated_minutes)
                        remaining = 0
                        break
                    failure_reason = (
                        "capacity_shortfall"
                        if self._free_minutes(cursor, occupied, task_boundary) < remaining
                        else "fragmentation_segment_cap"
                    )
                    break

                block_start, block_end = overlap
                gap = int((block_start - cursor).total_seconds() // 60)
                available_segments = MAX_SEGMENTS_PER_TASK - len(tentative)
                preferred_remaining = (
                    best_session_lengths(
                        remaining, session_profile,
                        max_segments=available_segments,
                    )
                    if remaining >= MIN_SEGMENT_MINUTES and available_segments > 0
                    else ()
                )
                # A real protected interval is a useful natural boundary for a
                # long task whose inferred session shape prefers splitting.
                # Short tasks still skip the gap when a later whole window fits.
                if (
                    len(preferred_remaining) > 1
                    and gap >= MIN_SEGMENT_MINUTES
                    and available_segments > 1
                ):
                    allocation = min(gap, preferred_remaining[0])
                    leftover = remaining - allocation
                    if 0 < leftover < MIN_SEGMENT_MINUTES:
                        allocation -= MIN_SEGMENT_MINUTES - leftover
                    if allocation >= MIN_SEGMENT_MINUTES:
                        end = cursor + timedelta(minutes=allocation)
                        tentative.append((cursor, end))
                        remaining -= allocation
                        cursor = block_end
                        continue
                filler = self._best_gap_filler(
                    pending, primary, gap, cursor, block_start,
                    plan_date, day_start, boundary,
                ) if gap > 0 else None
                if filler is not None:
                    place_whole_filler(filler, cursor)
                    continue

                later = self._some_later_window_fits(
                    block_end, remaining, occupied, task_boundary,
                )
                if later is not None:
                    cursor = later
                    continue

                if self._free_minutes(cursor, occupied, task_boundary) < remaining:
                    failure_reason = "capacity_shortfall"
                    break
                if gap < MIN_SEGMENT_MINUTES or len(tentative) >= MAX_SEGMENTS_PER_TASK - 1:
                    cursor = block_end
                    if len(tentative) >= MAX_SEGMENTS_PER_TASK - 1:
                        failure_reason = "fragmentation_segment_cap"
                        break
                    continue

                allocation = min(gap, remaining)
                leftover = remaining - allocation
                if 0 < leftover < MIN_SEGMENT_MINUTES:
                    allocation -= MIN_SEGMENT_MINUTES - leftover
                if allocation < MIN_SEGMENT_MINUTES:
                    failure_reason = "fragmentation_segment_cap"
                    break
                end = cursor + timedelta(minutes=allocation)
                tentative.append((cursor, end))
                remaining -= allocation
                cursor = block_end

            pending.remove(primary)
            if remaining or failure_reason:
                reason = failure_reason or "fragmentation_segment_cap"
                result.unplaced_tasks.append(UnplacedTask(
                    primary.task_id, primary.title, remaining, reason,
                    "Not enough free minutes remain today."
                    if reason == "capacity_shortfall"
                    else "Enough total time exists, but the available windows cannot fit within the segment limits.",
                ))
                primary.segments = []
                primary.scheduled_start = primary.scheduled_end = None
                continue

            primary.segments = [
                TaskSegment(index, start.time(), end.time())
                for index, (start, end) in enumerate(tentative)
            ]
            if len(primary.segments) > MAX_SEGMENTS_PER_TASK:
                raise AssertionError("Allocator exceeded MAX_SEGMENTS_PER_TASK.")
            if sum(segment.duration_minutes for segment in primary.segments) != primary.estimated_minutes:
                raise AssertionError("Allocator did not preserve exact task duration.")
            primary.scheduled_start = primary.segments[0].scheduled_start
            primary.scheduled_end = primary.segments[-1].scheduled_end
            for start, end in tentative:
                placed_flexible.append((start, end, primary))
            result.placed_tasks.append(primary)

        result.placed_tasks.sort(key=lambda task: task.order_index)
        return result
