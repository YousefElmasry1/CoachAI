import tempfile
import unittest
from datetime import time
from pathlib import Path

from database import Database, StaleScheduleError
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService
from smart_scheduler import SmartScheduler


class Patch41PriorityIncrementalTests(unittest.TestCase):
    """Regression coverage updated for the approved 4.1.1 replanning rule."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "patch41.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                (user, f"{user}@example.test", user.upper()),
            )
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user, "2026-09-30", "patch 4.1"),
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

    def revision(self, plan_id=None, user_id="a"):
        row = self.db.get_plan_by_id(plan_id or self.plan, user_id)
        return int(row["schedule_revision"])

    def add_existing(
        self, title, start, end, *, priority=3, fixed=False, user_id="a", plan_id=None,
    ):
        target_plan = plan_id or self.plan
        minutes = (
            (int(end[:2]) * 60 + int(end[3:5]))
            - (int(start[:2]) * 60 + int(start[3:5]))
        )
        task_id = self.db.add_task(
            target_plan, user_id, title,
            estimated_minutes=minutes, priority=priority,
            is_fixed_time=fixed,
            scheduled_start=start if fixed else None,
            scheduled_end=end if fixed else None,
        )
        if not fixed:
            self.db.replace_plan_segments(
                target_plan, user_id, {task_id: [{
                    "segment_index": 0,
                    "scheduled_start": f"{start}:00",
                    "scheduled_end": f"{end}:00",
                }]}, self.revision(target_plan, user_id),
            )
        return task_id

    def add_incremental(self, title, minutes, priority, *, blocked=None):
        return self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title=title, estimated_minutes=minutes,
            is_fixed_time=False, priority=priority,
            blocked_slots=blocked or [],
        )

    def windows(self, task_id, user_id="a"):
        """Compare authoritative schedule/Calendar identity, not row surrogate IDs."""
        return [(
            row["segment_index"], row["scheduled_start"], row["scheduled_end"],
            row["google_event_id"], row["google_calendar_id"],
            row["calendar_provenance"], row["calendar_sync_status"],
        ) for row in self.db.get_task_segments(task_id, user_id)]

    def test_observed_medium_then_high_replans_both_by_priority(self):
        medium = self.add_existing("OOP", "10:00", "11:00", priority=3)
        result = self.add_incremental("OS", 70, priority=2)

        self.assertEqual((("09:00:00", "10:10:00"),), result.segments)
        self.assertEqual("10:10:00", self.windows(medium)[0][1])
        self.assertEqual("11:10:00", self.windows(medium)[0][2])
        self.assertEqual(2, self.db.get_task(result.task_id, "a")["priority"])

    def test_high_new_task_uses_earlier_gap_without_pointless_movement(self):
        medium = self.add_existing("Later medium", "11:00", "12:00", priority=3)
        before = self.windows(medium)
        result = self.add_incremental("Earlier high", 70, priority=2)

        self.assertEqual((("09:00:00", "10:10:00"),), result.segments)
        self.assertEqual(before, self.windows(medium))

    def test_high_new_task_never_moves_an_earlier_fixed_task(self):
        fixed = self.add_existing("Fixed", "09:00", "10:00", priority=4, fixed=True)
        before = self.windows(fixed)
        result = self.add_incremental("High", 70, priority=2)

        self.assertEqual((("10:00:00", "11:10:00"),), result.segments)
        self.assertEqual(before, self.windows(fixed))

    def test_high_new_task_reorders_lower_priority_existing_flexible_work(self):
        low = self.add_existing("Existing low", "09:00", "10:00", priority=4)
        result = self.add_incremental("New high", 70, priority=2)

        self.assertEqual((("09:00:00", "10:10:00"),), result.segments)
        self.assertEqual("10:10:00", self.windows(low)[0][1])
        self.assertEqual("11:10:00", self.windows(low)[0][2])

    def test_low_new_task_also_preserves_higher_priority_existing_work(self):
        high = self.add_existing("Existing high", "09:00", "10:00", priority=2)
        before = self.windows(high)
        result = self.add_incremental("New low", 70, priority=4)

        self.assertEqual((("10:00:00", "11:10:00"),), result.segments)
        self.assertEqual(before, self.windows(high))

        # Priority remains an actual normal-scheduler input when multiple
        # pending tasks compete to fill the same pre-block gap.
        allocation = SmartScheduler(
            SchedulingPreferences(time(9), blocked_slots=[(time(10), time(11))])
        ).allocate([
            {"task_id": 1, "title": "Primary", "estimated_minutes": 90, "priority": 3},
            {"task_id": 2, "title": "Low filler", "estimated_minutes": 30, "priority": 4},
            {"task_id": 3, "title": "High filler", "estimated_minutes": 30, "priority": 2},
        ], self._date())
        by_id = {task.task_id: task for task in allocation.placed_tasks}
        self.assertEqual(time(9), by_id[3].scheduled_start)
        self.assertEqual(time(9, 30), by_id[2].scheduled_start)

    def test_split_placement_preserves_duration_minimum_and_segment_cap(self):
        self.add_existing("Block", "10:00", "11:00", fixed=True)
        self.add_existing("Late block", "12:30", "23:30", fixed=True)
        result = self.add_incremental("Split high", 150, priority=2)

        self.assertEqual(
            (("09:00:00", "10:00:00"), ("11:00:00", "12:30:00")),
            result.segments,
        )
        durations = [
            (int(end[:2]) * 60 + int(end[3:5]))
            - (int(start[:2]) * 60 + int(start[3:5]))
            for start, end in result.segments
        ]
        self.assertEqual(150, sum(durations))
        self.assertTrue(all(minutes >= 15 for minutes in durations))
        self.assertLessEqual(len(durations), 3)

    def test_calendar_block_is_never_bypassed_by_priority(self):
        result = self.add_incremental(
            "Calendar-aware high", 70, priority=2,
            blocked=[(time(9), time(10))],
        )
        self.assertEqual((("10:00:00", "11:10:00"),), result.segments)

    def test_undo_restores_exact_schedule_and_removes_created_high_task(self):
        existing = self.add_existing("Existing", "10:00", "11:00", priority=3)
        before = self.db._schedule_snapshot(self.plan, "a")
        result = self.add_incremental("High", 70, priority=2)

        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertIsNone(self.db.get_task(result.task_id, "a"))
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))
        self.assertEqual(1, len(self.db.get_task_segments(existing, "a")))

    def test_stale_revision_rejects_incremental_add_without_writes(self):
        before = self.db._schedule_snapshot(self.plan, "a")
        before_count = len(self.db.get_tasks_by_plan(self.plan, "a"))
        original = self.db.get_plan_by_id
        calls = 0

        def stale_on_validation(plan_id, user_id):
            nonlocal calls
            calls += 1
            row = original(plan_id, user_id)
            if calls == 2:
                changed = dict(row)
                changed["schedule_revision"] = int(row["schedule_revision"]) + 1
                return changed
            return row

        self.db.get_plan_by_id = stale_on_validation
        try:
            with self.assertRaises(StaleScheduleError):
                self.add_incremental("Stale high", 70, priority=2)
        finally:
            self.db.get_plan_by_id = original

        self.assertEqual(before_count, len(self.db.get_tasks_by_plan(self.plan, "a")))
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))

    def test_incremental_priority_placement_is_tenant_isolated(self):
        other = self.add_existing(
            "User B", "09:00", "10:00", priority=4,
            user_id="b", plan_id=self.other_plan,
        )
        other_before = self.db._schedule_snapshot(self.other_plan, "b")
        result = self.add_incremental("User A high", 70, priority=2)

        self.assertIsNotNone(self.db.get_task(result.task_id, "a"))
        self.assertIsNone(self.db.get_task(result.task_id, "b"))
        self.assertEqual(other_before, self.db._schedule_snapshot(self.other_plan, "b"))
        self.assertEqual(1, len(self.db.get_task_segments(other, "b")))

    @staticmethod
    def _date():
        from datetime import date
        return date(2026, 9, 30)


if __name__ == "__main__":
    unittest.main()
