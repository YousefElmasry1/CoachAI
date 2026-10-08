from database import OwnershipError
from notifications import DomainEvent, NotificationEventType, RiskState

from tests.notification_test_support import NotificationTestCase


class NotificationSecurityTests(NotificationTestCase):
    def test_preferences_and_outbox_are_tenant_scoped(self):
        self.repo.update_preferences("u1", locale="ar", tone="friendly")
        self.repo.update_preferences("u2", locale="en", tone="neutral")
        self.repo.enqueue_event(DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="u1-only",
        ))
        self.db.connection.commit()
        self.assertEqual("ar", self.repo.get_preferences("u1")["locale"])
        self.assertEqual("en", self.repo.get_preferences("u2")["locale"])
        self.assertEqual(1, len(self.repo.list_outbox("u1")))
        self.assertEqual([], self.repo.list_outbox("u2"))

    def test_device_cannot_be_claimed_or_read_by_another_user(self):
        self.repo.register_device(
            "u1", device_id="owned", platform="android", provider="fcm",
            push_token="private-token",
        )
        with self.assertRaises(OwnershipError):
            self.repo.get_device("u2", "owned")
        with self.assertRaises(OwnershipError):
            self.repo.register_device(
                "u2", device_id="owned", platform="android", provider="fcm",
                push_token="other-token",
            )

    def test_notification_delivery_read_and_action_are_owner_scoped(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Private"}, idempotency_key="private-notification",
        )
        notification = self.service.process_event(event)
        self.db.connection.commit()
        notification_id = notification["notification_id"]
        for action in (
            lambda: self.repo.get_notification("u2", notification_id),
            lambda: self.repo.create_delivery("u2", notification_id, "in_app"),
            lambda: self.repo.mark_read("u2", notification_id),
            lambda: self.repo.mark_acted("u2", notification_id),
        ):
            with self.assertRaises(OwnershipError):
                action()
        self.assertEqual(1, len(self.repo.list_notifications("u1")))
        self.assertEqual([], self.repo.list_notifications("u2"))

    def test_in_app_unread_read_and_acted_state(self):
        notification = self.service.process_event(DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Act on me"}, idempotency_key="read-state",
        ))
        self.db.connection.commit()
        self.assertEqual(1, self.service.unread_count("u1"))
        self.assertEqual(1, len(self.service.list_in_app("u1", unread_only=True)))
        self.service.mark_read("u1", notification["notification_id"])
        self.assertEqual(0, self.service.unread_count("u1"))
        self.service.mark_acted("u1", notification["notification_id"])
        stored = self.repo.get_notification("u1", notification["notification_id"])
        self.assertIsNotNone(stored["read_at"])
        self.assertIsNotNone(stored["acted_at"])

    def test_task_notification_state_rejects_cross_tenant_task(self):
        task_id = self.direct_task("u1")
        with self.assertRaises(OwnershipError):
            self.repo.set_task_notification_state_no_commit(
                "u2", task_id, RiskState.AT_RISK, transitioned=True,
            )
        self.db.connection.rollback()

    def test_outbox_completion_requires_matching_owner(self):
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="owner-event",
        )
        self.repo.enqueue_event(event)
        self.db.connection.commit()
        with self.assertRaises(OwnershipError):
            self.repo.mark_outbox_processed(event.event_id, "u2")
        self.db.connection.rollback()
        self.assertEqual("queued", self.repo.list_outbox("u1")[0]["status"])


if __name__ == "__main__":
    import unittest
    unittest.main()
