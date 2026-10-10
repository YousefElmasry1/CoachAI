from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from calendar_security import CalendarOperationQueue
from calendar_worker import CalendarWorker, CalendarWorkerSettings
from identity import AuthenticatedPrincipal
from unit_of_work import DatabaseFactory, UnitOfWork


class FakeCalendarClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.created = []

    def create_event(self, calendar_id, title, start_time, end_time, event_date,
                     timezone="UTC", event_id=None):
        if self.fail:
            raise RuntimeError("Bearer should-never-be-stored")
        self.created.append((calendar_id, event_id))
        return event_id

    def update_event(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("Bearer should-never-be-stored")

    def delete_event(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("Bearer should-never-be-stored")


class CalendarWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "calendar-worker.db"
        self.url = f"sqlite:///{path.as_posix()}"
        self.factory = DatabaseFactory(self.url)
        self.principal = AuthenticatedPrincipal(
            "u1", "u1@example.com", "u1", {"sub": "u1"}
        )
        with UnitOfWork(self.factory) as uow:
            uow.db.create_user("u1", "u1@example.com", "u1", "Africa/Cairo")
            plan = uow.db.create_plan("u1", date(2026, 10, 9), "calendar")
            self.task_id = uow.db.add_task(
                plan, "u1", "Export me", estimated_minutes=60,
                scheduled_start="09:00", scheduled_end="10:00",
            )

    def tearDown(self):
        self.factory.close()
        self.tempdir.cleanup()

    def worker(self, client):
        settings = CalendarWorkerSettings(
            self.url, "worker-test", poll_seconds=1, batch_size=10,
            lease_seconds=60, max_attempts=3,
        )
        worker = CalendarWorker(self.factory, settings)
        worker._load_client = lambda _user_id: (client, self.principal)
        return worker

    def enqueue(self, key="export"):
        with UnitOfWork(self.factory) as uow:
            return CalendarOperationQueue(uow.db).enqueue(
                self.principal, operation_type="event.upsert",
                idempotency_key=key, payload={"task_id": self.task_id},
            )[0]

    def test_worker_exports_and_completes_durable_operation(self):
        operation = self.enqueue()
        fake = FakeCalendarClient()
        result = self.worker(fake).run_once()
        self.assertEqual({"claimed": 1, "completed": 1, "failed": 0}, result)
        with UnitOfWork(self.factory) as uow:
            stored = CalendarOperationQueue(uow.db).get(
                self.principal, operation.operation_id
            )
            segment = uow.db.get_task_segments(self.task_id, "u1")[0]
        self.assertEqual("completed", stored.status)
        self.assertEqual("synced", segment["calendar_sync_status"])
        self.assertTrue(str(segment["google_event_id"]).startswith("coachai"))

    def test_provider_failure_is_redacted_and_retryable(self):
        operation = self.enqueue("failure")
        result = self.worker(FakeCalendarClient(fail=True)).run_once()
        self.assertEqual(1, result["failed"])
        with UnitOfWork(self.factory) as uow:
            stored = CalendarOperationQueue(uow.db).get(
                self.principal, operation.operation_id
            )
        self.assertEqual("failed", stored.status)
        self.assertEqual("Calendar provider operation failed.", stored.last_error)
        self.assertNotIn("Bearer", stored.last_error)


if __name__ == "__main__":
    unittest.main()
