from datetime import datetime, timedelta
import json
from unittest.mock import patch

from notifications import DomainEvent, NotificationEventType, NotificationType

from tests.notification_test_support import NotificationTestCase


class NotificationOutboxTests(NotificationTestCase):
    def test_idempotency_key_creates_one_outbox_row_and_one_revision(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "A"}, idempotency_key="same-event",
        )
        first = self.repo.enqueue_event(event, bump_sync_revision=True)
        duplicate = self.repo.enqueue_event(event, bump_sync_revision=True)
        self.db.connection.commit()
        self.assertTrue(first[1])
        self.assertFalse(duplicate[1])
        self.assertEqual(1, self.repo.get_sync_revision("u1"))
        self.assertEqual(1, len(self.repo.list_outbox("u1")))

    def test_abandoned_worker_lock_is_recovered_for_retry(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="stale-lock",
        )
        self.repo.enqueue_event(event)
        self.db.execute(
            """UPDATE notification_outbox
               SET status='processing',attempts=1,locked_at='2026-10-08 08:00:00'
               WHERE outbox_id=?""",
            (event.event_id,),
        )
        self.db.connection.commit()
        claimed = self.repo.claim_outbox(now=datetime(2026, 10, 8, 9, 0))
        self.assertEqual(1, len(claimed))
        self.assertEqual(event.event_id, claimed[0]["outbox_id"])
        self.assertEqual(2, claimed[0]["attempts"])

    def test_transaction_rollback_removes_outbox_and_domain_write(self):
        before = self.db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"]
        with patch.object(
            self.db, "_enqueue_notification_event_no_commit", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.db.add_task(self.plan1, "u1", "Must roll back")
        self.assertEqual(before, self.db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"])
        self.assertEqual([], self.repo.list_outbox("u1"))

    def test_successful_task_write_preserves_outbox_and_sync_revision(self):
        task_id = self.db.add_task(self.plan1, "u1", "Atomic")
        row = self.repo.list_outbox("u1")[0]
        self.assertEqual("TASK_CREATED", row["event_type"])
        self.assertEqual(task_id, row["task_id"])
        self.assertEqual(1, self.repo.get_sync_revision("u1"))

    def test_processing_creates_deduplicated_notification_and_delivery(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Deep work", "reason": "fragmentation"},
            idempotency_key="unplaced:one",
        )
        self.repo.enqueue_event(event)
        self.db.connection.commit()
        result = self.service.process_outbox()
        self.assertEqual({"processed": 1, "failed": 0}, result)
        notification = self.repo.list_notifications("u1")[0]
        self.assertEqual("task_unplaced", notification["notification_type"])
        delivery = self.db.fetch_one(
            "SELECT * FROM notification_deliveries WHERE notification_id=?",
            (notification["notification_id"],),
        )
        self.assertEqual("in_app", delivery["channel"])
        self.assertEqual("delivered", delivery["status"])
        # Reprocessing the same durable identity cannot duplicate output.
        self.service.process_event(event, now_utc=datetime(2026, 10, 8, 10, 1))
        self.db.connection.commit()
        self.assertEqual(1, self.db.fetch_one("SELECT COUNT(*) AS n FROM notifications")["n"])

    def test_cooldown_and_expiration_are_persisted(self):
        self.repo.set_type_preference(
            "u1", NotificationType.TASK_UNPLACED, cooldown_seconds=3600,
        )
        first = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "A"}, idempotency_key="cooldown:first",
        )
        second = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "A"}, idempotency_key="cooldown:second",
        )
        self.service.process_event(first)
        self.db.connection.commit()
        suppressed = self.service.process_event(second)
        self.db.connection.commit()
        self.assertEqual("cooldown_active", suppressed["suppression_reason"])
        self.db.execute(
            "UPDATE notifications SET expires_at='2000-01-01 00:00:00',status='active' WHERE notification_id=?",
            (self.repo.list_notifications("u1")[0]["notification_id"],),
        )
        self.db.connection.commit()
        self.assertEqual(1, self.repo.expire_due(now=datetime(2026, 10, 8)))

    def test_schedule_operation_emits_one_batched_revision_aware_event(self):
        task1 = self.direct_task(title="A")
        task2 = self.direct_task(title="B")
        operation_id = self.db.replace_plan_segments(
            self.plan1, "u1",
            {task1: self.segment("09:00:00", "10:00:00"), task2: self.segment("10:00:00", "11:00:00")},
            self.revision(),
        )
        schedule_events = [
            row for row in self.repo.list_outbox("u1")
            if row["event_type"] == "SCHEDULE_OPERATION_APPLIED"
        ]
        self.assertEqual(1, len(schedule_events))
        row = schedule_events[0]
        payload = json.loads(row["event_payload"])
        self.assertTrue(payload["batched"])
        self.assertEqual(2, payload["adjusted_count"])
        self.assertEqual(operation_id, row["schedule_operation_id"])
        self.assertEqual(self.revision(), row["schedule_revision"])

    def test_undo_emits_one_event_and_increments_user_sync(self):
        task_id = self.direct_task()
        operation_id = self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment()}, self.revision(),
        )
        before = self.repo.get_sync_revision("u1")
        self.db.undo_schedule_operation(operation_id, "u1")
        rows = [r for r in self.repo.list_outbox("u1") if r["event_type"] == "SCHEDULE_OPERATION_UNDONE"]
        self.assertEqual(1, len(rows))
        self.assertEqual(operation_id, rows[0]["schedule_operation_id"])
        self.assertEqual(before + 1, self.repo.get_sync_revision("u1"))

    def test_segment_notification_identity_is_revision_aware(self):
        task_id = self.direct_task()
        first = self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment()}, self.revision(),
        )
        old_segment = self.db.get_task_segments(task_id, "u1")[0]
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment("11:00:00", "12:00:00")}, self.revision(),
        )
        new_segment = self.db.get_task_segments(task_id, "u1")[0]
        self.assertNotEqual(old_segment["segment_id"], new_segment["segment_id"])
        reminder = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_REMINDER_DUE,
            task_id=task_id, segment_id=new_segment["segment_id"],
            scheduled_date="2026-10-08", segment_index=0,
            schedule_revision=self.revision(), schedule_operation_id=first,
            idempotency_key=f"task:{task_id}:2026-10-08:0:reminder:rev{self.revision()}",
        )
        self.repo.enqueue_event(reminder)
        self.db.connection.commit()
        row = self.db.fetch_one(
            "SELECT * FROM notification_outbox WHERE user_id=? AND idempotency_key=?",
            ("u1", reminder.idempotency_key),
        )
        self.assertEqual(0, row["segment_index"])
        self.assertEqual(self.revision(), row["schedule_revision"])


if __name__ == "__main__":
    import unittest
    unittest.main()
