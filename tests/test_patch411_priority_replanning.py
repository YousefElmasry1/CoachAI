import tempfile
import unittest
from datetime import time
from pathlib import Path

from database import Database, StaleScheduleError
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService


class Patch411PriorityReplanningTests(unittest.TestCase):
    """Priority replanning changes only the eligible current-day region."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "patch411.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user_id in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                (user_id, f"{user_id}@example.test", user_id.upper()),
            )
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user_id, "2026-09-30", "patch 4.1.1"),
            )
        self.db.connection.commit()
        self.plan = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='a'"
        )["plan_id"])
        self.other_plan = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='b'"
        )["plan_id"])
        self.service = SchedulerService(self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    @staticmethod
    def _minutes(start, end):
        return (int(end[:2]) * 60 + int(end[3:5])) - (
            int(start[:2]) * 60 + int(start[3:5])
        )

    def add_task(
        self,
        title,
        windows,
        *,
        priority=3,
        status="pending",
        started=False,
        running=False,
        fixed=False,
        is_break=False,
        user_id="a",
        plan_id=None,
    ):
        target_plan = plan_id or self.plan
        duration = sum(self._minutes(start, end) for start, end in windows)
        task_id = self.db.add_task(
            target_plan,
            user_id,
            title,
            estimated_minutes=duration,
            priority=priority,
            is_fixed_time=fixed or is_break,
            is_break=is_break,
            scheduled_start=windows[0][0] if len(windows) == 1 else None,
            scheduled_end=windows[0][1] if len(windows) == 1 else None,
        )
        if len(windows) > 1:
            for index, (start, end) in enumerate(windows):
                self.db.execute(
                    """INSERT INTO task_segments
                       (task_id,segment_index,scheduled_start,scheduled_end,
                        calendar_provenance,calendar_sync_status)
                       VALUES (?,?,?,?,?,?)""",
                    (task_id, index, f"{start}:00", f"{end}:00", "legacy_unknown", "not_synced"),
                )
            self.db.execute(
                "UPDATE tasks SET scheduled_start=?,scheduled_end=? WHERE task_id=?",
                (f"{windows[0][0]}:00", f"{windows[-1][1]}:00", task_id),
            )
        started_at = "2026-09-30 06:00:00" if started else None
        timer_started_at = "2026-09-30 06:30:00" if running else None
        self.db.execute(
            """UPDATE tasks SET status=?,started_at=?,timer_segment_started_at=?
               WHERE task_id=?""",
            (status, started_at, timer_started_at, task_id),
        )
        self.db.connection.commit()
        return task_id

    def add_new(self, title="New", minutes=60, priority=2, blocked=None):
        return self.service.add_and_schedule_task(
            self.plan,
            "a",
            SchedulingPreferences(time(9)),
            title=title,
            estimated_minutes=minutes,
            is_fixed_time=False,
            priority=priority,
            blocked_slots=blocked or [],
        )

    def windows(self, task_id, user_id="a"):
        return [
            (row["scheduled_start"], row["scheduled_end"])
            for row in self.db.get_task_segments(task_id, user_id)
        ]

    def test_started_task_is_locked_while_future_pool_reorders(self):
        oop = self.add_task("OOP", [("09:00", "10:00")], status="in_progress", started=True, running=True)
        db_task = self.add_task("DB", [("10:00", "11:00")], priority=3)
        os_task = self.add_task("OS", [("11:00", "12:10")], priority=2)
        network = self.add_task("Network", [("12:10", "13:10")], priority=4)
        locked = self.windows(oop)

        result = self.add_new("Algorithms", 60, 2)

        self.assertEqual(locked, self.windows(oop))
        self.assertEqual([("11:00:00", "12:10:00")], self.windows(os_task))
        self.assertEqual((("10:00:00", "11:00:00"),), result.segments)
        self.assertEqual([("12:10:00", "13:10:00")], self.windows(db_task))
        self.assertEqual([("13:10:00", "14:10:00")], self.windows(network))

    def test_paused_in_progress_task_is_still_locked(self):
        paused = self.add_task("Paused", [("09:00", "10:00")], status="in_progress", started=True)
        low = self.add_task("Low", [("10:00", "11:00")], priority=4)
        before = self.windows(paused)

        result = self.add_new("High", 60, 2)

        self.assertEqual(before, self.windows(paused))
        self.assertEqual((("10:00:00", "11:00:00"),), result.segments)
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(low))

    def test_completed_task_is_never_replanned(self):
        completed = self.add_task("Done", [("09:00", "10:00")], status="completed", started=True)
        medium = self.add_task("Medium", [("10:00", "11:00")], priority=3)
        before = self.windows(completed)

        result = self.add_new("High", 60, 2)

        self.assertEqual(before, self.windows(completed))
        self.assertEqual((("10:00:00", "11:00:00"),), result.segments)
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(medium))

    def test_new_critical_task_precedes_eligible_high_task(self):
        high = self.add_task("High", [("09:00", "10:00")], priority=2)

        result = self.add_new("Critical", 45, 1)

        self.assertEqual((("09:00:00", "09:45:00"),), result.segments)
        self.assertEqual([("09:45:00", "10:45:00")], self.windows(high))

    def test_all_existing_unstarted_flexible_tasks_compete_by_priority(self):
        low = self.add_task("Low", [("09:00", "10:00")], priority=4)
        high = self.add_task("High", [("10:00", "11:00")], priority=2)
        medium = self.add_task("Medium", [("11:00", "12:00")], priority=3)

        result = self.add_new("New high", 30, 2)

        self.assertEqual([("10:00:00", "11:00:00")], self.windows(high))
        self.assertEqual((("09:00:00", "09:30:00"),), result.segments)
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(medium))
        self.assertEqual([("12:00:00", "13:00:00")], self.windows(low))

    def test_fixed_task_remains_an_immutable_anchor(self):
        fixed = self.add_task("Fixed", [("10:00", "11:00")], fixed=True, priority=5)
        low = self.add_task("Low", [("11:00", "12:00")], priority=4)
        before = self.windows(fixed)

        result = self.add_new("High", 60, 2)

        self.assertEqual(before, self.windows(fixed))
        self.assertEqual((("09:00:00", "10:00:00"),), result.segments)
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(low))

    def test_break_remains_an_immutable_anchor(self):
        break_id = self.add_task("Break", [("10:00", "10:30")], is_break=True)
        low = self.add_task("Low", [("10:30", "11:30")], priority=4)
        before = self.windows(break_id)

        result = self.add_new("High", 60, 2)

        self.assertEqual(before, self.windows(break_id))
        self.assertEqual((("09:00:00", "10:00:00"),), result.segments)
        self.assertEqual([("10:30:00", "11:30:00")], self.windows(low))

    def test_calendar_block_remains_hard_occupancy(self):
        low = self.add_task("Low", [("10:00", "11:00")], priority=4)

        result = self.add_new("High", 60, 2, blocked=[(time(9), time(10))])

        self.assertEqual((("10:00:00", "11:00:00"),), result.segments)
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(low))

    def test_replanned_owned_segment_keeps_identity_and_becomes_retryable(self):
        low = self.add_task("Owned low", [("09:00", "10:00")], priority=4)
        segment = self.db.get_task_segments(low, "a")[0]
        self.db.update_segment_calendar_state(
            int(segment["segment_id"]),
            "a",
            google_event_id="evt-low",
            google_calendar_id="primary",
            calendar_provenance="coachai_created",
            calendar_sync_status="synced",
        )

        self.add_new("High", 60, 2)

        moved = self.db.get_task_segments(low, "a")[0]
        self.assertEqual("10:00:00", moved["scheduled_start"])
        self.assertEqual("11:00:00", moved["scheduled_end"])
        self.assertEqual("evt-low", moved["google_event_id"])
        self.assertEqual("primary", moved["google_calendar_id"])
        self.assertEqual("coachai_created", moved["calendar_provenance"])
        self.assertEqual("pending_update", moved["calendar_sync_status"])

    def test_priority_cannot_override_capacity(self):
        fixed = self.add_task("All day anchor", [("09:00", "23:59")], fixed=True)
        before = self.windows(fixed)

        result = self.add_new("Critical", 60, 1)

        self.assertFalse(result.placed)
        self.assertEqual("capacity_shortfall", result.reason)
        self.assertEqual(before, self.windows(fixed))
        self.assertEqual([], self.windows(result.task_id))

    def test_replanning_begins_after_protected_started_portion(self):
        outside = self.add_task("Earlier pending", [("09:00", "10:00")], priority=5)
        started = self.add_task("Started", [("10:00", "11:00")], status="in_progress", started=True)
        future = self.add_task("Future", [("11:00", "12:00")], priority=4)
        outside_before = self.windows(outside)
        started_before = self.windows(started)

        result = self.add_new("Critical", 60, 1)

        self.assertEqual(outside_before, self.windows(outside))
        self.assertEqual(started_before, self.windows(started))
        self.assertEqual((("11:00:00", "12:00:00"),), result.segments)
        self.assertEqual([("12:00:00", "13:00:00")], self.windows(future))

    def test_merely_scheduled_task_remains_eligible(self):
        scheduled = self.add_task("Scheduled only", [("09:00", "10:00")], priority=4)
        task = self.db.get_task(scheduled, "a")
        self.assertEqual("pending", task["status"])
        self.assertIsNone(task["started_at"])

        result = self.add_new("High", 60, 2)

        self.assertEqual((("09:00:00", "10:00:00"),), result.segments)
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(scheduled))

    def test_existing_unscheduled_critical_task_competes_before_new_low(self):
        critical = self.db.add_task(
            self.plan,
            "a",
            "Unscheduled critical",
            estimated_minutes=60,
            priority=1,
        )

        result = self.add_new(
            "New low",
            60,
            4,
            blocked=[(time(10), time(23, 59))],
        )

        self.assertEqual([("09:00:00", "10:00:00")], self.windows(critical))
        self.assertFalse(result.placed)
        self.assertEqual("capacity_shortfall", result.reason)
        self.assertEqual([], self.windows(result.task_id))

    def test_add_and_replan_creates_exactly_one_operation(self):
        self.add_task("Medium", [("09:00", "10:00")], priority=3)
        before = int(self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"])

        result = self.add_new("High", 60, 2)

        after = int(self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"])
        operation = self.db.fetch_one(
            "SELECT status,user_id,plan_id FROM schedule_operations WHERE operation_id=?",
            (result.operation_id,),
        )
        self.assertEqual(before + 1, after)
        self.assertEqual("applied", operation["status"])
        self.assertEqual("a", operation["user_id"])
        self.assertEqual(self.plan, int(operation["plan_id"]))

    def test_task_start_race_rejects_replan_without_any_write(self):
        existing = self.add_task("About to start", [("09:00", "10:00")], priority=4)
        before = self.db._schedule_snapshot(self.plan, "a")
        before_tasks = len(self.db.get_tasks_by_plan(self.plan, "a"))
        before_operations = int(self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM schedule_operations"
        )["n"])
        original = self.db.get_tasks_with_segments
        calls = 0

        def starts_during_replan(plan_id, user_id):
            nonlocal calls
            calls += 1
            rows = original(plan_id, user_id)
            if calls == 2:
                changed = [dict(row) for row in rows]
                for row in changed:
                    if int(row["task_id"]) == existing:
                        row["status"] = "in_progress"
                        row["started_at"] = "2026-09-30 06:30:00"
                return changed
            return rows

        self.db.get_tasks_with_segments = starts_during_replan
        try:
            with self.assertRaises(StaleScheduleError):
                self.add_new("High", 60, 2)
        finally:
            self.db.get_tasks_with_segments = original

        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))
        self.assertEqual(before_tasks, len(self.db.get_tasks_by_plan(self.plan, "a")))
        self.assertEqual(before_operations, int(self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM schedule_operations"
        )["n"]))

    def test_undo_removes_new_task_and_restores_exact_previous_schedule(self):
        medium = self.add_task("Medium", [("09:00", "10:00")], priority=3)
        low = self.add_task("Low", [("10:00", "11:00")], priority=4)
        before = self.db._schedule_snapshot(self.plan, "a")

        result = self.add_new("Critical", 45, 1)
        self.assertNotEqual(before, self.db._schedule_snapshot(self.plan, "a"))
        self.db.undo_schedule_operation(result.operation_id, "a")

        self.assertIsNone(self.db.get_task(result.task_id, "a"))
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))
        self.assertEqual([("09:00:00", "10:00:00")], self.windows(medium))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(low))

    def test_pending_task_outside_affected_region_is_preserved(self):
        early = self.add_task("Early pending", [("09:00", "10:00")], priority=5)
        self.add_task("Completed", [("10:00", "11:00")], status="completed", started=True)
        late = self.add_task("Late low", [("11:00", "12:00")], priority=4)
        early_before = self.windows(early)

        result = self.add_new("High", 30, 2)

        self.assertEqual(early_before, self.windows(early))
        self.assertEqual((("11:00:00", "11:30:00"),), result.segments)
        self.assertEqual([("11:30:00", "12:30:00")], self.windows(late))

    def test_tenant_schedule_and_operations_are_isolated(self):
        other = self.add_task(
            "User B", [("09:00", "10:00")], priority=4,
            user_id="b", plan_id=self.other_plan,
        )
        other_before = self.db._schedule_snapshot(self.other_plan, "b")

        result = self.add_new("User A high", 60, 2)

        self.assertEqual(other_before, self.db._schedule_snapshot(self.other_plan, "b"))
        self.assertEqual([("09:00:00", "10:00:00")], self.windows(other, "b"))
        operation = self.db.fetch_one(
            "SELECT user_id,plan_id FROM schedule_operations WHERE operation_id=?",
            (result.operation_id,),
        )
        self.assertEqual("a", operation["user_id"])
        self.assertEqual(self.plan, int(operation["plan_id"]))

    def test_same_priority_preserves_existing_windows_when_new_task_fits(self):
        first = self.add_task("First", [("10:00", "10:30")], priority=2)
        second = self.add_task("Second", [("11:00", "11:30")], priority=2)
        first_before = self.windows(first)
        second_before = self.windows(second)

        result = self.add_new("New peer", 30, 2)

        self.assertEqual(first_before, self.windows(first))
        self.assertEqual(second_before, self.windows(second))
        self.assertEqual((("09:00:00", "09:30:00"),), result.segments)

    def test_duration_feasibility_yields_to_a_fixed_anchor(self):
        fixed = self.add_task("Fixed", [("10:00", "11:00")], fixed=True)
        high = self.add_task("Long high", [("11:00", "12:30")], priority=2)

        result = self.add_new("Critical short", 30, 1)

        self.assertEqual((("09:00:00", "09:30:00"),), result.segments)
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(fixed))
        self.assertEqual([("11:00:00", "12:30:00")], self.windows(high))

    def test_existing_split_task_replans_with_exact_duration_and_segment_rules(self):
        split = self.add_task(
            "Existing split", [("10:00", "10:30"), ("12:00", "12:30")], priority=4,
        )

        result = self.add_new("High", 30, 2, blocked=[(time(9, 30), time(10))])

        self.assertEqual((("09:00:00", "09:30:00"),), result.segments)
        split_windows = self.windows(split)
        duration = sum(self._minutes(start, end) for start, end in split_windows)
        self.assertEqual(60, duration)
        self.assertLessEqual(len(split_windows), 3)
        self.assertTrue(all(self._minutes(start, end) >= 15 for start, end in split_windows))

    def test_fragmented_capacity_reports_segment_cap_without_partial_rows(self):
        blocked = [
            (time(9, 15), time(9, 30)),
            (time(9, 45), time(10)),
            (time(10, 15), time(10, 30)),
            (time(10, 45), time(23, 59)),
        ]

        result = self.add_new("Four fragments", 60, 1, blocked=blocked)

        self.assertFalse(result.placed)
        self.assertEqual("fragmentation_segment_cap", result.reason)
        self.assertEqual([], self.windows(result.task_id))


if __name__ == "__main__":
    unittest.main()
