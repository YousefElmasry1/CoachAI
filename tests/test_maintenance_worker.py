from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from maintenance_worker import run_daily_stale_task_cleanup
from timezone_utils import user_today
from unit_of_work import DatabaseFactory, UnitOfWork


class MaintenanceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "maintenance.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        with UnitOfWork(self.factory) as uow:
            uow.db.create_user("u1", "u1@example.com", "u1", "Africa/Cairo")
            yesterday = user_today(uow.db, "u1") - timedelta(days=1)
            self.plan = uow.db.create_plan("u1", yesterday, "stale")
            self.task = uow.db.add_task(
                self.plan, "u1", "unfinished", estimated_minutes=30
            )

    def tearDown(self):
        self.factory.close()
        self.tempdir.cleanup()

    def test_poll_is_idempotent_and_publishes_mobile_change(self):
        first = run_daily_stale_task_cleanup(self.factory)
        second = run_daily_stale_task_cleanup(self.factory)
        self.assertEqual(1, first["closed_tasks"])
        self.assertEqual(0, second["closed_tasks"])
        with UnitOfWork(self.factory) as uow:
            self.assertEqual("failed", uow.db.get_task(self.task, "u1")["status"])
            changes = MobileRepository(uow.db).sync_since("u1", 0)["changes"]
            self.assertTrue(any(item["entity_type"] == "task" for item in changes))


from mobile_repository import MobileRepository


if __name__ == "__main__":
    unittest.main()
