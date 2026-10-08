"""Deterministic multi-day orchestration over CoachAI's segmented scheduler model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from itertools import combinations
from typing import Optional

from config import (
    MAX_FUTURE_PLANNING_WEEKS, MAX_SEGMENTS_PER_TASK,
    MIN_SEGMENT_MINUTES, WORK_DAY_END,
)
from database import Database
from scheduler import SchedulingPreferences
from session_suitability import (
    balanced_session_lengths,
    build_session_profile,
    fit_session_lengths,
    is_reasonable_existing,
    session_quality,
)
from smart_scheduler import SmartScheduler
from timezone_utils import user_today


@dataclass(frozen=True)
class FutureSegment:
    scheduled_date: date
    scheduled_start: time
    scheduled_end: time
    segment_index: int

    @property
    def minutes(self) -> int:
        return int((datetime.combine(self.scheduled_date, self.scheduled_end) -
                    datetime.combine(self.scheduled_date, self.scheduled_start)).total_seconds() // 60)


@dataclass(frozen=True)
class FutureOutcome:
    task_id: Optional[int]
    title: str
    placed: bool
    reason: str
    message: str
    remaining_minutes: int
    segments: tuple[FutureSegment, ...] = ()
    candidate_id: Optional[str] = None
    is_draft: bool = False


@dataclass(frozen=True)
class FutureChange:
    task_id: Optional[int]
    title: str
    kind: str
    old_segments: tuple[FutureSegment, ...]
    new_segments: tuple[FutureSegment, ...]
    explanation: str
    candidate_id: Optional[str] = None
    is_draft: bool = False


@dataclass
class FuturePlanPreview:
    user_id: str
    planning_start: date
    planning_end: date
    work_day_start: time
    expected_revisions: dict[int, int]
    expected_task_states: dict[int, dict]
    segments_by_task: dict[int, list[dict]]
    task_updates: dict[int, dict] = field(default_factory=dict)
    draft_tasks: dict[str, dict] = field(default_factory=dict)
    draft_segments: dict[str, list[dict]] = field(default_factory=dict)
    expected_anchor_plan_id: Optional[int] = None
    outcomes: list[FutureOutcome] = field(default_factory=list)
    changes: list[FutureChange] = field(default_factory=list)
    operation_id: Optional[str] = None


class FuturePlanningService:
    """Compute, preview, validate, and atomically apply a future horizon."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def validate_horizon(planning_start: date, planning_end: date) -> None:
        """Enforce the product's explicit, non-truncating 12-week limit."""
        if planning_start > planning_end:
            raise ValueError("planning_start must be on or before planning_end.")
        maximum = planning_start + timedelta(weeks=MAX_FUTURE_PLANNING_WEEKS)
        if planning_end > maximum:
            raise ValueError(
                f"Future Planning supports at most {MAX_FUTURE_PLANNING_WEEKS} weeks "
                f"({planning_start.isoformat()} through {maximum.isoformat()}). "
                "Choose a shorter range; the requested range was not truncated or saved."
            )

    @staticmethod
    def _parse_dt(value) -> Optional[datetime]:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=None)

    @staticmethod
    def _parse_time(value) -> time:
        if isinstance(value, time):
            return value.replace(microsecond=0)
        return time.fromisoformat(str(value)).replace(microsecond=0)

    @staticmethod
    def _dates(start: date, end: date):
        cursor = start
        while cursor <= end:
            yield cursor
            cursor += timedelta(days=1)

    def _existing(self, task: dict) -> tuple[FutureSegment, ...]:
        return tuple(
            FutureSegment(
                date.fromisoformat(str(row.get("effective_date") or row.get("scheduled_date") or task["plan_date"])),
                self._parse_time(row["scheduled_start"]), self._parse_time(row["scheduled_end"]),
                int(row["segment_index"]),
            ) for row in task.get("segments", [])
        )

    @staticmethod
    def _state(task: dict) -> dict:
        return {
            "status": task["status"], "estimated_minutes": int(task["estimated_minutes"]),
            "actual_minutes": task.get("actual_minutes"),
            "started_at": str(task.get("started_at")) if task.get("started_at") else None,
            "priority": int(task["priority"]),
            "earliest_start": str(task.get("earliest_start")) if task.get("earliest_start") else None,
            "deadline": str(task.get("deadline")) if task.get("deadline") else None,
        }

    @staticmethod
    def _overlaps(a_start, a_end, b_start, b_end) -> bool:
        return a_start < b_end and a_end > b_start

    def _free_windows(
        self, days: list[date], work_day_start: time,
        occupied: dict[date, list[tuple[time, time]]], earliest: datetime, latest: datetime,
    ) -> list[tuple[datetime, datetime]]:
        windows: list[tuple[datetime, datetime]] = []
        for day in days:
            day_start = max(datetime.combine(day, work_day_start), earliest)
            day_end = min(datetime.combine(day, WORK_DAY_END), latest)
            if day_end <= day_start:
                continue
            cursor = day_start
            raw = [(datetime.combine(day, s), datetime.combine(day, e))
                   for s, e in occupied.get(day, [])]
            for block_start, block_end in SmartScheduler._merge(raw):
                if block_end <= cursor:
                    continue
                if block_start > cursor:
                    windows.append((cursor, min(block_start, day_end)))
                cursor = max(cursor, block_end)
                if cursor >= day_end:
                    break
            if cursor < day_end:
                windows.append((cursor, day_end))
        return [(s, e) for s, e in windows
                if int((e - s).total_seconds() // 60) >= MIN_SEGMENT_MINUTES]

    @staticmethod
    def _fits_existing(
        existing: tuple[FutureSegment, ...], remaining: int, earliest: datetime, latest: datetime,
        occupied: dict[date, list[tuple[time, time]]], horizon_start: date, horizon_end: date,
    ) -> bool:
        if not existing or len(existing) > MAX_SEGMENTS_PER_TASK:
            return False
        if sum(s.minutes for s in existing) != remaining:
            return False
        for segment in existing:
            start = datetime.combine(segment.scheduled_date, segment.scheduled_start)
            end = datetime.combine(segment.scheduled_date, segment.scheduled_end)
            if not (horizon_start <= segment.scheduled_date <= horizon_end):
                return False
            if segment.minutes < MIN_SEGMENT_MINUTES or start < earliest or end > latest:
                return False
            if any(FuturePlanningService._overlaps(segment.scheduled_start, segment.scheduled_end, a, b)
                   for a, b in occupied.get(segment.scheduled_date, [])):
                return False
        return True

    @staticmethod
    @lru_cache(maxsize=8192)
    def _allocation_amounts(
        capacities: tuple[int, ...], remaining: int,
    ) -> Optional[tuple[int, ...]]:
        if sum(capacities) < remaining or remaining < len(capacities) * MIN_SEGMENT_MINUTES:
            return None
        amounts = [MIN_SEGMENT_MINUTES] * len(capacities)
        left = remaining - sum(amounts)
        extras = [capacity - MIN_SEGMENT_MINUTES for capacity in capacities]
        # This is exactly the former 15-minute round-robin loop, collapsed
        # into complete rounds plus at most one partial round.  Most horizon
        # combinations share the same capacity tuple, so the pure result is
        # also safely reusable from the bounded cache.
        upper = max(((extra + 14) // 15 for extra in extras), default=0)
        low, high = 0, upper
        while low < high:
            rounds = (low + high + 1) // 2
            used = sum(min(extra, rounds * 15) for extra in extras)
            if used <= left:
                low = rounds
            else:
                high = rounds - 1
        used = 0
        for index, extra in enumerate(extras):
            addition = min(extra, low * 15)
            amounts[index] += addition
            used += addition
        left -= used
        if left:
            for index, capacity in enumerate(capacities):
                if left <= 0:
                    break
                chunk = min(left, capacity - amounts[index], 15)
                amounts[index] += chunk
                left -= chunk
        if left:
            return None
        return tuple(amounts)

    @staticmethod
    def _allocate_combination(
        combo: tuple[tuple[datetime, datetime], ...], remaining: int,
    ) -> Optional[tuple[FutureSegment, ...]]:
        capacities = tuple(int((end - start).total_seconds() // 60) for start, end in combo)
        amounts = fit_session_lengths(remaining, capacities)
        if amounts is None:
            return None
        return tuple(
            FutureSegment(start.date(), start.time(), (start + timedelta(minutes=amount)).time(), index)
            for index, ((start, _), amount) in enumerate(zip(combo, amounts))
        )

    def _best_placement(
        self, windows: list[tuple[datetime, datetime]], remaining: int,
        daily_load: dict[date, int], existing: tuple[FutureSegment, ...],
        *, task: Optional[dict] = None, urgency: float = 0.0,
    ) -> Optional[tuple[FutureSegment, ...]]:
        best = None
        best_score = None
        metadata = task or {}
        profile = build_session_profile(
            remaining,
            title=str(metadata.get("title") or ""),
            category=str(metadata.get("category_name") or metadata.get("category") or ""),
            description=str(metadata.get("description") or ""),
            urgency=urgency,
        )
        base_max = max(daily_load.values(), default=0)
        base_sum_squares = sum(value * value for value in daily_load.values())
        window_data = [
            (start, end, int((end - start).total_seconds() // 60))
            for start, end in windows
        ]

        def evaluate(candidate: tuple[FutureSegment, ...]) -> None:
            nonlocal best, best_score
            increments: dict[date, int] = {}
            for segment in candidate:
                increments[segment.scheduled_date] = (
                    increments.get(segment.scheduled_date, 0) + segment.minutes
                )
            resulting_max = base_max
            resulting_sum_squares = base_sum_squares
            for day, increment in increments.items():
                previous = daily_load.get(day, 0)
                updated = previous + increment
                resulting_max = max(resulting_max, updated)
                resulting_sum_squares += updated * updated - previous * previous
            count = len(candidate)
            score = (
                session_quality((segment.minutes for segment in candidate), profile),
                count,
                resulting_max, resulting_sum_squares,
                tuple((segment.scheduled_date, segment.scheduled_start)
                      for segment in candidate),
            )
            if best_score is None or score < best_score:
                best = candidate
                best_score = score

        def consider(indices: tuple[int, ...]) -> None:
            selected = tuple(window_data[index] for index in indices)
            candidate = self._allocate_combination(
                tuple((start, end) for start, end, _ in selected), remaining,
            )
            if candidate is not None:
                evaluate(candidate)

        def consider_sessions_in_window(window_index: int, count: int) -> None:
            start, _, capacity = window_data[window_index]
            if capacity < remaining or remaining < count * MIN_SEGMENT_MINUTES:
                return
            lengths = balanced_session_lengths(remaining, count)
            cursor = start
            candidate = []
            for index, minutes in enumerate(lengths):
                end = cursor + timedelta(minutes=minutes)
                candidate.append(FutureSegment(
                    cursor.date(), cursor.time(), end.time(), index,
                ))
                cursor = end
            evaluate(tuple(candidate))

        size = len(window_data)
        for count in range(1, min(2, MAX_SEGMENTS_PER_TASK) + 1):
            for indices in combinations(range(size), count):
                consider(indices)
        for window_index in range(size):
            for count in range(2, MAX_SEGMENTS_PER_TASK + 1):
                consider_sessions_in_window(window_index, count)

        if MAX_SEGMENTS_PER_TASK >= 3 and size >= 3:
            # For a fixed first/second window and third-window capacity, the
            # allocation amounts are fixed.  A third window on a different
            # day is therefore ordered solely by that day's current load and
            # then by time.  Only that best different day, the two already
            # selected days, or the exact existing segment can win.  This is
            # an exact reduction of the cubic search, not a heuristic/beam.
            suffix_options: list[dict[int, tuple[list[int], dict[date, int]]]] = [
                {} for _ in range(size)
            ]
            for second in range(1, size - 1):
                grouped: dict[int, dict[date, int]] = {}
                for third in range(second + 1, size):
                    start, _, capacity = window_data[third]
                    grouped.setdefault(capacity, {}).setdefault(start.date(), third)
                suffix_options[second] = {
                    capacity: (
                        sorted(
                            by_day.values(),
                            key=lambda index: (
                                daily_load.get(window_data[index][0].date(), 0),
                                window_data[index][0],
                            ),
                        ),
                        by_day,
                    )
                    for capacity, by_day in grouped.items()
                }

            existing_third = None
            if len(existing) == 3:
                for index, (start, _, _) in enumerate(window_data):
                    if (
                        start.date() == existing[2].scheduled_date
                        and start.time() == existing[2].scheduled_start
                    ):
                        existing_third = index
                        break

            for first, second in combinations(range(size), 2):
                special_days = {
                    window_data[first][0].date(), window_data[second][0].date(),
                }
                for capacity, (ranked, by_day) in suffix_options[second].items():
                    candidates = {
                        by_day[day] for day in special_days if day in by_day
                    }
                    for third in ranked:
                        if window_data[third][0].date() not in special_days:
                            candidates.add(third)
                            break
                    if (
                        existing_third is not None
                        and existing_third > second
                        and window_data[existing_third][2] == capacity
                    ):
                        candidates.add(existing_third)
                    for third in candidates:
                        consider((first, second, third))
        return best

    def preview(
        self, user_id: str, planning_start: date, planning_end: date,
        preferences: SchedulingPreferences,
        *,
        selected_task_ids: Optional[set[int]] = None,
        task_overrides: Optional[dict[int, dict]] = None,
        draft_candidates: Optional[list[dict]] = None,
    ) -> FuturePlanPreview:
        self.validate_horizon(planning_start, planning_end)
        if planning_start < user_today(self.db, user_id):
            raise ValueError("Future Planning cannot modify dates before the user's local today.")
        if preferences is None or preferences.work_day_start is None:
            raise ValueError("work_day_start is required.")
        if preferences.work_day_start >= WORK_DAY_END:
            raise ValueError("work_day_start must be before WORK_DAY_END.")
        plans = self.db.get_plans_in_range(user_id, planning_start, planning_end)
        revisions = {int(p["plan_id"]): int(p["schedule_revision"] or 0) for p in plans}
        anchor_plan = next(
            (p for p in plans if str(p["plan_date"]) == planning_start.isoformat()), None
        )
        expected_anchor_plan_id = int(anchor_plan["plan_id"]) if anchor_plan is not None else None
        tasks = self.db.get_future_planning_tasks(user_id, planning_start, planning_end)
        available_task_ids = {int(task["task_id"]) for task in tasks}
        selected = None if selected_task_ids is None else {int(value) for value in selected_task_ids}
        missing = set() if selected is None else selected - available_task_ids
        if missing:
            raise ValueError("A selected task is outside this owned planning scope.")
        overrides = {int(key): dict(value) for key, value in (task_overrides or {}).items()}
        if selected is not None and set(overrides) - selected:
            raise ValueError("Task constraints may only be changed for selected tasks.")
        draft_by_internal_id: dict[int, dict] = {}
        seen_temporary_ids: set[str] = set()
        for index, raw in enumerate(draft_candidates or []):
            draft = dict(raw)
            temporary_id = str(draft.get("temporary_id") or "").strip()
            title = str(draft.get("title") or "").strip()
            remaining = int(draft.get("remaining_minutes") or 0)
            priority = int(draft.get("priority") or 3)
            if not temporary_id or temporary_id in seen_temporary_ids:
                raise ValueError("Every draft task requires a unique temporary identifier.")
            if not title or remaining <= 0:
                raise ValueError("Every draft task requires a title and positive duration.")
            if not 1 <= priority <= 5:
                raise ValueError("Priority must be between 1 and 5.")
            earliest = self._parse_dt(draft.get("earliest_start"))
            deadline = self._parse_dt(draft.get("deadline"))
            if earliest and deadline and earliest > deadline:
                raise ValueError("Earliest start must be on or before the deadline.")
            seen_temporary_ids.add(temporary_id)
            internal_id = -(index + 1)
            normalized_draft = {
                "temporary_id": temporary_id,
                "title": title,
                "description": draft.get("description"),
                "remaining_minutes": remaining,
                "duration_source": draft.get("duration_source"),
                "priority": priority,
                "earliest_start": earliest.isoformat(sep=" ") if earliest else None,
                "deadline": deadline.isoformat(sep=" ") if deadline else None,
            }
            draft_by_internal_id[internal_id] = normalized_draft
            tasks.append({
                "task_id": internal_id,
                "_draft_temporary_id": temporary_id,
                "title": title,
                "description": draft.get("description"),
                "estimated_minutes": remaining,
                "actual_minutes": 0,
                "priority": priority,
                "earliest_start": normalized_draft["earliest_start"],
                "deadline": normalized_draft["deadline"],
                "status": "pending",
                "started_at": None,
                "is_fixed_time": 0,
                "is_break": 0,
                "plan_date": planning_start.isoformat(),
                "segments": [],
            })
        days = list(self._dates(planning_start, planning_end))
        occupied: dict[date, list[tuple[time, time]]] = {day: [] for day in days}
        daily_load: dict[date, int] = {day: 0 for day in days}
        scope_task_ids = {int(task["task_id"]) for task in tasks}
        all_scope_segments = self.db.get_segments_in_date_range(user_id, planning_start, planning_end)
        owned_event_ids = {
            str(segment["google_event_id"])
            for segment in all_scope_segments
            if segment["google_event_id"] and segment["calendar_provenance"] == "coachai_created"
        }
        # Segments whose logical task belongs outside the selected range are
        # unrelated work: they remain immutable occupancy inside the horizon.
        for segment in all_scope_segments:
            if int(segment["task_id"]) not in scope_task_ids:
                day = date.fromisoformat(str(segment["effective_date"]))
                occupied[day].append((self._parse_time(segment["scheduled_start"]),
                                      self._parse_time(segment["scheduled_end"])))
        for day in days:
            for event in self.db.get_google_calendar_events(user_id, day.isoformat()):
                if str(event["google_event_id"]) in owned_event_ids:
                    continue
                start, end = self._parse_time(event["start_time"]), self._parse_time(event["end_time"])
                if end > start:
                    occupied[day].append((start, end))
        eligible: list[dict] = []
        original_states: dict[int, dict] = {}
        normalized_updates: dict[int, dict] = {}
        for original in tasks:
            task_id = int(original["task_id"])
            task = dict(original)
            is_draft = task_id in draft_by_internal_id
            protected = (bool(task["is_fixed_time"]) or bool(task["is_break"])
                         or task["status"] in {"completed", "failed"} or bool(task.get("started_at")))
            explicitly_selected = is_draft or (selected is not None and task_id in selected)
            if protected or (selected is not None and not explicitly_selected):
                for segment in self._existing(task):
                    if planning_start <= segment.scheduled_date <= planning_end:
                        occupied[segment.scheduled_date].append((segment.scheduled_start, segment.scheduled_end))
                if protected and explicitly_selected:
                    raise ValueError(f"{task['title']} is protected and cannot be future-planned.")
                continue
            if not is_draft:
                original_states[task_id] = self._state(task)
            update = overrides.get(task_id, {})
            unknown = set(update) - {"estimated_minutes", "priority", "earliest_start", "deadline"}
            if unknown:
                raise ValueError(f"Unsupported task constraint(s): {', '.join(sorted(unknown))}.")
            if update:
                if "estimated_minutes" in update and int(update["estimated_minutes"]) < int(task.get("actual_minutes") or 0):
                    raise ValueError("Estimated minutes cannot be less than work already completed.")
                if "priority" in update and not 1 <= int(update["priority"]) <= 5:
                    raise ValueError("Priority must be between 1 and 5.")
                changed = {}
                for key, value in update.items():
                    current = task.get(key)
                    if key in {"estimated_minutes", "priority"}:
                        is_changed = int(value) != int(current)
                    else:
                        is_changed = self._parse_dt(value) != self._parse_dt(current)
                    if is_changed:
                        changed[key] = value
                task.update(update)
                earliest = self._parse_dt(task.get("earliest_start"))
                deadline = self._parse_dt(task.get("deadline"))
                if earliest and deadline and earliest > deadline:
                    raise ValueError("Earliest start must be on or before the deadline.")
                normalized = {
                    key: (value.isoformat(sep=" ") if isinstance(value, datetime) else value)
                    for key, value in changed.items()
                }
                if normalized:
                    normalized_updates[task_id] = normalized
            eligible.append(task)

        horizon_start_dt = datetime.combine(planning_start, preferences.work_day_start)
        horizon_end_dt = datetime.combine(planning_end, WORK_DAY_END)
        ranked = []
        for task in eligible:
            earliest = max(self._parse_dt(task.get("earliest_start")) or horizon_start_dt, horizon_start_dt)
            deadline = min(self._parse_dt(task.get("deadline")) or horizon_end_dt, horizon_end_dt)
            remaining = max(0, int(task["estimated_minutes"]) - int(task.get("actual_minutes") or 0))
            free = self._free_windows(days, preferences.work_day_start, occupied, earliest, deadline)
            capacity = sum(int((end - start).total_seconds() // 60) for start, end in free)
            slack = capacity - remaining
            is_draft = int(task["task_id"]) in draft_by_internal_id
            stable_tie = (1 if is_draft else 0, abs(int(task["task_id"])))
            ranked.append((slack, int(task["priority"]), deadline, stable_tie, task))
        ranked.sort(key=lambda item: item[:4])

        outcomes: list[FutureOutcome] = []
        changes: list[FutureChange] = []
        proposed: dict[int, list[dict]] = {}
        proposed_drafts: dict[str, list[dict]] = {}
        states: dict[int, dict] = {}
        for _, _, _, _, task in ranked:
            task_id, title = int(task["task_id"]), str(task["title"])
            draft = draft_by_internal_id.get(task_id)
            temporary_id = draft["temporary_id"] if draft else None
            if draft is None:
                states[task_id] = original_states[task_id]
            remaining = max(0, int(task["estimated_minutes"]) - int(task.get("actual_minutes") or 0))
            earliest_raw = self._parse_dt(task.get("earliest_start"))
            deadline_raw = self._parse_dt(task.get("deadline"))
            earliest = max(earliest_raw or horizon_start_dt, horizon_start_dt)
            latest = min(deadline_raw or horizon_end_dt, horizon_end_dt)
            existing = self._existing(task)
            candidate = None
            windows = (
                self._free_windows(days, preferences.work_day_start, occupied, earliest, latest)
                if earliest <= latest else []
            )
            capacity = sum(int((end - start).total_seconds() // 60)
                           for start, end in windows)
            urgency = (
                min(1.0, remaining / max(1, capacity))
                if deadline_raw is not None else 0.0
            )
            profile = build_session_profile(
                max(remaining, 1),
                title=title,
                category=str(task.get("category_name") or task.get("category") or ""),
                description=str(task.get("description") or ""),
                urgency=urgency,
            )
            if remaining == 0:
                candidate = ()
            elif earliest <= latest and self._fits_existing(
                existing, remaining, earliest, latest, occupied, planning_start, planning_end
            ) and is_reasonable_existing(
                (segment.minutes for segment in existing), profile,
            ):
                candidate = existing
            else:
                candidate = self._best_placement(
                    windows, remaining, daily_load, existing,
                    task=task, urgency=urgency,
                )
            if candidate is None:
                if deadline_raw and deadline_raw < horizon_start_dt:
                    reason = "deadline_impossible"
                elif earliest_raw and earliest_raw > horizon_end_dt:
                    reason = "earliest_start_outside_horizon"
                elif capacity < remaining:
                    reason = "insufficient_feasible_capacity"
                else:
                    reason = "fragmentation_segment_cap"
                message = (
                    f"{title} could not be fully scheduled: {capacity} feasible minutes are available "
                    f"for {remaining} remaining minutes ({reason.replace('_', ' ')})."
                )
                candidate = ()
                outcomes.append(FutureOutcome(
                    None if draft else task_id, title, False, reason, message, remaining,
                    candidate_id=temporary_id, is_draft=draft is not None,
                ))
            else:
                for segment in candidate:
                    occupied[segment.scheduled_date].append((segment.scheduled_start, segment.scheduled_end))
                    daily_load[segment.scheduled_date] += segment.minutes
                reason = "already_valid" if candidate == existing else "scheduled"
                message = (f"{title} kept its valid schedule." if reason == "already_valid" else
                           f"{title} was placed across {len(candidate)} segment(s) before its completion boundary.")
                outcomes.append(FutureOutcome(
                    None if draft else task_id, title, True, reason, message, remaining, tuple(candidate),
                    candidate_id=temporary_id, is_draft=draft is not None,
                ))
            payload = [{
                "segment_index": i, "scheduled_date": s.scheduled_date.isoformat(),
                "scheduled_start": s.scheduled_start.isoformat(), "scheduled_end": s.scheduled_end.isoformat(),
            } for i, s in enumerate(candidate)]
            if draft is None:
                proposed[task_id] = payload
            else:
                proposed_drafts[temporary_id] = payload
            normalized = tuple(FutureSegment(s.scheduled_date, s.scheduled_start, s.scheduled_end, i)
                               for i, s in enumerate(candidate))
            if normalized != existing:
                kind = "unscheduled" if not normalized else ("newly_scheduled" if not existing else "moved")
                changes.append(FutureChange(
                    None if draft else task_id, title, kind, existing, normalized, outcomes[-1].message,
                    candidate_id=temporary_id, is_draft=draft is not None,
                ))
        return FuturePlanPreview(
            user_id=user_id,
            planning_start=planning_start,
            planning_end=planning_end,
            work_day_start=preferences.work_day_start,
            expected_revisions=revisions,
            expected_task_states=states,
            segments_by_task=proposed,
            task_updates=normalized_updates,
            draft_tasks={
                draft["temporary_id"]: draft for draft in draft_by_internal_id.values()
            },
            draft_segments=proposed_drafts,
            expected_anchor_plan_id=expected_anchor_plan_id,
            outcomes=outcomes,
            changes=changes,
        )

    def preview_request(self, user_id: str, request) -> FuturePlanPreview:
        """Adapt one structured request into the existing planner input."""
        from planning_request import StructuredPlanningRequest

        if not isinstance(request, StructuredPlanningRequest):
            raise TypeError("request must be a StructuredPlanningRequest.")
        selected = {task.task_id for task in request.tasks if not task.is_draft}
        overrides: dict[int, dict] = {}
        drafts: list[dict] = []
        for requested in request.tasks:
            if requested.is_draft:
                drafts.append({
                    "temporary_id": requested.temporary_id,
                    "title": requested.title,
                    "description": requested.description,
                    "remaining_minutes": requested.remaining_minutes,
                    "duration_source": requested.duration_source,
                    "priority": requested.priority or 3,
                    "earliest_start": requested.earliest_start,
                    "deadline": requested.deadline,
                })
                continue
            row = self.db.get_task(requested.task_id, user_id)
            if row is None:
                raise ValueError("A selected task is not owned by this user.")
            update = {}
            if requested.remaining_minutes is not None:
                update["estimated_minutes"] = int(row["actual_minutes"] or 0) + requested.remaining_minutes
            if requested.priority is not None:
                update["priority"] = requested.priority
            if requested.earliest_start is not None:
                update["earliest_start"] = requested.earliest_start
            if requested.deadline is not None:
                update["deadline"] = requested.deadline
            if update:
                overrides[requested.task_id] = update
        return self.preview(
            user_id,
            request.planning_start,
            request.planning_end,
            SchedulingPreferences(work_day_start=request.work_day_start),
            selected_task_ids=selected,
            task_overrides=overrides,
            draft_candidates=drafts,
        )

    def apply(self, preview: FuturePlanPreview) -> str:
        # Apply is independently authoritative: callers cannot bypass the
        # product limit by constructing or mutating a preview outside the UI.
        self.validate_horizon(preview.planning_start, preview.planning_end)
        operation_id = self.db.apply_future_plan(
            preview.user_id, preview.segments_by_task,
            preview.expected_revisions, preview.expected_task_states,
            preview.task_updates,
            draft_tasks=preview.draft_tasks,
            draft_segments=preview.draft_segments,
            anchor_plan_date=preview.planning_start,
            expected_anchor_plan_id=preview.expected_anchor_plan_id,
        )
        preview.operation_id = operation_id
        return operation_id
