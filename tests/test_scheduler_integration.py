import tempfile
import unittest
from unittest.mock import patch
from datetime import time
from pathlib import Path

from database import Database
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService


class SchedulerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "test.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        self.db.execute(
            "INSERT INTO users (user_id,email,display_name) VALUES ('u','u@example.com','U')"
        )
        self.db.execute(
            "INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u','2026-09-29','test')"
        )
        self.db.connection.commit()
        self.plan_id = self.db.fetch_one("SELECT plan_id FROM plans")["plan_id"]

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_compute_validate_and_atomic_persist(self):
        primary = self.db.add_task(self.plan_id, "u", "Primary", estimated_minutes=150)
        service = SchedulerService(self.db)
        placed = service.schedule_plan(
            self.plan_id, "u", SchedulingPreferences(time(9)),
            blocked_slots=[(time(10), time(11)), (time(12), time(22, 30))],
        )
        self.assertEqual([primary], [item.task_id for item in placed])
        segments = self.db.get_task_segments(primary, "u")
        self.assertEqual(3, len(segments))
        self.assertIsNotNone(service.last_operation_id)
        plan = self.db.get_plan_by_id(self.plan_id, "u")
        self.assertEqual(2, plan["schedule_revision"])

    def test_unplaced_task_has_no_partial_persistence(self):
        task_id = self.db.add_task(self.plan_id, "u", "Too much", estimated_minutes=120)
        service = SchedulerService(self.db)
        placed = service.schedule_plan(
            self.plan_id, "u", SchedulingPreferences(time(9)),
            blocked_slots=[(time(10), time(23, 59, 59))],
        )
        self.assertEqual([], placed)
        self.assertEqual("capacity_shortfall", service.last_unplaced[0].reason)
        self.assertEqual([], self.db.get_task_segments(task_id, "u"))
        task = self.db.get_tasks_by_plan(self.plan_id, "u")[0]
        self.assertIsNone(task["scheduled_start"])

    def test_failed_run_clears_previous_operation_identity(self):
        self.db.add_task(self.plan_id, "u", "Task", estimated_minutes=60)
        service = SchedulerService(self.db)
        service.schedule_plan(self.plan_id, "u", SchedulingPreferences(time(9)))
        self.assertIsNotNone(service.last_operation_id)

        class BrokenScheduler:
            def allocate(self, tasks, plan_date):
                raise RuntimeError("compute failed")

        with patch.object(service, "_resolve_scheduler", return_value=BrokenScheduler()):
            with self.assertRaises(RuntimeError):
                service.schedule_plan(self.plan_id, "u", SchedulingPreferences(time(9)))
        self.assertIsNone(service.last_operation_id)
        self.assertEqual([], service.last_unplaced)


if __name__ == "__main__":
    unittest.main()
