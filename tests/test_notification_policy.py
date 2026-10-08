from datetime import datetime, timedelta
import unittest

from notifications import (
    DeliveryChannel,
    DomainEvent,
    NotificationEventType,
    NotificationPolicy,
    NotificationType,
)
from notifications.policy import quiet_hours_end_utc


class NotificationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = NotificationPolicy()
        self.preferences = {
            "timezone": "Africa/Cairo",
            "quiet_hours_start": None,
            "quiet_hours_end": None,
            "coach_checkins_enabled": 0,
            "daily_summary_enabled": 0,
        }
        self.now = datetime(2026, 10, 8, 10, 0)

    def event(self, event_type, **payload):
        return DomainEvent.create(
            user_id="u1", event_type=event_type, payload=payload,
            idempotency_key=f"key:{event_type}:{payload}",
        )

    def test_schedule_operation_is_one_normal_in_app_intent(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.SCHEDULE_OPERATION_APPLIED, adjusted_count=3),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual(NotificationType.SCHEDULE_CHANGE, decision.notification_type)
        self.assertEqual(DeliveryChannel.IN_APP, decision.channel)
        self.assertTrue(decision.should_deliver)

    def test_task_reminder_uses_deferred_local_channel(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.TASK_REMINDER_DUE, task_title="Study"),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual(NotificationType.TASK_REMINDER, decision.notification_type)
        self.assertEqual(DeliveryChannel.LOCAL, decision.channel)

    def test_lifecycle_is_persisted_but_suppressed_by_default(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.TASK_COMPLETED, task_title="Study"),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual("lifecycle_state_only", decision.suppression_reason)

    def test_checkins_and_daily_summary_are_opt_in(self):
        checkin = self.policy.decide(
            self.event(NotificationEventType.FOCUS_SESSION_STARTED),
            self.preferences, now_utc=self.now,
        )
        summary = self.policy.decide(
            self.event(NotificationEventType.DAILY_SUMMARY_READY),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual("coach_checkins_disabled", checkin.suppression_reason)
        self.assertEqual("daily_summary_disabled", summary.suppression_reason)

    def test_safe_deadline_transition_is_state_only(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.DEADLINE_RISK_CHANGED, state="SAFE"),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual("risk_resolved_in_app_state_only", decision.suppression_reason)

    def test_type_override_can_disable_or_route(self):
        event = self.event(NotificationEventType.TASK_UNPLACED)
        disabled = self.policy.decide(
            event, self.preferences, {"enabled": 0}, now_utc=self.now,
        )
        routed = self.policy.decide(
            event, self.preferences,
            {"enabled": 1, "preferred_channel": "push", "interruption_level": "time_sensitive"},
            now_utc=self.now,
        )
        self.assertEqual("disabled_by_user", disabled.suppression_reason)
        self.assertEqual(DeliveryChannel.PUSH, routed.channel)
        self.assertEqual("time_sensitive", routed.interruption_level.value)

    def test_foreground_downgrades_external_delivery_to_in_app(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.TASK_REMINDER_DUE),
            self.preferences, now_utc=self.now, app_is_open=True,
        )
        self.assertEqual(DeliveryChannel.IN_APP, decision.channel)

    def test_cooldown_is_auditable_suppression(self):
        decision = self.policy.decide(
            self.event(NotificationEventType.TASK_UNPLACED),
            self.preferences, now_utc=self.now, cooldown_active=True,
        )
        self.assertEqual("cooldown_active", decision.suppression_reason)

    def test_quiet_hours_delay_normal_but_not_high(self):
        preferences = dict(
            self.preferences, timezone="UTC",
            quiet_hours_start="22:00", quiet_hours_end="07:00",
        )
        at_night = datetime(2026, 10, 8, 23, 0)
        normal = self.policy.decide(
            self.event(NotificationEventType.SCHEDULE_OPERATION_APPLIED),
            preferences, now_utc=at_night,
        )
        high = self.policy.decide(
            self.event(NotificationEventType.TASK_UNPLACED),
            preferences, now_utc=at_night,
        )
        self.assertEqual(datetime(2026, 10, 9, 7, 0), normal.scheduled_for)
        self.assertEqual(at_night, high.scheduled_for)

    def test_quiet_hours_never_move_a_later_explicit_schedule_earlier(self):
        preferences = dict(
            self.preferences, timezone="UTC",
            quiet_hours_start="22:00", quiet_hours_end="07:00",
        )
        decision = self.policy.decide(
            self.event(
                NotificationEventType.TASK_REMINDER_DUE,
                scheduled_for="2026-10-09T08:50:00",
                expires_at="2026-10-09T10:00:00",
            ),
            preferences, now_utc=datetime(2026, 10, 8, 23, 0),
        )
        self.assertEqual(datetime(2026, 10, 9, 8, 50), decision.scheduled_for)

    def test_explicit_timing_expiration_and_dedupe(self):
        scheduled = self.now + timedelta(hours=2)
        expires = scheduled + timedelta(minutes=30)
        decision = self.policy.decide(
            self.event(
                NotificationEventType.TASK_REMINDER_DUE,
                scheduled_for=scheduled.isoformat(), expires_at=expires.isoformat(),
                dedupe_key="task:4:2026-10-08:0:reminder:rev7",
            ),
            self.preferences, now_utc=self.now,
        )
        self.assertEqual(scheduled, decision.scheduled_for)
        self.assertEqual(expires, decision.expires_at)
        self.assertEqual("task:4:2026-10-08:0:reminder:rev7", decision.dedupe_key)

    def test_dst_gap_quiet_end_advances_to_first_valid_minute(self):
        # New York jumps from 01:59 to 03:00 on this date.
        end = quiet_hours_end_utc(
            datetime(2026, 3, 8, 6, 30), "America/New_York", "22:00", "02:30",
        )
        self.assertEqual(datetime(2026, 3, 8, 7, 0), end)


if __name__ == "__main__":
    unittest.main()
