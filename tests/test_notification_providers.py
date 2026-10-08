from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from database import OwnershipError
from notifications import DeliveryChannel, DomainEvent, NotificationEventType, NotificationType
from notifications.providers import (
    APNsConfig, APNsProvider, ErrorClassification, FCMConfig, FCMProvider,
    ProviderResult, PushMessage, build_push_payload,
)
from notifications.service import NotificationService
from tests.notification_test_support import NotificationTestCase


class _FakeCredentials:
    @staticmethod
    def Certificate(path):
        return ("certificate", path)


class _FakeFirebase:
    credentials = _FakeCredentials()

    @staticmethod
    def get_app(name):
        raise ValueError("not initialized")

    @staticmethod
    def initialize_app(credential, options, name):
        return {"credential": credential, "options": options, "name": name}


class _FakeMessaging:
    response = "projects/test/messages/1"
    error = None
    last_message = None

    class Notification:
        def __init__(self, **kwargs):
            self.values = kwargs

    class Message:
        def __init__(self, **kwargs):
            self.values = kwargs

    @classmethod
    def send(cls, message, **kwargs):
        cls.last_message = message
        if cls.error:
            raise cls.error
        return cls.response


class ProviderContractTests(unittest.TestCase):
    def message(self):
        return PushMessage(
            token="test-token", title="Title", body="Body",
            data={"payload_version": "1", "action": "OPEN"},
            notification_id="n1", delivery_id="d1", app_identifier="com.example.app",
        )

    def test_payload_is_minimal_versioned_and_actionable(self):
        payload = build_push_payload({
            "notification_id": "n1", "notification_type": "deadline_risk",
            "task_id": 9, "segment_id": 4, "segment_index": 0,
            "schedule_revision": 7, "schedule_operation_id": "op",
            "action_metadata": '{"complete_task":true}',
            "body": "must not leak", "title": "must not leak",
        })
        self.assertEqual("1", payload["payload_version"])
        self.assertEqual("COMPLETE", payload["action"])
        self.assertEqual("7", payload["schedule_revision"])
        self.assertNotIn("body", payload)
        self.assertNotIn("title", payload)

    def test_fcm_accepted_contract(self):
        _FakeMessaging.error = None
        provider = FCMProvider(
            FCMConfig(enabled=True, project_id="test", dry_run=True),
            firebase_module=_FakeFirebase, messaging_module=_FakeMessaging,
        )
        result = provider.send(self.message())
        self.assertTrue(result.accepted)
        self.assertEqual("projects/test/messages/1", result.provider_message_id)
        self.assertEqual("test-token", _FakeMessaging.last_message.values["token"])

    def test_fcm_missing_credential_file_is_configuration_error(self):
        provider = FCMProvider(
            FCMConfig(enabled=True, credentials_path="missing-secret.json"),
            firebase_module=_FakeFirebase, messaging_module=_FakeMessaging,
        )
        result = provider.send(self.message())
        self.assertEqual(ErrorClassification.CONFIGURATION, result.classification)

    def test_fcm_error_classification(self):
        cases = {
            "UnregisteredError": ErrorClassification.PERMANENT_TOKEN,
            "SenderIdMismatchError": ErrorClassification.PERMANENT_TOKEN,
            "QuotaExceededError": ErrorClassification.RATE_LIMITED,
            "ThirdPartyAuthError": ErrorClassification.AUTHENTICATION,
            "InvalidArgumentError": ErrorClassification.PERMANENT_REQUEST,
            "UnavailableError": ErrorClassification.TRANSIENT,
            "UnexpectedProviderError": ErrorClassification.UNKNOWN,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(expected, FCMProvider.classify_error(type(name, (Exception,), {})()))

    def test_fcm_invalid_token_result(self):
        _FakeMessaging.error = type("UnregisteredError", (Exception,), {})()
        provider = FCMProvider(
            FCMConfig(enabled=True), firebase_module=_FakeFirebase, messaging_module=_FakeMessaging,
        )
        self.assertEqual(ErrorClassification.PERMANENT_TOKEN, provider.send(self.message()).classification)
        _FakeMessaging.error = None

    def setUp(self):
        self.keys = tempfile.TemporaryDirectory()
        key = ec.generate_private_key(ec.SECP256R1())
        self.key_path = Path(self.keys.name) / "AuthKey_TEST.p8"
        self.key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))

    def tearDown(self):
        self.keys.cleanup()

    def apns_config(self, **changes):
        values = dict(
            enabled=True, environment="sandbox", key_id="KEY1234567",
            team_id="TEAM123456", bundle_id="com.example.app",
            private_key_path=str(self.key_path), timeout_seconds=2,
        )
        values.update(changes)
        return APNsConfig(**values)

    def test_apns_token_auth_and_accepted_response(self):
        class Response:
            status_code = 200
            headers = {"apns-id": "apns-1"}
        class Client:
            def post(inner, url, **kwargs):
                self.assertIn("api.sandbox.push.apple.com", url)
                self.assertTrue(kwargs["headers"]["authorization"].startswith("bearer "))
                self.assertEqual("com.example.app", kwargs["headers"]["apns-topic"])
                return Response()
        result = APNsProvider(self.apns_config(), client=Client(), clock=lambda: 1_800_000_000).send(self.message())
        self.assertTrue(result.accepted)
        self.assertEqual("apns-1", result.provider_message_id)

    def test_apns_response_classification(self):
        cases = [
            (400, "BadDeviceToken", ErrorClassification.PERMANENT_TOKEN),
            (410, "Unregistered", ErrorClassification.PERMANENT_TOKEN),
            (403, "InvalidProviderToken", ErrorClassification.AUTHENTICATION),
            (429, "TooManyRequests", ErrorClassification.RATE_LIMITED),
            (500, "InternalServerError", ErrorClassification.TRANSIENT),
            (400, "BadTopic", ErrorClassification.PERMANENT_REQUEST),
        ]
        for status, reason, expected in cases:
            with self.subTest(reason=reason):
                self.assertEqual(expected, APNsProvider.classify_response(status, reason))

    def test_apns_rate_limit_preserves_retry_guidance(self):
        class Response:
            status_code = 429
            headers = {"retry-after": "120"}
            @staticmethod
            def json(): return {"reason": "TooManyRequests"}
        class Client:
            @staticmethod
            def post(*args, **kwargs): return Response()
        result = APNsProvider(self.apns_config(), client=Client()).send(self.message())
        self.assertEqual(ErrorClassification.RATE_LIMITED, result.classification)
        self.assertEqual(120, result.retry_after_seconds)

    def test_apns_timeout_is_transient(self):
        class Client:
            @staticmethod
            def post(*args, **kwargs): raise httpx.ReadTimeout("timeout")
        result = APNsProvider(self.apns_config(), client=Client()).send(self.message())
        self.assertEqual(ErrorClassification.TRANSIENT, result.classification)

    def test_apns_missing_configuration_fails_without_network(self):
        result = APNsProvider(APNsConfig(enabled=True)).send(self.message())
        self.assertEqual(ErrorClassification.CONFIGURATION, result.classification)


class _SequenceProvider:
    def __init__(self, name, *results):
        self.name = name
        self.results = list(results)
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class PushDeliveryRuntimeTests(NotificationTestCase):
    NOW = datetime(2026, 10, 8, 12, 0)

    def configure_push(self, provider="fcm", device_id="phone", primary=True):
        platform = "android" if provider == "fcm" else "ios"
        self.repo.register_device(
            "u1", device_id=device_id, platform=platform, provider=provider,
            push_token=f"token-{device_id}", app_identifier="com.example.app",
            provider_metadata={"environment": "test"}, is_primary=primary,
        )
        self.repo.set_type_preference(
            "u1", NotificationType.TASK_UNPLACED,
            preferred_channel=DeliveryChannel.PUSH,
        )

    def create_push(self, key="push"):
        notification = self.service.process_event(DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Push"}, idempotency_key=key, occurred_at=self.NOW,
        ), now_utc=self.NOW)
        self.db.commit()
        return notification

    def test_accepted_is_sent_not_device_delivered(self):
        self.configure_push()
        notification = self.create_push()
        provider = _SequenceProvider("fcm", ProviderResult(True, provider_message_id="fcm-1"))
        service = NotificationService(self.db, push_providers={"fcm": provider})
        result = service.process_push_deliveries(now_utc=self.NOW, worker_id="p1")
        delivery = self.repo.list_push_deliveries("u1")[0]
        self.assertEqual(1, result["accepted"])
        self.assertEqual("sent", delivery["status"])
        self.assertIsNotNone(delivery["accepted_at"])
        self.assertIsNone(delivery["delivered_at"])
        self.assertEqual(notification["notification_id"], provider.messages[0].data["notification_id"])

    def test_permanent_token_invalidates_only_that_device(self):
        self.configure_push()
        self.create_push()
        provider = _SequenceProvider("fcm", ProviderResult(
            False, classification=ErrorClassification.PERMANENT_TOKEN,
            error_code="UNREGISTERED",
        ))
        result = NotificationService(self.db, push_providers={"fcm": provider}).process_push_deliveries(
            now_utc=self.NOW, worker_id="p1",
        )
        delivery = self.repo.list_push_deliveries("u1")[0]
        device = self.repo.get_device("u1", "phone")
        self.assertEqual(1, result["invalid_tokens"])
        self.assertEqual("failed", delivery["status"])
        self.assertFalse(device["is_enabled"])
        self.assertIsNone(device["push_token"])

    def test_transient_retry_preserves_delivery_identity_and_backoff(self):
        self.configure_push()
        self.create_push()
        provider = _SequenceProvider(
            "fcm",
            ProviderResult(False, classification=ErrorClassification.TRANSIENT, error_code="timeout"),
            ProviderResult(True, provider_message_id="fcm-2"),
        )
        service = NotificationService(self.db, push_providers={"fcm": provider})
        first = service.process_push_deliveries(now_utc=self.NOW, worker_id="p1")
        original = self.repo.list_push_deliveries("u1")[0]
        self.assertEqual(1, first["retrying"])
        self.assertEqual("2026-10-08 12:00:30.000000", str(original["next_attempt_at"]))
        self.assertEqual(0, service.process_push_deliveries(now_utc=self.NOW + timedelta(seconds=29))["attempted"])
        second = service.process_push_deliveries(now_utc=self.NOW + timedelta(seconds=30), worker_id="p2")
        stored = self.repo.list_push_deliveries("u1")[0]
        self.assertEqual(1, second["accepted"])
        self.assertEqual(original["delivery_id"], stored["delivery_id"])
        self.assertEqual(2, stored["retry_count"])

    def test_rate_limit_uses_bounded_provider_guidance(self):
        self.configure_push()
        self.create_push()
        provider = _SequenceProvider("fcm", ProviderResult(
            False, classification=ErrorClassification.RATE_LIMITED,
            error_code="quota", retry_after_seconds=1200,
        ))
        NotificationService(self.db, push_providers={"fcm": provider}).process_push_deliveries(
            now_utc=self.NOW, worker_id="p1",
        )
        delivery = self.repo.list_push_deliveries("u1")[0]
        self.assertEqual("2026-10-08 12:15:00.000000", str(delivery["next_attempt_at"]))

    def test_configuration_and_permanent_request_do_not_retry(self):
        for index, classification in enumerate((ErrorClassification.CONFIGURATION, ErrorClassification.PERMANENT_REQUEST)):
            with self.subTest(classification=classification):
                device = f"phone-{index}"
                self.configure_push(device_id=device)
                self.repo.update_preferences("u1", multi_device_policy="all")
                self.create_push(f"permanent:{index}")
                provider = _SequenceProvider("fcm", *[
                    ProviderResult(False, classification=classification, error_code="bad") for _ in range(index + 1)
                ])
                NotificationService(self.db, push_providers={"fcm": provider}).process_push_deliveries(
                    now_utc=self.NOW, worker_id=f"p{index}",
                )
        self.assertTrue(all(row["status"] == "failed" for row in self.repo.list_push_deliveries("u1")))

    def test_multi_device_failure_isolation(self):
        self.repo.update_preferences("u1", multi_device_policy="all")
        self.configure_push("fcm", "android")
        self.configure_push("apns", "ios", primary=False)
        self.create_push()
        fcm = _SequenceProvider("fcm", ProviderResult(False, classification=ErrorClassification.TRANSIENT, error_code="down"))
        apns = _SequenceProvider("apns", ProviderResult(True, provider_message_id="apns-1"))
        result = NotificationService(self.db, push_providers={"fcm": fcm, "apns": apns}).process_push_deliveries(
            now_utc=self.NOW, worker_id="multi",
        )
        self.assertEqual(2, result["attempted"])
        self.assertEqual(1, result["accepted"])
        self.assertEqual(1, result["retrying"])
        self.assertEqual(1, len(self.repo.list_notifications("u1")))

    def test_primary_policy_sends_only_primary_device(self):
        self.configure_push("fcm", "primary", primary=True)
        self.configure_push("fcm", "secondary", primary=False)
        self.create_push()
        deliveries = self.repo.list_push_deliveries("u1")
        self.assertEqual(["primary"], [row["device_id"] for row in deliveries])

    def test_push_claim_lease_and_recovery(self):
        self.configure_push()
        self.create_push()
        first = self.repo.claim_push_deliveries(now=self.NOW, worker_id="a", stale_after_seconds=60)
        self.assertEqual(1, len(first))
        self.assertEqual([], self.repo.claim_push_deliveries(now=self.NOW + timedelta(seconds=59), worker_id="b", stale_after_seconds=60))
        recovered = self.repo.claim_push_deliveries(now=self.NOW + timedelta(seconds=61), worker_id="b", stale_after_seconds=60)
        self.assertEqual(1, len(recovered))

    def test_completed_task_push_is_cancelled_before_provider(self):
        self.configure_push()
        task_id = self.direct_task(title="Done")
        event = DomainEvent.create(
            user_id="u1", event_type=NotificationEventType.TASK_UNPLACED,
            task_id=task_id, payload={"task_title": "Done"},
            idempotency_key="completed-push", occurred_at=self.NOW,
        )
        self.service.process_event(event, now_utc=self.NOW)
        self.db.execute("UPDATE tasks SET status='completed' WHERE task_id=?", (task_id,))
        self.db.commit()
        provider = _SequenceProvider("fcm", ProviderResult(True))
        result = NotificationService(self.db, push_providers={"fcm": provider}).process_push_deliveries(now_utc=self.NOW)
        self.assertEqual(1, result["cancelled"])
        self.assertEqual([], provider.messages)

    def test_delivery_ownership_is_enforced(self):
        self.configure_push()
        self.create_push()
        delivery_id = self.repo.list_push_deliveries("u1")[0]["delivery_id"]
        with self.assertRaises(OwnershipError):
            self.repo.get_delivery("u2", delivery_id)
        with self.assertRaises(OwnershipError):
            self.repo.mark_push_accepted("u2", delivery_id, worker_id="x", provider_message_id=None, provider="fcm")

    def test_duplicate_processing_does_not_resend_accepted_delivery(self):
        self.configure_push()
        self.create_push()
        provider = _SequenceProvider("fcm", ProviderResult(True, provider_message_id="one"))
        service = NotificationService(self.db, push_providers={"fcm": provider})
        service.process_push_deliveries(now_utc=self.NOW)
        service.process_push_deliveries(now_utc=self.NOW + timedelta(hours=1))
        self.assertEqual(1, len(provider.messages))
        self.assertEqual(1, len(self.repo.list_push_deliveries("u1")))


if __name__ == "__main__":
    unittest.main()
