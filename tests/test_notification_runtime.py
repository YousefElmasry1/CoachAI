from datetime import date, datetime, timedelta
from unittest.mock import patch

from database import Database, OwnershipError
from notifications import (
    DeliveryChannel, DomainEvent, NotificationEventType, NotificationType,
    NotificationWorker,
)

from tests.notification_test_support import NotificationTestCase


class NotificationRuntimeTests(NotificationTestCase):
    NOW = datetime(2026, 10, 8, 5, 0)

    def reminder_event(self, key="runtime"):
        return DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Runtime"}, idempotency_key=key,
            occurred_at=self.NOW,
        )

    def create_scheduled_task(self, start="09:00:00", end="10:00:00"):
        task_id = self.direct_task(title="Scheduled")
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment(start, end)}, self.revision(),
        )
        return task_id

    def test_default_reminder_creates_local_schedule(self):
        task_id = self.create_scheduled_task()
        result = self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.assertEqual(1, result["scheduled"])
        self.assertEqual({"processed": 1, "failed": 0}, self.service.process_outbox(now_utc=self.NOW))
        schedule = self.repo.list_local_schedules("u1", status="scheduled")[0]
        self.assertEqual(task_id, schedule["task_id"])
        self.assertEqual("2026-10-08 05:50:00.000000", str(schedule["scheduled_fire_at"]))
        self.assertEqual(self.revision(), schedule["schedule_revision"])

    def test_configured_leads_and_off(self):
        task_id = self.direct_task(title="Lead configuration")
        for lead in (5, 10, 15, 30):
            self.repo.update_preferences("u1", default_reminder_lead_minutes=lead)
            self.db.replace_plan_segments(
                self.plan1, "u1",
                {task_id: self.segment(f"{10 + lead // 10:02d}:00:00", f"{11 + lead // 10:02d}:00:00")},
                self.revision(),
            )
            self.assertEqual(1, self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)["scheduled"])
        self.repo.update_preferences("u1", default_reminder_lead_minutes=0)
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment("15:00:00", "16:00:00")}, self.revision(),
        )
        self.assertEqual(0, self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)["scheduled"])

    def test_move_cancels_old_and_creates_revision_aware_schedule(self):
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        old = self.repo.list_local_schedules("u1")[0]
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment("11:00:00", "12:00:00")}, self.revision(),
        )
        result = self.service.reconcile_task_notifications("u1", task_id, now_utc=self.NOW)
        self.assertEqual(1, result["cancelled"])
        self.service.process_outbox(now_utc=self.NOW)
        rows = self.repo.list_local_schedules("u1")
        self.assertEqual("cancelled", next(row for row in rows if row["local_schedule_id"] == old["local_schedule_id"])["status"])
        active = [row for row in rows if row["status"] == "scheduled"]
        self.assertEqual(1, len(active))
        self.assertEqual(self.revision(), active[0]["schedule_revision"])

    def test_undo_reconciles_back_to_new_revision(self):
        task_id = self.create_scheduled_task()
        first_revision = self.revision()
        operation = self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment("11:00:00", "12:00:00")}, first_revision,
        )
        self.service.reconcile_task_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        self.db.undo_schedule_operation(operation, "u1")
        result = self.service.reconcile_task_notifications("u1", task_id, now_utc=self.NOW)
        self.assertEqual(1, result["cancelled"])
        self.service.process_outbox(now_utc=self.NOW)
        active = self.repo.list_local_schedules("u1", status="scheduled")
        self.assertEqual(1, len(active))
        self.assertEqual(self.revision(), active[0]["schedule_revision"])

    def test_task_lifecycle_cancels_future_reminder_and_preserves_history(self):
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        notification_id = self.repo.list_notifications("u1")[0]["notification_id"]
        self.service.cancel_task_notifications("u1", task_id, reason="task_completed")
        self.assertEqual("cancelled", self.repo.list_local_schedules("u1")[0]["status"])
        self.assertEqual("cancelled", self.repo.get_notification("u1", notification_id)["status"])

    def test_stale_reminder_event_is_expired_without_notification(self):
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment("11:00:00", "12:00:00")}, self.revision(),
        )
        result = self.service.process_outbox(now_utc=self.NOW)
        self.assertEqual(1, result["expired"])
        self.assertEqual([], self.repo.list_local_schedules("u1"))

    def test_expired_outbox_event_is_never_claimed(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="already-expired", occurred_at=self.NOW,
            payload={"expires_at": (self.NOW - timedelta(seconds=1)).isoformat()},
        )
        self.repo.enqueue_event(event)
        self.db.commit()
        self.assertEqual([], self.repo.claim_outbox(now=self.NOW))
        self.assertEqual("expired", self.repo.list_outbox("u1")[0]["status"])

    def test_claim_order_is_sequence_deterministic(self):
        for key in ("first", "second", "third"):
            self.repo.enqueue_event(self.reminder_event(key))
        self.db.commit()
        claimed = self.repo.claim_outbox(now=self.NOW, worker_id="ordered")
        self.assertEqual(["first", "second", "third"], [row["idempotency_key"] for row in claimed])

    def test_retry_backoff_is_deterministic_and_bounded(self):
        event = self.reminder_event("retry")
        self.repo.enqueue_event(event)
        self.db.commit()
        with patch.object(self.service, "process_event", side_effect=RuntimeError("temporary")):
            result = self.service.process_outbox(now_utc=self.NOW, worker_id="a")
        self.assertEqual(1, result["failed"])
        row = self.repo.list_outbox("u1")[0]
        self.assertEqual("failed", row["status"])
        self.assertEqual("2026-10-08 05:00:30.000000", str(row["available_at"]))
        self.assertEqual([], self.repo.claim_outbox(now=self.NOW + timedelta(seconds=29), worker_id="b"))
        self.assertEqual(1, len(self.repo.claim_outbox(now=self.NOW + timedelta(seconds=30), worker_id="b")))

    def test_permanent_failure_exhausts_retries(self):
        event = self.reminder_event("permanent")
        self.repo.enqueue_event(event)
        self.db.commit()
        with patch.object(self.service, "process_event", side_effect=ValueError("bad payload")):
            self.service.process_outbox(now_utc=self.NOW, max_attempts=5)
        row = self.repo.list_outbox("u1")[0]
        self.assertEqual(5, row["attempts"])
        self.assertEqual([], self.repo.claim_outbox(now=self.NOW + timedelta(days=1)))

    def test_lease_prevents_double_claim_and_recovers_after_timeout(self):
        event = self.reminder_event("lease")
        self.repo.enqueue_event(event)
        self.db.commit()
        first = self.repo.claim_outbox(now=self.NOW, worker_id="worker-a", stale_after_seconds=60)
        self.assertEqual(1, len(first))
        self.assertEqual([], self.repo.claim_outbox(now=self.NOW + timedelta(seconds=59), worker_id="worker-b", stale_after_seconds=60))
        recovered = self.repo.claim_outbox(now=self.NOW + timedelta(seconds=61), worker_id="worker-b", stale_after_seconds=60)
        self.assertEqual(1, len(recovered))
        with self.assertRaises(OwnershipError):
            self.repo.mark_outbox_processed(event.event_id, "u1", worker_id="worker-a")

    def test_second_database_connection_cannot_claim_same_event(self):
        event = self.reminder_event("two-connections")
        self.repo.enqueue_event(event)
        self.db.commit()
        other = Database(f"sqlite:///{self.db_path.as_posix()}")
        try:
            self.assertEqual(1, len(self.repo.claim_outbox(now=self.NOW, worker_id="a")))
            self.assertEqual([], NotificationWorker(other, worker_id="b").service.repository.claim_outbox(now=self.NOW, worker_id="b"))
        finally:
            other.close()

    def test_sync_revision_and_acknowledgements_are_idempotent(self):
        notification = self.service.process_event(self.reminder_event("sync"), now_utc=self.NOW)
        self.db.commit()
        created_revision = self.repo.get_sync_revision("u1")
        self.service.mark_notification_read("u1", notification["notification_id"])
        read_revision = self.repo.get_sync_revision("u1")
        self.service.mark_notification_read("u1", notification["notification_id"])
        self.assertEqual(read_revision, self.repo.get_sync_revision("u1"))
        self.assertGreater(read_revision, created_revision)
        changes = self.service.sync_notifications_since_revision("u1", created_revision)
        self.assertEqual(1, len(changes["notifications"]))
        self.assertIsNotNone(changes["notifications"][0]["read_at"])

    def test_device_ack_never_regresses(self):
        self.repo.register_device("u1", device_id="phone", platform="android", provider="none")
        self.service.process_event(self.reminder_event("ack"), now_utc=self.NOW)
        self.db.commit()
        revision = self.repo.get_sync_revision("u1")
        self.repo.acknowledge_schedule_revision("u1", "phone", revision)
        self.repo.acknowledge_schedule_revision("u1", "phone", 0)
        self.assertEqual(revision, self.repo.get_device("u1", "phone")["last_acknowledged_schedule_revision"])

    def test_sync_pagination_returns_safe_checkpoint(self):
        for index in range(3):
            self.service.process_event(self.reminder_event(f"page:{index}"), now_utc=self.NOW)
        self.db.commit()
        first = self.service.sync_notifications_since_revision("u1", 0, limit=2)
        self.assertTrue(first["has_more"])
        self.assertLess(first["revision"], first["current_revision"])
        second = self.service.sync_notifications_since_revision("u1", first["revision"], limit=2)
        self.assertFalse(second["has_more"])
        self.assertEqual(1, len(second["notifications"]))

    def test_primary_device_policy_avoids_duplicate_local_presentation(self):
        self.repo.register_device("u1", device_id="primary", platform="android", provider="none", is_primary=True)
        self.repo.register_device("u1", device_id="secondary", platform="ios", provider="none")
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        rows = self.repo.list_local_schedules("u1", status="scheduled")
        self.assertEqual(["primary"], [row["device_id"] for row in rows])

    def test_all_device_policy_and_invalid_device_filtering(self):
        self.repo.update_preferences("u1", multi_device_policy="all")
        self.repo.register_device("u1", device_id="one", platform="android", provider="none")
        self.repo.register_device("u1", device_id="two", platform="ios", provider="none")
        self.repo.invalidate_device_token("u1", "two")
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        self.assertEqual(["one"], [row["device_id"] for row in self.repo.list_local_schedules("u1")])

    def test_push_channel_creates_intent_without_provider_delivery(self):
        self.repo.set_type_preference("u1", NotificationType.TASK_UNPLACED, preferred_channel=DeliveryChannel.PUSH)
        self.repo.register_device("u1", device_id="push", platform="android", provider="fcm", push_token="opaque")
        notification = self.service.process_event(self.reminder_event("push-intent"), now_utc=self.NOW)
        self.db.commit()
        delivery = self.db.fetch_one("SELECT * FROM notification_deliveries WHERE notification_id=?", (notification["notification_id"],))
        self.assertEqual("push", delivery["channel"])
        self.assertEqual("queued", delivery["status"])
        self.assertIsNone(delivery["provider_message_id"])

    def test_foreground_suppression_routes_external_channel_in_app(self):
        self.repo.set_type_preference("u1", NotificationType.TASK_UNPLACED, preferred_channel=DeliveryChannel.PUSH)
        event = self.reminder_event("foreground")
        self.repo.enqueue_event(event)
        self.db.commit()
        self.service.process_outbox(now_utc=self.NOW, foreground_user_ids={"u1"})
        delivery = self.db.fetch_one("SELECT * FROM notification_deliveries")
        self.assertEqual("in_app", delivery["channel"])
        self.assertEqual("delivered", delivery["status"])

    def test_quiet_hours_do_not_schedule_after_reminder_expiry(self):
        self.repo.update_preferences(
            "u1", quiet_hours_start="22:00", quiet_hours_end="07:00",
        )
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_REMINDER_DUE,
            idempotency_key="quiet-expiry", occurred_at=datetime(2026, 10, 8, 23, 0),
            payload={
                "scheduled_for": "2026-10-08T23:10:00",
                "expires_at": "2026-10-09T00:00:00",
            },
        )
        notification = self.service.process_event(event, now_utc=datetime(2026, 10, 8, 23, 0))
        self.db.commit()
        self.assertEqual("expired_before_delivery", notification["suppression_reason"])
        self.assertEqual([], self.repo.list_local_schedules("u1"))

    def test_reminder_calculation_uses_later_dst_fold(self):
        self.db.execute("UPDATE users SET timezone='America/New_York' WHERE user_id='u1'")
        self.db.execute(
            "INSERT INTO plans(user_id,plan_date,raw_input) VALUES ('u1','2026-11-01','dst')"
        )
        self.db.commit()
        plan = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='u1' AND plan_date='2026-11-01'"
        )["plan_id"])
        task = int(self.db._insert_and_get_id(
            "INSERT INTO tasks(plan_id,title,estimated_minutes,priority) VALUES (?,?,?,3)",
            (plan, "DST", 30), pk_column="task_id",
        ))
        self.db.commit()
        revision = int(self.db.get_plan_by_id(plan, "u1")["schedule_revision"])
        self.db.replace_plan_segments(
            plan, "u1", {task: [{
                "segment_index": 0, "scheduled_start": "01:30:00",
                "scheduled_end": "02:00:00", "scheduled_date": "2026-11-01",
            }]}, revision,
        )
        now = datetime(2026, 11, 1, 4, 0)
        self.service.schedule_local_notifications("u1", task, now_utc=now)
        self.service.process_outbox(now_utc=now)
        schedule = self.repo.list_local_schedules("u1", status="scheduled")[0]
        self.assertEqual("2026-11-01 06:20:00.000000", str(schedule["scheduled_fire_at"]))

    def test_daily_summary_is_opt_in_nonempty_and_deduplicated(self):
        self.assertFalse(self.service.generate_daily_summary("u1", date(2026, 10, 8), now_utc=self.NOW))
        self.repo.update_preferences("u1", daily_summary_enabled=True)
        self.assertFalse(self.service.generate_daily_summary("u1", date(2026, 10, 9), now_utc=self.NOW))
        self.direct_task(title="Real activity")
        self.assertTrue(self.service.generate_daily_summary("u1", date(2026, 10, 8), now_utc=self.NOW))
        self.assertFalse(self.service.generate_daily_summary("u1", date(2026, 10, 8), now_utc=self.NOW))

    def test_local_schedule_is_tenant_scoped(self):
        task_id = self.create_scheduled_task()
        self.service.schedule_local_notifications("u1", task_id, now_utc=self.NOW)
        self.service.process_outbox(now_utc=self.NOW)
        self.assertEqual([], self.repo.list_local_schedules("u2"))
        with self.assertRaises(OwnershipError):
            self.repo.list_local_schedules("u2", task_id=task_id)


if __name__ == "__main__":
    import unittest
    unittest.main()
