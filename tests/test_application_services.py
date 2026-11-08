from __future__ import annotations

from datetime import date, datetime, time, timedelta
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
        from timezone_utils import user_today
        self.plan1 = self.db.create_plan("u1", user_today(self.db, "u1"), "test")
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

    def test_today_plan_is_created_once_and_reused(self):
        # setUp creates today's plan for the other service tests; remove it so
        # this case can verify both the initial creation and later reuse.
        self.db.execute("DELETE FROM plans WHERE plan_id=?", (self.plan1,))
        first = self.app.tasks.ensure_today_plan(idempotency_key="today-1")
        second = self.app.tasks.ensure_today_plan(idempotency_key="today-2")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(
            1,
            int(
                self.db.fetch_one(
                    "SELECT COUNT(*) AS count FROM plans WHERE user_id=? AND plan_date=?",
                    ("u1", str(first["plan_date"])),
                )["count"]
            ),
        )

    def test_mobile_today_plan_schedule_move_and_undo_flow(self):
        target = self.app.tasks.ensure_today_plan(idempotency_key="flow-plan")
        created = self.app.tasks.create_task(
            plan_id=target["plan_id"],
            title="Mobile focus block",
            estimated_minutes=45,
            idempotency_key="flow-task",
        )
        revision = int(
            self.db.get_plan_by_id(target["plan_id"], "u1")["schedule_revision"]
        )
        scheduled = self.app.scheduling.schedule_plan(
            target["plan_id"],
            expected_revision=revision,
            work_day_start=time(8),
            idempotency_key="flow-schedule",
        )
        moved = self.app.scheduling.move_task(
            created["task_id"],
            expected_schedule_revision=scheduled["schedule_revision"],
            new_start=time(15),
            idempotency_key="flow-move",
        )
        undone = self.app.scheduling.undo(
            moved["operation_id"],
            idempotency_key="flow-undo",
        )

        self.assertTrue(moved["success"])
        self.assertTrue(undone["undone"])
        self.assertGreater(
            undone["schedule_revisions"][str(target["plan_id"])],
            moved["schedule_revision"],
        )

    def test_mobile_future_day_can_be_created_loaded_and_scheduled(self):
        from timezone_utils import user_today

        future_day = user_today(self.db, "u1") + timedelta(days=5)
        target = self.app.tasks.ensure_plan_date(
            future_day,
            idempotency_key="future-day-plan",
        )
        created = self.app.tasks.create_task(
            plan_id=target["plan_id"],
            title="Prepare next week",
            estimated_minutes=30,
            idempotency_key="future-day-task",
        )
        loaded = self.app.tasks.get_plan_day(future_day)

        self.assertEqual(target["plan_id"], loaded["plan"]["plan_id"])
        self.assertEqual(created["task_id"], loaded["plan"]["tasks"][0]["task_id"])
        self.assertEqual(future_day, loaded["date"])

    def test_past_day_is_read_only_at_every_planning_boundary(self):
        from timezone_utils import user_today

        past_day = user_today(self.db, "u1") - timedelta(days=1)
        past_plan = self.db.create_plan("u1", past_day, "history")

        with self.assertRaises(ValidationError):
            self.app.tasks.ensure_plan_date(past_day, idempotency_key="past-plan")
        with self.assertRaises(ValidationError):
            self.app.tasks.create_task(
                plan_id=past_plan,
                title="Must not be added",
                estimated_minutes=30,
                idempotency_key="past-task",
            )
        with self.assertRaises(ValidationError):
            self.app.scheduling.schedule_plan(
                past_plan,
                expected_revision=0,
                work_day_start=time(9),
                idempotency_key="past-schedule",
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

    def test_display_name_change_is_durable_and_idempotent(self):
        first = self.app.account.change_display_name(
            "  Ahmed H.  ", idempotency_key="display-name-change"
        )
        replay = self.app.account.change_display_name(
            "Ahmed H.", idempotency_key="display-name-change"
        )
        self.assertEqual("Ahmed H.", first["display_name"])
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual("Ahmed H.", self.app.account.get_profile()["display_name"])

    def test_notification_preferences_round_trip(self):
        defaults = self.app.notifications.get_preferences()
        self.assertEqual(10, defaults["default_reminder_lead_minutes"])
        self.assertEqual(1, defaults["sound_enabled"])
        changed = self.app.notifications.update_preferences(
            idempotency_key="notification-preferences",
            changes={
                "daily_summary_enabled": True,
                "coach_checkins_enabled": True,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "07:00",
                "sound_enabled": False,
            },
        )
        self.assertEqual(1, changed["daily_summary_enabled"])
        self.assertEqual(1, changed["coach_checkins_enabled"])
        self.assertEqual(0, changed["sound_enabled"])
        current = self.app.notifications.get_preferences()
        self.assertEqual("22:00", str(current["quiet_hours_start"])[:5])
        self.assertEqual("07:00", str(current["quiet_hours_end"])[:5])

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
