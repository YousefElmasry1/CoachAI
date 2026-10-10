from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest

from backend_errors import (
    IdempotencyConflict,
    InvalidStateTransition,
    ResourceNotFound,
    VersionConflict,
)
from database import Database, OwnershipError
from identity import AuthenticatedPrincipal
from task_application import TaskApplicationService


class TaskApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "tasks.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user_id in ("u1", "u2"):
            self.db.create_user(user_id, f"{user_id}@example.com", user_id, "Africa/Cairo")
        self.plan1 = self.db.create_plan("u1", date(2026, 10, 8), "test")
        self.plan2 = self.db.create_plan("u2", date(2026, 10, 8), "test")
        self.category1 = self.db.create_category("u1", "Work")
        self.category2 = self.db.create_category("u2", "Private")
        self.now = datetime(2026, 10, 8, 8, 0, 0)
        principal = AuthenticatedPrincipal("u1", "u1@example.com", "u1", {"sub": "u1"})
        self.service = TaskApplicationService(self.db, principal, clock=lambda: self.now)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def create(self, key="create-1", **changes):
        values = {
            "plan_id": self.plan1,
            "title": "Focus task",
            "estimated_minutes": 30,
            "category_id": self.category1,
            "idempotency_key": key,
        }
        values.update(changes)
        return self.service.create_task(**values)

    def test_create_is_principal_scoped_and_idempotent(self):
        created = self.create()
        replay = self.create()
        self.assertFalse(created["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(created["task_id"], replay["task_id"])
        self.assertEqual(1, self.db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"])
        with self.assertRaises(IdempotencyConflict):
            self.create(title="Changed payload")

    def test_spoofed_plan_and_cross_tenant_category_are_hidden(self):
        with self.assertRaises(ResourceNotFound):
            self.create(key="foreign-plan", plan_id=self.plan2)
        with self.assertRaises(ResourceNotFound):
            self.create(key="foreign-category", category_id=self.category2)
        with self.assertRaises(OwnershipError):
            self.db.update_profile("u1", favorite_category_id=self.category2)

    def test_lifecycle_timer_is_atomic_versioned_and_retry_safe(self):
        task = self.create()
        started = self.service.start(
            task["task_id"], expected_version=0, idempotency_key="start-1"
        )
        self.assertEqual("in_progress", started["status"])
        self.assertEqual(1, started["version"])
        self.assertIsNotNone(started["timer_segment_started_at"])

        replay = self.service.start(
            task["task_id"], expected_version=0, idempotency_key="start-1"
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(started["version"], replay["version"])

        self.now = datetime(2026, 10, 8, 8, 5, 0)
        paused = self.service.pause(
            task["task_id"], expected_version=1, idempotency_key="pause-1"
        )
        self.assertEqual(300, paused["timer_accumulated_seconds"])
        self.assertEqual(2, paused["version"])

        self.now = datetime(2026, 10, 8, 8, 7, 0)
        resumed = self.service.resume(
            task["task_id"], expected_version=2, idempotency_key="resume-1"
        )
        self.assertEqual(120, resumed["timer_total_paused_seconds"])

        self.now = datetime(2026, 10, 8, 8, 12, 0)
        completed = self.service.complete(
            task["task_id"], expected_version=3, idempotency_key="complete-1"
        )
        self.assertEqual("completed", completed["status"])
        self.assertEqual(10, completed["actual_minutes"])
        self.assertEqual(4, completed["version"])

    def test_invalid_transitions_and_version_conflict_do_not_mutate(self):
        task = self.create()
        self.service.complete(
            task["task_id"], expected_version=0, idempotency_key="complete-direct"
        )
        for action in (
            lambda: self.service.start(
                task["task_id"], expected_version=1, idempotency_key="restart"
            ),
            lambda: self.service.defer_to_tomorrow(
                task["task_id"], expected_version=1, idempotency_key="defer-terminal"
            ),
            lambda: self.service.complete(
                task["task_id"], expected_version=1, idempotency_key="complete-twice"
            ),
        ):
            with self.assertRaises(InvalidStateTransition):
                action()
        with self.assertRaises(VersionConflict):
            self.service.update_task(
                task["task_id"], expected_version=0,
                idempotency_key="stale-update", changes={"title": "stale"},
            )
        self.assertEqual("Focus task", self.db.get_task(task["task_id"], "u1")["title"])

    def test_failure_after_mutation_rolls_back_task_and_idempotency_record(self):
        task = self.create()
        original_record_change = self.service.mobile.record_change

        def fail_after_sql(**_kwargs):
            raise RuntimeError("injected sync failure")

        self.service.mobile.record_change = fail_after_sql
        with self.assertRaises(RuntimeError):
            self.service.start(
                task["task_id"], expected_version=0, idempotency_key="start-rollback"
            )
        row = self.db.get_task(task["task_id"], "u1")
        self.assertEqual("pending", row["status"])
        self.assertEqual(0, row["version"])
        self.assertIsNone(self.db.fetch_one(
            "SELECT record_id FROM idempotency_records WHERE idempotency_key=?",
            ("start-rollback",),
        ))

        self.service.mobile.record_change = original_record_change
        succeeded = self.service.start(
            task["task_id"], expected_version=0, idempotency_key="start-rollback"
        )
        self.assertEqual("in_progress", succeeded["status"])


if __name__ == "__main__":
    unittest.main()
