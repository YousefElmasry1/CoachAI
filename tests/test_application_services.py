from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
import tempfile
import unittest

from application_services import CoachAIApplication, NotificationApplicationService
from backend_errors import ResourceNotFound, VersionConflict
from database import Database
from identity import AuthenticatedPrincipal
from notifications import NotificationRepository
from backend_errors import ValidationError


class PrincipalBoundApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "application.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user_id in ("u1", "u2"):
            self.db.create_user(user_id, f"{user_id}@example.com", user_id, "UTC")
        self.plan1 = self.db.create_plan("u1", date(2026, 10, 9), "test")
        self.principal = AuthenticatedPrincipal(
            "u1", "u1@example.com", "u1", {"sub": "u1"}
        )
        self.app = CoachAIApplication(self.db, self.principal)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_scheduling_uses_expected_revision_and_durable_idempotency(self):
        self.app.tasks.create_task(
            plan_id=self.plan1, title="Focus", estimated_minutes=60,
            idempotency_key="task-create",
        )
        expected = int(self.db.get_plan_by_id(self.plan1, "u1")["schedule_revision"])
        first = self.app.scheduling.schedule_plan(
            self.plan1, expected_revision=expected, work_day_start=time(8),
            idempotency_key="schedule-1",
        )
        replay = self.app.scheduling.schedule_plan(
            self.plan1, expected_revision=expected, work_day_start=time(8),
            idempotency_key="schedule-1",
        )
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["operation_id"], replay["operation_id"])
        with self.assertRaises(VersionConflict):
            self.app.scheduling.schedule_plan(
                self.plan1, expected_revision=expected, work_day_start=time(8),
                idempotency_key="schedule-stale",
            )

    def test_device_tenant_is_principal_bound_and_tokens_are_never_returned(self):
        registered = self.app.notifications.register_device(
            device_id="phone-u1", platform="android", provider="fcm",
            push_token="super-secret-token", idempotency_key="device-u1",
        )
        self.assertNotIn("push_token", registered)
        self.assertNotIn("super-secret-token", str(registered))

        NotificationRepository(self.db).register_device(
            "u2", device_id="phone-u2", platform="android", provider="fcm",
            push_token="u2-token",
        )
        with self.assertRaises(ResourceNotFound):
            self.app.notifications.register_device(
                device_id="phone-u2", platform="android", provider="fcm",
                push_token="spoof", idempotency_key="device-spoof",
            )

    def test_timezone_change_is_durable_and_queues_reconciliation(self):
        self.app.notifications.register_device(
            device_id="phone", platform="android", provider="none",
            idempotency_key="device",
        )
        result = self.app.account.change_timezone(
            "Africa/Cairo", idempotency_key="timezone-change"
        )
        self.assertEqual("Africa/Cairo", result["timezone"])
        device = NotificationRepository(self.db).get_device("u1", "phone")
        self.assertEqual("Africa/Cairo", device["timezone"])
        queued = self.db.fetch_one(
            "SELECT status FROM calendar_operations WHERE user_id=? AND operation_type=?",
            ("u1", "timezone_reconcile"),
        )
        self.assertEqual("queued", queued["status"])

    def test_notification_mobile_contract_ack_type_preferences_and_limits(self):
        registered = self.app.notifications.register_device(
            device_id="phone-contract", platform="android", provider="none",
            idempotency_key="device-contract",
        )
        self.assertEqual("phone-contract", registered["device_id"])
        preference = self.app.notifications.set_type_preference(
            "task_reminder",
            enabled=True,
            preferred_channel="local",
            lead_minutes=15,
            cooldown_seconds=60,
            interruption_level="active",
            idempotency_key="type-preference",
        )
        replay = self.app.notifications.set_type_preference(
            "task_reminder",
            enabled=True,
            preferred_channel="local",
            lead_minutes=15,
            cooldown_seconds=60,
            interruption_level="active",
            idempotency_key="type-preference",
        )
        self.assertEqual("local", preference["preferred_channel"])
        self.assertTrue(replay["idempotent_replay"])
        current = NotificationRepository(self.db).get_sync_revision("u1")
        acknowledged = self.app.notifications.acknowledge_schedule_revision(
            "phone-contract", current, idempotency_key="ack-contract"
        )
        self.assertEqual(current, acknowledged["acknowledged_schedule_revision"])
        for invalid in (0, 501, 1000000):
            if invalid == 0:
                continue
            with self.assertRaises(ValidationError):
                self.app.notifications.sync_since(0, limit=invalid)

    def test_timezone_change_reissues_pending_reminders_for_new_zone(self):
        future_plan = self.db.create_plan("u1", date(2099, 1, 2), "future")
        task_id = self.db.add_task(
            future_plan, "u1", "Timezone reminder", estimated_minutes=60,
            scheduled_start="09:00", scheduled_end="10:00",
        )
        self.app.notifications.service.reconcile_task_notifications(
            "u1", task_id, now_utc=datetime(2099, 1, 1, 0, 0)
        )
        before = self.db.fetch_all(
            """SELECT status,event_payload FROM notification_outbox
               WHERE user_id=? AND task_id=? AND event_type='TASK_REMINDER_DUE'""",
            ("u1", task_id),
        )
        self.assertEqual(1, len(before))
        self.assertEqual("UTC", json.loads(before[0]["event_payload"])["timezone"])

        result = self.app.account.change_timezone(
            "Africa/Cairo", idempotency_key="timezone-reminder-regeneration"
        )
        rows = self.db.fetch_all(
            """SELECT status,event_payload FROM notification_outbox
               WHERE user_id=? AND task_id=? AND event_type='TASK_REMINDER_DUE'
               ORDER BY created_at""",
            ("u1", task_id),
        )
        self.assertEqual(2, len(rows))
        self.assertEqual({"cancelled", "queued"}, {row["status"] for row in rows})
        queued_payload = json.loads(
            next(row for row in rows if row["status"] == "queued")["event_payload"]
        )
        self.assertEqual("Africa/Cairo", queued_payload["timezone"])
        self.assertEqual(1, result["notification_reminders_queued"])

    def test_task_reminder_preference_change_replaces_local_schedule(self):
        future_plan = self.db.create_plan("u1", date(2099, 1, 2), "future")
        task_id = self.db.add_task(
            future_plan, "u1", "Preference reminder", estimated_minutes=60,
            scheduled_start="09:00", scheduled_end="10:00",
        )
        self.app.notifications.service.reconcile_task_notifications(
            "u1", task_id, now_utc=datetime(2099, 1, 1, 0, 0)
        )
        self.app.notifications.service.process_outbox(
            now_utc=datetime(2099, 1, 1, 0, 0)
        )
        active_before = NotificationRepository(self.db).list_local_schedules(
            "u1", status="scheduled"
        )
        self.assertEqual(1, len(active_before))

        result = self.app.notifications.set_type_preference(
            "task_reminder",
            enabled=True,
            preferred_channel="local",
            lead_minutes=30,
            cooldown_seconds=0,
            interruption_level="active",
            idempotency_key="replace-reminder-preference",
        )
        self.assertEqual(1, result["local_schedules_cancelled"])
        self.assertEqual(1, result["notification_reminders_queued"])
        self.assertEqual(
            "cancelled",
            NotificationRepository(self.db).list_local_schedules("u1")[0]["status"],
        )
        queued = self.db.fetch_all(
            """SELECT idempotency_key,event_payload FROM notification_outbox
               WHERE user_id=? AND task_id=? AND status='queued'""",
            ("u1", task_id),
        )
        self.assertEqual(1, len(queued))
        self.assertIn(":cfg", queued[0]["idempotency_key"])
        self.assertEqual(
            "2099-01-02T08:30:00",
            json.loads(queued[0]["event_payload"])["scheduled_for"],
        )


if __name__ == "__main__":
    unittest.main()
