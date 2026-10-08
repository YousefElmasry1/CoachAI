import tempfile
import unittest
from datetime import time
from pathlib import Path

from database import Database, OwnershipError, StaleScheduleError
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService


class Patch4CurrentDayTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "patch4.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                (user, f"{user}@example.test", user.upper()),
            )
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user, "2026-09-30", "patch4"),
            )
        self.db.connection.commit()
        self.plan = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='a'")["plan_id"]
        self.other_plan = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='b'")["plan_id"]
        self.service = SchedulerService(self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def revision(self):
        return int(self.db.get_plan_by_id(self.plan, "a")["schedule_revision"])

    def add(self, title, minutes=60, **kwargs):
        return self.db.add_task(self.plan, "a", title, estimated_minutes=minutes, **kwargs)

    @staticmethod
    def segment(start, end, **extra):
        return [{
            "segment_index": 0,
            "scheduled_start": start,
            "scheduled_end": end,
            **extra,
        }]

    def test_flexible_creation_places_in_gap_without_moving_existing_tasks_and_undo_deletes_it(self):
        fixed = self.add(
            "Meeting", 60, is_fixed_time=True,
            scheduled_start="09:00", scheduled_end="10:00",
        )
        existing = self.add("Existing", 60)
        self.db.replace_plan_segments(
            self.plan, "a",
            {
                fixed: self.segment("09:00:00", "10:00:00"),
                existing: self.segment("10:30:00", "11:30:00"),
            },
            self.revision(),
        )
        before = self.db._schedule_snapshot(self.plan, "a")
        result = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="New", estimated_minutes=30, is_fixed_time=False,
        )
        self.assertTrue(result.placed)
        self.assertEqual((("10:00:00", "10:30:00"),), result.segments)
        self.assertEqual(before["segments"], [
            row for row in self.db._schedule_snapshot(self.plan, "a")["segments"]
            if int(row["task_id"]) != result.task_id
        ])
        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertIsNone(self.db.get_task(result.task_id, "a"))
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))

    def test_flexible_creation_uses_later_whole_window_and_can_split(self):
        self.add("Block", 60, is_fixed_time=True, scheduled_start="10:00", scheduled_end="11:00")
        whole = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="Whole", estimated_minutes=90, is_fixed_time=False,
        )
        self.assertEqual((("11:00:00", "12:30:00"),), whole.segments)

        self.db.undo_schedule_operation(whole.operation_id, "a")
        self.add("Late block", 660, is_fixed_time=True, scheduled_start="12:30", scheduled_end="23:30")
        split = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="Split", estimated_minutes=150, is_fixed_time=False,
        )
        self.assertTrue(split.placed)
        self.assertEqual(2, len(split.segments))
        self.assertEqual(("09:00:00", "10:00:00"), split.segments[0])
        self.assertEqual(("11:00:00", "12:30:00"), split.segments[1])

    def test_unplaced_results_distinguish_capacity_and_fragmentation(self):
        self.add(
            "All day", 899, is_fixed_time=True,
            scheduled_start="09:00", scheduled_end="23:59:00",
        )
        capacity = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="No capacity", estimated_minutes=60, is_fixed_time=False,
        )
        self.assertFalse(capacity.placed)
        self.assertEqual("capacity_shortfall", capacity.reason)
        self.assertEqual([], self.db.get_task_segments(capacity.task_id, "a"))

        self.db.undo_schedule_operation(capacity.operation_id, "a")
        self.db.delete_task(self.db.get_tasks_by_plan(self.plan, "a")[-1]["task_id"], "a")

        # Four 15-minute gaps contain enough total time but require four segments.
        for index, (start, end) in enumerate((
            ("09:15", "09:30"), ("09:45", "10:00"),
            ("10:15", "10:30"), ("10:45", "23:59"),
        )):
            self.add(f"Block {index}", is_fixed_time=True, scheduled_start=start, scheduled_end=end)
        fragmented = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="Fragmented", estimated_minutes=60, is_fixed_time=False,
        )
        self.assertFalse(fragmented.placed)
        self.assertEqual("fragmentation_segment_cap", fragmented.reason)

    def test_fixed_creation_persists_exact_window_and_requires_start(self):
        result = self.service.add_and_schedule_task(
            self.plan, "a", SchedulingPreferences(time(9)),
            title="Fixed", estimated_minutes=45, is_fixed_time=True,
            fixed_start=time(14),
        )
        task = self.db.get_task(result.task_id, "a")
        self.assertEqual(1, task["is_fixed_time"])
        self.assertEqual((("14:00:00", "14:45:00"),), result.segments)
        with self.assertRaises(ValueError):
            self.service.add_and_schedule_task(
                self.plan, "a", SchedulingPreferences(time(9)),
                title="Invalid", estimated_minutes=30, is_fixed_time=True,
            )

        snapshot = self.db._schedule_snapshot(self.plan, "a")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.service.add_and_schedule_task(
                self.plan, "a", SchedulingPreferences(time(9)),
                title="Conflict", estimated_minutes=30, is_fixed_time=True,
                fixed_start=time(14, 30),
            )
        self.assertEqual(snapshot, self.db._schedule_snapshot(self.plan, "a"))

    def test_manual_move_collapses_split_task_and_is_exactly_undoable(self):
        task = self.add("Split", 60)
        self.db.replace_plan_segments(
            self.plan, "a", {task: [
                {"segment_index": 0, "scheduled_start": "09:00:00", "scheduled_end": "09:30:00"},
                {"segment_index": 1, "scheduled_start": "11:00:00", "scheduled_end": "11:30:00"},
            ]}, self.revision(),
        )
        before = self.db._schedule_snapshot(self.plan, "a")
        revision = self.revision()
        result = self.service.move_flexible_task(task, "a", time(19), revision)
        self.assertEqual((("19:00:00", "20:00:00"),), result.segments)
        self.assertEqual(1, len(self.db.get_task_segments(task, "a")))
        self.assertEqual(revision + 1, self.revision())
        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))

    def test_manual_move_rejects_overlap_fixed_stale_and_cross_tenant(self):
        flexible = self.add("Flexible", 60)
        fixed = self.add(
            "Fixed", 60, is_fixed_time=True,
            scheduled_start="10:00", scheduled_end="11:00",
        )
        self.db.replace_plan_segments(
            self.plan, "a",
            {flexible: self.segment("09:00:00", "10:00:00"), fixed: self.segment("10:00:00", "11:00:00")},
            self.revision(),
        )
        snapshot = self.db._schedule_snapshot(self.plan, "a")
        revision = self.revision()
        with self.assertRaises(ValueError):
            self.service.move_flexible_task(flexible, "a", time(10, 30), revision)
        with self.assertRaises(ValueError):
            self.service.move_flexible_task(fixed, "a", time(12), revision)
        self.assertEqual(snapshot, self.db._schedule_snapshot(self.plan, "a"))

        self.db.update_task(flexible, "a", title="changed")
        with self.assertRaises(StaleScheduleError):
            self.service.move_flexible_task(flexible, "a", time(12), revision)
        with self.assertRaises(OwnershipError):
            self.service.move_flexible_task(flexible, "b", time(12), self.revision())

    def test_manual_move_failure_rolls_back_segments_cache_revision_and_operation(self):
        task = self.add("Flexible", 60)
        self.db.replace_plan_segments(
            self.plan, "a", {task: self.segment("09:00:00", "10:00:00")}, self.revision(),
        )
        snapshot = self.db._schedule_snapshot(self.plan, "a")
        revision = self.revision()
        operation_count = self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"]
        original_execute = self.db.execute

        def fail_insert(sql, parameters=()):
            if "INSERT INTO schedule_operations" in sql:
                raise RuntimeError("injected operation failure")
            return original_execute(sql, parameters)

        self.db.execute = fail_insert
        with self.assertRaises(RuntimeError):
            self.service.move_flexible_task(task, "a", time(12), revision)
        self.db.execute = original_execute
        self.assertEqual(snapshot, self.db._schedule_snapshot(self.plan, "a"))
        self.assertEqual(revision, self.revision())
        self.assertEqual(operation_count, self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"])

    def test_manual_move_preserves_owned_calendar_identity_and_marks_update_pending(self):
        task = self.add("Calendar task", 60)
        self.db.replace_plan_segments(
            self.plan, "a", {task: [{
                "segment_index": 0,
                "scheduled_start": "09:00:00",
                "scheduled_end": "10:00:00",
                "google_event_id": "owned-event",
                "google_calendar_id": "primary",
                "calendar_provenance": "coachai_created",
                "calendar_sync_status": "synced",
            }]}, self.revision(),
        )
        result = self.service.move_flexible_task(task, "a", time(12), self.revision())
        segment = self.db.get_task_segments(task, "a")[0]
        self.assertEqual("owned-event", segment["google_event_id"])
        self.assertEqual("coachai_created", segment["calendar_provenance"])
        self.assertEqual("pending_update", segment["calendar_sync_status"])
        self.db.undo_schedule_operation(result.operation_id, "a")
        restored = self.db.get_task_segments(task, "a")[0]
        self.assertEqual("owned-event", restored["google_event_id"])
        self.assertEqual("09:00:00", restored["scheduled_start"])

    def test_defer_remains_a_plan_transfer_not_a_manual_move(self):
        task = self.add("Defer", 60)
        self.db.replace_plan_segments(
            self.plan, "a", {task: self.segment("09:00:00", "10:00:00")}, self.revision(),
        )
        self.db.execute(
            "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
            ("a", "2026-10-01", "deferred"),
        )
        self.db.connection.commit()
        tomorrow = self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id=? AND plan_date=?",
            ("a", "2026-10-01"),
        )["plan_id"]
        operation_count = self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"]
        self.db.move_task_unscheduled(task, "a", tomorrow, 0)
        moved = self.db.get_task(task, "a")
        self.assertEqual(tomorrow, moved["plan_id"])
        self.assertEqual([], self.db.get_task_segments(task, "a"))
        self.assertIsNone(moved["scheduled_start"])
        self.assertEqual(
            operation_count,
            self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"],
        )


if __name__ == "__main__":
    unittest.main()
