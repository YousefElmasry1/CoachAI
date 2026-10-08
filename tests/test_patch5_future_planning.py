import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

from analytics import BurnoutCalculator, TaskSnapshot, build_intermediate_stats
from database import Database, OwnershipError, StaleScheduleError
from future_planner import FuturePlanningService, FutureSegment
from scheduler import SchedulingPreferences
from session_suitability import (
    balanced_session_lengths,
    build_session_profile,
    session_quality,
)


class Patch5FuturePlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite:///{(Path(self.tmp.name) / 'patch5.db').as_posix()}")
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user, f"{user}@test.local", user.upper(), "UTC"),
            )
        self.db.connection.commit()
        self.days = [date(2026, 10, day) for day in (5, 6, 7)]
        self.today_patcher = patch("future_planner.user_today", return_value=self.days[0])
        self.today_patcher.start()
        self.addCleanup(self.today_patcher.stop)
        self.plans = {}
        for day in self.days:
            self.plans[day] = self.db.create_plan("a", day, "future")
        self.other_plan = self.db.create_plan("b", self.days[0], "other")
        self.service = FuturePlanningService(self.db)
        self.pref = SchedulingPreferences(work_day_start=time(8))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def task(self, title, minutes, *, day=None, priority=3, earliest=None, deadline=None,
             fixed=False, start=None, actual=None, user="a"):
        plan = self.other_plan if user == "b" else self.plans[day or self.days[0]]
        end = None
        if start:
            end = (datetime.combine(date.min, start) + __import__("datetime").timedelta(minutes=minutes)).time()
        task_id = self.db.add_task(
            plan, user, title, estimated_minutes=minutes, priority=priority,
            is_fixed_time=fixed, scheduled_start=start.isoformat() if start else None,
            scheduled_end=end.isoformat() if end else None,
            earliest_start=earliest, deadline=deadline,
        )
        if actual is not None:
            self.db.execute("UPDATE tasks SET actual_minutes=? WHERE task_id=?", (actual, task_id))
            self.db.connection.commit()
        return task_id

    def preview(self, start=None, end=None):
        return self.service.preview("a", start or self.days[0], end or self.days[-1], self.pref)

    def test_cached_allocation_matches_round_robin_semantics(self):
        capacity_cases = (
            (15,), (16,), (47,), (15, 15), (16, 100), (20, 35),
            (15, 15, 90), (17, 34, 100), (840, 840, 840),
        )
        for capacities in capacity_cases:
            minimum, maximum = len(capacities) * 15, sum(capacities)
            for remaining in range(minimum, maximum + 1):
                expected = [15] * len(capacities)
                left = remaining - sum(expected)
                while left:
                    progressed = False
                    for index, capacity in enumerate(capacities):
                        if expected[index] < capacity and left:
                            chunk = min(left, capacity - expected[index], 15)
                            expected[index] += chunk
                            left -= chunk
                            progressed = True
                    if not progressed:
                        expected = None
                        break
                actual = FuturePlanningService._allocation_amounts(capacities, remaining)
                self.assertEqual(None if expected is None else tuple(expected), actual)

    def test_optimized_three_segment_search_matches_brute_force(self):
        start = datetime(2026, 10, 5, 8)
        windows = (
            (start, start + timedelta(minutes=75)),
            (start + timedelta(minutes=90), start + timedelta(minutes=240)),
            (start + timedelta(days=1), start + timedelta(days=1, minutes=300)),
            (start + timedelta(days=2), start + timedelta(days=2, minutes=180)),
            (start + timedelta(days=3), start + timedelta(days=3, minutes=420)),
            (start + timedelta(days=4), start + timedelta(days=4, minutes=120)),
            (start + timedelta(days=5), start + timedelta(days=5, minutes=600)),
        )

        def brute(remaining, daily_load, existing=()):
            best, best_score = None, None
            profile = build_session_profile(remaining)
            candidates = []
            for count in range(1, 4):
                for combo in combinations(windows, count):
                    candidate = FuturePlanningService._allocate_combination(combo, remaining)
                    if candidate is None:
                        continue
                    candidates.append(candidate)
            for window_start, window_end in windows:
                capacity = int((window_end - window_start).total_seconds() // 60)
                for count in range(2, 4):
                    if capacity < remaining or remaining < count * 15:
                        continue
                    cursor = window_start
                    candidate = []
                    for index, minutes in enumerate(balanced_session_lengths(remaining, count)):
                        end = cursor + timedelta(minutes=minutes)
                        candidate.append(FutureSegment(
                            cursor.date(), cursor.time(), end.time(), index,
                        ))
                        cursor = end
                    candidates.append(tuple(candidate))
            for candidate in candidates:
                loads = dict(daily_load)
                for segment in candidate:
                    loads[segment.scheduled_date] = (
                        loads.get(segment.scheduled_date, 0) + segment.minutes
                    )
                score = (
                    session_quality((segment.minutes for segment in candidate), profile),
                    len(candidate),
                    max(loads.values(), default=0),
                    sum(value * value for value in loads.values()),
                    tuple((s.scheduled_date, s.scheduled_start) for s in candidate),
                )
                if best_score is None or score < best_score:
                    best, best_score = candidate, score
            return best

        load_cases = (
            {},
            {start.date(): 90, (start + timedelta(days=1)).date(): 300},
            {
                (start + timedelta(days=index)).date(): (index % 3) * 75
                for index in range(6)
            },
        )
        for remaining in (30, 60, 119, 180, 300, 515, 700):
            for daily_load in load_cases:
                expected = brute(remaining, daily_load)
                actual = self.service._best_placement(
                    list(windows), remaining, daily_load, (),
                )
                self.assertEqual(expected, actual)
                if expected is not None:
                    self.assertEqual(
                        brute(remaining, daily_load, expected),
                        self.service._best_placement(
                            list(windows), remaining, daily_load, expected,
                        ),
                    )

    def test_horizon_validation_and_outside_task_exclusion(self):
        with self.assertRaises(ValueError):
            self.preview(self.days[-1], self.days[0])
        outside = self.db.create_plan("a", date(2026, 10, 8), "outside")
        outside_task = self.db.add_task(outside, "a", "Outside", estimated_minutes=30)
        inside = self.task("Inside", 30)
        preview = self.preview()
        self.assertIn(inside, preview.segments_by_task)
        self.assertNotIn(outside_task, preview.segments_by_task)

    def test_twelve_week_horizon_boundary_is_inclusive(self):
        start = self.days[0]
        less_than_limit = self.service.preview(
            "a", start, start + timedelta(weeks=12) - timedelta(days=1), self.pref
        )
        exact_limit = self.service.preview(
            "a", start, start + timedelta(weeks=12), self.pref
        )

        self.assertEqual(start + timedelta(weeks=12) - timedelta(days=1),
                         less_than_limit.planning_end)
        self.assertEqual(start + timedelta(weeks=12), exact_limit.planning_end)

    def test_preview_rejects_over_twelve_weeks_without_mutation(self):
        task_id = self.task("Boundary", 60)
        start = self.days[0]
        before_revision = int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        )

        with self.assertRaisesRegex(
            ValueError, r"at most 12 weeks.*not truncated or saved"
        ):
            self.service.preview(
                "a", start, start + timedelta(weeks=12, days=1), self.pref
            )

        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        self.assertEqual(before_revision, int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        ))
        self.assertEqual(
            0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"]
        )

    def test_apply_revalidates_over_twelve_weeks_without_mutation(self):
        task_id = self.task("Bypass", 60)
        preview = self.preview()
        before_revision = int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        )
        preview.planning_end = preview.planning_start + timedelta(weeks=12, days=1)

        with self.assertRaisesRegex(ValueError, r"at most 12 weeks"):
            self.service.apply(preview)

        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        self.assertEqual(before_revision, int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        ))
        self.assertEqual(
            0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"]
        )

    def test_deadline_is_latest_completion_not_target_date(self):
        task_id = self.task("Deadline", 60, deadline=datetime(2026, 10, 7, 12))
        segment = self.preview().outcomes[0].segments[0]
        self.assertEqual(date(2026, 10, 5), segment.scheduled_date)
        self.assertLessEqual(datetime.combine(segment.scheduled_date, segment.scheduled_end), datetime(2026, 10, 7, 12))
        self.assertEqual(task_id, self.preview().outcomes[0].task_id)

    def test_exact_earliest_start_is_respected(self):
        self.task("Later", 60, earliest=datetime(2026, 10, 6, 10))
        segment = self.preview().outcomes[0].segments[0]
        self.assertGreaterEqual(datetime.combine(segment.scheduled_date, segment.scheduled_start), datetime(2026, 10, 6, 10))

    def test_remaining_work_not_original_estimate(self):
        self.task("Partial", 300, actual=120)
        outcome = self.preview().outcomes[0]
        self.assertEqual(180, outcome.remaining_minutes)
        self.assertEqual(180, sum(segment.minutes for segment in outcome.segments))

    def test_fixed_break_and_terminal_tasks_are_protected(self):
        fixed = self.task("Anchor", 60, fixed=True, start=time(8))
        flexible = self.task("Work", 60)
        preview = self.preview(self.days[0], self.days[0])
        self.assertNotIn(fixed, preview.segments_by_task)
        segment = next(o for o in preview.outcomes if o.task_id == flexible).segments[0]
        self.assertGreaterEqual(segment.scheduled_start, time(9))

    def test_external_calendar_event_is_hard_occupancy(self):
        self.db.upsert_google_calendar_event(
            "a", "external", "Busy", "08:00", "10:00", self.days[0].isoformat(), "primary",
        )
        target = self.task("After calendar", 60)
        outcome = next(o for o in self.preview(self.days[0], self.days[0]).outcomes if o.task_id == target)
        self.assertGreaterEqual(outcome.segments[0].scheduled_start, time(10))

    def test_out_of_range_logical_task_segment_remains_an_anchor(self):
        outside_day = date(2026, 10, 4)
        outside_plan = self.db.create_plan("a", outside_day, "outside anchor")
        anchor = self.db.add_task(outside_plan, "a", "Spanning anchor", estimated_minutes=120)
        self.db.execute(
            """INSERT INTO task_segments
               (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
               VALUES (?,?,?,?,?)""",
            (anchor, 0, self.days[0].isoformat(), "08:00", "10:00"),
        )
        self.db.connection.commit()
        target = self.task("Inside", 60)
        outcome = next(o for o in self.preview(self.days[0], self.days[0]).outcomes if o.task_id == target)
        self.assertGreaterEqual(outcome.segments[0].scheduled_start, time(10))

    def test_nonexistent_dst_constraint_is_rejected(self):
        self.db.execute("UPDATE users SET timezone='America/New_York' WHERE user_id='a'")
        self.db.connection.commit()
        with self.assertRaises(ValueError):
            self.task("DST gap", 30, earliest=datetime(2026, 3, 8, 2, 30))

    def test_urgency_before_priority_preserves_future_capacity(self):
        urgent = self.task("Urgent", 900, priority=3, deadline=datetime(2026, 10, 5, 23, 59, 59))
        low = self.task("Low", 900, priority=5, deadline=datetime(2026, 10, 7, 23, 59, 59))
        preview = self.preview()
        urgent_out = next(o for o in preview.outcomes if o.task_id == urgent)
        low_out = next(o for o in preview.outcomes if o.task_id == low)
        self.assertTrue(urgent_out.placed)
        self.assertEqual(self.days[0], urgent_out.segments[0].scheduled_date)
        self.assertTrue(low_out.placed)

    def test_session_quality_precedes_workload_balancing_for_long_task(self):
        self.task("Long", 600)
        outcome = self.preview().outcomes[0]
        self.assertTrue(outcome.placed)
        self.assertEqual(3, len(outcome.segments))
        self.assertEqual(600, sum(s.minutes for s in outcome.segments))
        self.assertGreater(len({s.scheduled_date for s in outcome.segments}), 1)

    def test_two_segments_are_used_only_when_no_whole_window_exists(self):
        for index, day in enumerate(self.days[:2]):
            self.db.upsert_google_calendar_event(
                "a", f"two-{index}", "Busy", "08:30", "23:59",
                day.isoformat(), "primary",
            )
        task_id = self.task("Needs two", 60)
        outcome = next(
            item for item in self.preview(self.days[0], self.days[1]).outcomes
            if item.task_id == task_id
        )
        self.assertTrue(outcome.placed)
        self.assertEqual(2, len(outcome.segments))
        self.assertEqual(60, sum(segment.minutes for segment in outcome.segments))
        self.assertTrue(all(segment.minutes >= 15 for segment in outcome.segments))

    def test_three_segments_are_used_only_when_required(self):
        for index, day in enumerate(self.days):
            self.db.upsert_google_calendar_event(
                "a", f"three-{index}", "Busy", "08:30", "23:59",
                day.isoformat(), "primary",
            )
        task_id = self.task("Needs three", 75)
        outcome = next(item for item in self.preview().outcomes if item.task_id == task_id)
        self.assertTrue(outcome.placed)
        self.assertEqual(3, len(outcome.segments))
        self.assertEqual(75, sum(segment.minutes for segment in outcome.segments))
        self.assertTrue(all(segment.minutes >= 15 for segment in outcome.segments))

    def test_workload_balancing_breaks_ties_after_continuity(self):
        existing = self.task("Existing load", 120, start=time(8))
        target = self.task("Whole target", 60)
        preview = self.preview()
        existing_outcome = next(item for item in preview.outcomes if item.task_id == existing)
        target_outcome = next(item for item in preview.outcomes if item.task_id == target)
        self.assertEqual(self.days[0], existing_outcome.segments[0].scheduled_date)
        self.assertEqual(1, len(target_outcome.segments))
        self.assertEqual(self.days[1], target_outcome.segments[0].scheduled_date)

    def test_valid_existing_multi_day_placement_remains_stable(self):
        task_id = self.task("Study stable split", 180)
        for index, day in enumerate(self.days[:2]):
            self.db.execute(
                """INSERT INTO task_segments
                   (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
                   VALUES (?,?,?,?,?)""",
                (task_id, index, day.isoformat(), "08:00", "09:30"),
            )
        self.db.connection.commit()
        outcome = next(item for item in self.preview().outcomes if item.task_id == task_id)
        self.assertEqual(
            [(self.days[0], time(8), time(9, 30)),
             (self.days[1], time(8), time(9, 30))],
            [(segment.scheduled_date, segment.scheduled_start, segment.scheduled_end)
             for segment in outcome.segments],
        )

    def test_contiguous_preview_apply_and_undo_are_exact(self):
        task_id = self.task("Exact continuity", 60)
        preview = self.preview()
        proposed = preview.segments_by_task[task_id]
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        operation = self.service.apply(preview)
        stored = self.db.get_task_segments(task_id, "a")
        self.assertEqual(
            [(item["scheduled_date"], item["scheduled_start"], item["scheduled_end"])
             for item in proposed],
            [(str(item["effective_date"]), str(item["scheduled_start"]), str(item["scheduled_end"]))
             for item in stored],
        )
        self.db.undo_schedule_operation(operation, "a")
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))

    def test_fragmentation_or_segment_cap_is_structured(self):
        for day in self.days:
            self.task(f"A{day}", 840, day=day, fixed=True, start=time(9))
        target = self.task("Needs four", 240)
        outcome = next(o for o in self.preview().outcomes if o.task_id == target)
        self.assertFalse(outcome.placed)
        self.assertIn(outcome.reason, {"insufficient_feasible_capacity", "fragmentation_segment_cap"})
        self.assertEqual((), outcome.segments)

    def test_preview_is_read_only_and_deterministic(self):
        task_id = self.task("Dry", 120)
        before = self.db.get_task_segments(task_id, "a")
        first, second = self.preview(), self.preview()
        self.assertEqual(first.segments_by_task, second.segments_by_task)
        self.assertEqual(before, self.db.get_task_segments(task_id, "a"))
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])

    def test_atomic_apply_one_operation_and_exact_undo(self):
        first = self.task("One", 120)
        second = self.task("Two", 180, day=self.days[1])
        revisions = dict(self.preview().expected_revisions)
        preview = self.preview()
        operation = self.service.apply(preview)
        self.assertEqual(1, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])
        self.assertTrue(self.db.get_task_segments(first, "a"))
        self.assertTrue(self.db.get_task_segments(second, "a"))
        self.db.undo_schedule_operation(operation, "a")
        self.assertEqual([], self.db.get_task_segments(first, "a"))
        self.assertEqual([], self.db.get_task_segments(second, "a"))
        for plan_id, old in revisions.items():
            self.assertEqual(old + 2, int(self.db.get_plan_by_id(plan_id, "a")["schedule_revision"]))

    def test_stale_revision_and_progress_race_write_nothing(self):
        task_id = self.task("Race", 60)
        preview = self.preview()
        self.db.execute("UPDATE plans SET schedule_revision=schedule_revision+1 WHERE plan_id=?", (self.plans[self.days[0]],))
        self.db.connection.commit()
        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])

    def test_validation_failure_rolls_back_every_plan_and_operation(self):
        self.task("Anchor", 120, fixed=True, start=time(8))
        target = self.task("Candidate", 60)
        preview = self.preview(self.days[0], self.days[0])
        before_revision = int(self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"])
        preview.segments_by_task[target][0]["scheduled_start"] = "08:30:00"
        preview.segments_by_task[target][0]["scheduled_end"] = "09:30:00"
        with self.assertRaises(ValueError):
            self.service.apply(preview)
        self.assertEqual(before_revision, int(self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]))
        self.assertEqual([], self.db.get_task_segments(target, "a"))
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])

    def test_tenant_isolation(self):
        other = self.task("Other", 60, user="b")
        mine = self.task("Mine", 60)
        preview = self.preview()
        self.assertIn(mine, preview.segments_by_task)
        self.assertNotIn(other, preview.segments_by_task)
        with self.assertRaises(OwnershipError):
            self.db.apply_future_plan("b", preview.segments_by_task, preview.expected_revisions, preview.expected_task_states)

    def test_calendar_identity_stays_only_for_unchanged_segment(self):
        task_id = self.task("Stable", 60)
        initial = self.preview()
        self.service.apply(initial)
        segment = self.db.get_task_segments(task_id, "a")[0]
        self.db.update_segment_calendar_state(
            segment["segment_id"], "a", google_event_id="owned", google_calendar_id="primary",
            calendar_provenance="coachai_created", calendar_sync_status="synced",
        )
        again = self.preview()
        self.service.apply(again)
        restored = self.db.get_task_segments(task_id, "a")[0]
        self.assertEqual("owned", restored["google_event_id"])
        self.assertEqual("coachai_created", restored["calendar_provenance"])

    def test_moved_owned_segment_keeps_identity_for_calendar_update(self):
        task_id = self.task("Move owned", 60)
        self.service.apply(self.preview())
        segment = self.db.get_task_segments(task_id, "a")[0]
        self.db.update_segment_calendar_state(
            segment["segment_id"], "a", google_event_id="owned", google_calendar_id="primary",
            calendar_provenance="coachai_created", calendar_sync_status="synced",
        )
        self.db.upsert_google_calendar_event(
            "a", "blocker", "External", str(segment["scheduled_start"]),
            str(segment["scheduled_end"]), str(segment["effective_date"]), "primary",
        )

        moved = self.preview()
        self.service.apply(moved)
        restored = self.db.get_task_segments(task_id, "a")[0]

        self.assertEqual("owned", restored["google_event_id"])
        self.assertEqual("coachai_created", restored["calendar_provenance"])
        self.assertEqual("pending_update", restored["calendar_sync_status"])

    def test_date_aware_analytics_counts_one_logical_task(self):
        snap = TaskSnapshot(
            task_id=1, plan_date="2026-10-05", title="Split", category_name="Study",
            priority=2, estimated_minutes=60, actual_minutes=None, scheduled_start="09:00",
            scheduled_end="10:30", status="pending", failure_reason=None, completed_at=None,
            created_at="2026-10-05", weekday=0, hour=9,
            segments=(("09:00", "09:30"), ("10:00", "10:30")),
            dated_segments=(("2026-10-05", "09:00", "09:30"), ("2026-10-06", "10:00", "10:30")),
        )
        stats = build_intermediate_stats([snap], window_days=3, today=date(2026, 10, 7))
        result = BurnoutCalculator().compute(stats)
        self.assertEqual(30.0, result.schedule_density)
        self.assertEqual(1, stats.total_tasks)


if __name__ == "__main__":
    unittest.main()
