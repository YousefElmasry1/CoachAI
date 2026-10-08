import tempfile
import unittest
from pathlib import Path

from database import Database, OwnershipError, StaleScheduleError


class SegmentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "test.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for uid in ("u1", "u2"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                (uid, f"{uid}@example.com", uid),
            )
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (uid, f"2026-09-{29 if uid == 'u1' else 30}", "test"),
            )
        self.db.connection.commit()
        self.plan1 = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='u1'")["plan_id"]
        self.plan2 = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='u2'")["plan_id"]
        self.task1 = self.db.add_task(self.plan1, "u1", "Task", estimated_minutes=60)
        self.task2 = self.db.add_task(self.plan2, "u2", "Other", estimated_minutes=60)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    @staticmethod
    def segment(start="09:00:00", end="10:00:00"):
        return [{"segment_index": 0, "scheduled_start": start, "scheduled_end": end}]

    def revision(self, plan_id=None, user_id="u1"):
        return int(self.db.get_plan_by_id(plan_id or self.plan1, user_id)["schedule_revision"])

    def test_tenant_scope_replacement_cache_and_stale_rejection(self):
        operation = self.db.replace_plan_segments(
            self.plan1, "u1", {self.task1: self.segment()}, expected_revision=self.revision(),
        )
        self.assertTrue(operation)
        self.assertEqual([], self.db.get_task_segments(self.task1, "u2"))
        task = self.db.get_tasks_by_plan(self.plan1, "u1")[0]
        self.assertEqual("09:00:00", str(task["scheduled_start"]))
        with self.assertRaises(StaleScheduleError):
            self.db.replace_plan_segments(
                self.plan1, "u1", {self.task1: self.segment("10:00:00", "11:00:00")}, 0,
            )
        with self.assertRaises(OwnershipError):
            self.db.replace_plan_segments(self.plan1, "u2", {self.task1: []}, 1)

    def test_replacement_removes_stale_segments_and_undo_restores_exact_windows(self):
        first = self.db.replace_plan_segments(
            self.plan1, "u1", {self.task1: self.segment()}, self.revision(),
        )
        second = self.db.replace_plan_segments(
            self.plan1, "u1", {self.task1: [
                {"segment_index": 0, "scheduled_start": "11:00:00", "scheduled_end": "11:30:00"},
                {"segment_index": 1, "scheduled_start": "12:00:00", "scheduled_end": "12:30:00"},
            ]}, self.revision(),
        )
        self.assertEqual(2, len(self.db.get_task_segments(self.task1, "u1")))
        self.db.undo_schedule_operation(second, "u1")
        restored = self.db.get_task_segments(self.task1, "u1")
        self.assertEqual(1, len(restored))
        self.assertEqual("09:00:00", str(restored[0]["scheduled_start"]))
        with self.assertRaises(StaleScheduleError):
            self.db.undo_schedule_operation(first, "u1")

    def test_delete_task_cascades_segments(self):
        self.db.replace_plan_segments(self.plan1, "u1", {self.task1: self.segment()}, self.revision())
        self.db.delete_task(self.task1, "u1")
        count = self.db.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"]
        self.assertEqual(0, count)

    def test_manual_reschedule_updates_source_and_cache_together(self):
        self.db.replace_plan_segments(self.plan1, "u1", {self.task1: [{
            "segment_index": 0, "scheduled_start": "09:00:00", "scheduled_end": "10:00:00",
            "google_event_id": "owned", "calendar_provenance": "coachai_created",
            "calendar_sync_status": "synced",
        }]}, self.revision())
        self.db.set_single_task_segment(self.task1, "u1", "13:00", "14:00")
        segment = self.db.get_task_segments(self.task1, "u1")[0]
        task = self.db.get_task(self.task1, "u1")
        self.assertEqual("13:00:00", str(segment["scheduled_start"]))
        self.assertEqual("13:00:00", str(task["scheduled_start"]))
        self.assertEqual("owned", segment["google_event_id"])
        self.assertEqual("pending_update", segment["calendar_sync_status"])
        self.assertGreaterEqual(self.db.get_plan_by_id(self.plan1, "u1")["schedule_revision"], 3)

    def test_deferring_removes_date_bound_segments(self):
        self.db.replace_plan_segments(self.plan1, "u1", {self.task1: self.segment()}, self.revision())
        self.db.execute(
            "INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u1','2026-10-01','next')"
        )
        self.db.connection.commit()
        destination = self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='u1' AND plan_date='2026-10-01'"
        )["plan_id"]
        self.db.move_task_unscheduled(self.task1, "u1", destination, 0)
        self.assertEqual([], self.db.get_task_segments(self.task1, "u1"))
        task = self.db.get_task(self.task1, "u1")
        self.assertEqual(destination, task["plan_id"])
        self.assertIsNone(task["scheduled_start"])

    def test_cross_tenant_defer_is_rejected_without_losing_segments(self):
        self.db.replace_plan_segments(self.plan1, "u1", {self.task1: self.segment()}, self.revision())
        with self.assertRaises(OwnershipError):
            self.db.move_task_unscheduled(self.task1, "u1", self.plan2, 0)
        self.assertEqual(1, len(self.db.get_task_segments(self.task1, "u1")))

    def test_segment_calendar_state_is_tenant_scoped(self):
        self.db.replace_plan_segments(self.plan1, "u1", {self.task1: self.segment()}, self.revision())
        segment_id = self.db.get_task_segments(self.task1, "u1")[0]["segment_id"]
        with self.assertRaises(OwnershipError):
            self.db.update_segment_calendar_state(
                segment_id, "u2", google_event_id="stolen",
                calendar_provenance="coachai_created",
            )
        segment = self.db.get_task_segments(self.task1, "u1")[0]
        self.assertIsNone(segment["google_event_id"])

    def test_undo_and_timer_are_tenant_scoped(self):
        operation = self.db.replace_plan_segments(
            self.plan1, "u1", {self.task1: self.segment()}, self.revision(),
        )
        with self.assertRaises(OwnershipError):
            self.db.undo_schedule_operation(operation, "u2")
        with self.assertRaises(OwnershipError):
            self.db.set_task_timer_state(
                self.task1, "u2", 10, None, None, 0, expected_version=0,
            )
        self.assertEqual(1, len(self.db.get_task_segments(self.task1, "u1")))
        self.assertEqual("applied", self.db.get_schedule_operation(operation, "u1")["status"])


if __name__ == "__main__":
    unittest.main()
