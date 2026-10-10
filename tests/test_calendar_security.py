import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from backend_errors import (
    AuthorizationDenied,
    IdempotencyConflict,
    ResourceNotFound,
    ValidationError,
    VersionConflict,
)
from calendar_security import (
    CalendarOperationQueue,
    CalendarSecurityError,
    CalendarSyncStateStore,
    EncryptedGoogleTokenStore,
    OAuthStateStore,
    VersionedTokenCipher,
)
from database import Database
from google_calendar import GoogleCalendarClient, GoogleCalendarError
from identity import AuthenticatedPrincipal


class CalendarSecurityTests(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "calendar-security.db"
        self.db = Database(f"sqlite:///{db_path.as_posix()}")
        self.db.execute(
            "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
            ("u1", "one@example.com", "One"),
        )
        self.db.execute(
            "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
            ("u2", "two@example.com", "Two"),
        )
        self.db.commit()
        self.u1 = AuthenticatedPrincipal("u1", "one@example.com", "One", {})
        self.u2 = AuthenticatedPrincipal("u2", "two@example.com", "Two", {})

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_oauth_state_is_hashed_principal_bound_pkce_and_single_use(self):
        store = OAuthStateStore(self.db)
        request = store.issue(
            self.u1,
            redirect_uri="https://app.example.test/oauth/google/callback",
            now=self.NOW,
        )
        persisted = self.db.fetch_one("SELECT * FROM oauth_states")
        self.assertNotEqual(request.state, persisted["state_hash"])
        self.assertEqual(
            hashlib.sha256(request.state.encode()).hexdigest(), persisted["state_hash"]
        )
        verifier = persisted["code_verifier"]
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(expected_challenge, request.code_challenge)
        self.assertEqual("S256", request.code_challenge_method)

        with self.assertRaises(AuthorizationDenied):
            store.consume(
                self.u2,
                state=request.state,
                redirect_uri="https://app.example.test/oauth/google/callback",
                now=self.NOW,
            )
        consumed = store.consume(
            self.u1,
            state=request.state,
            redirect_uri="https://app.example.test/oauth/google/callback",
            now=self.NOW,
        )
        self.assertEqual(verifier, consumed.code_verifier)
        with self.assertRaises(AuthorizationDenied):
            store.consume(
                self.u1,
                state=request.state,
                redirect_uri="https://app.example.test/oauth/google/callback",
                now=self.NOW,
            )

    def test_oauth_state_rejects_expiry_redirect_mismatch_and_unknown_user(self):
        store = OAuthStateStore(self.db)
        request = store.issue(
            self.u1, redirect_uri="http://localhost:8501/callback",
            ttl_seconds=1, now=self.NOW,
        )
        with self.assertRaises(AuthorizationDenied):
            store.consume(
                self.u1,
                state=request.state,
                redirect_uri="http://localhost:8501/wrong",
                now=self.NOW,
            )
        with self.assertRaises(AuthorizationDenied):
            store.consume(
                self.u1,
                state=request.state,
                redirect_uri="http://localhost:8501/callback",
                now=self.NOW + timedelta(seconds=2),
            )
        missing = AuthenticatedPrincipal("missing", "m@example.com", "Missing", {})
        with self.assertRaises(ResourceNotFound):
            store.issue(missing, redirect_uri="https://app.example.test/callback")

    def test_versioned_cipher_round_trip_tamper_rejection_and_key_rotation(self):
        old_key = VersionedTokenCipher.generate_key()
        new_key = VersionedTokenCipher.generate_key()
        old_cipher = VersionedTokenCipher({"old": old_key}, active_key_id="old")
        envelope = old_cipher.encrypt("google-refresh-token")
        self.assertTrue(envelope.startswith("coachai:v1:old:"))
        self.assertNotIn("google-refresh-token", envelope)

        rotating = VersionedTokenCipher(
            {"old": old_key, "2026q4": new_key}, active_key_id="2026q4"
        )
        self.assertEqual("google-refresh-token", rotating.decrypt(envelope))
        self.assertTrue(rotating.needs_rotation(envelope))
        rotated = rotating.rotate(envelope)
        self.assertTrue(rotated.startswith("coachai:v1:2026q4:"))
        self.assertFalse(rotating.needs_rotation(rotated))
        self.assertEqual("google-refresh-token", rotating.decrypt(rotated))
        with self.assertRaises(CalendarSecurityError):
            rotating.decrypt(rotated[:-1] + ("A" if rotated[-1] != "A" else "B"))
        with self.assertRaises(CalendarSecurityError):
            rotating.decrypt("plaintext-token")

    def test_google_token_store_encrypts_at_rest_and_rotates_without_cross_tenant_access(self):
        old_key = VersionedTokenCipher.generate_key()
        new_key = VersionedTokenCipher.generate_key()
        old_store = EncryptedGoogleTokenStore(
            self.db, VersionedTokenCipher({"old": old_key}, active_key_id="old")
        )
        saved = old_store.save(
            self.u1,
            access_token="access-secret",
            refresh_token="refresh-secret",
            token_expiry=self.NOW + timedelta(hours=1),
            scopes="calendar.readonly calendar.events",
        )
        self.assertEqual("access-secret", saved.access_token)
        raw = self.db.fetch_one(
            "SELECT access_token,refresh_token FROM google_oauth_tokens WHERE user_id='u1'"
        )
        self.assertNotIn("access-secret", raw["access_token"])
        self.assertNotIn("refresh-secret", raw["refresh_token"])
        self.assertIsNone(old_store.get(self.u2))

        rotating_store = EncryptedGoogleTokenStore(
            self.db,
            VersionedTokenCipher(
                {"old": old_key, "new": new_key}, active_key_id="new"
            ),
        )
        loaded = rotating_store.get(self.u1)
        self.assertEqual("access-secret", loaded.access_token)
        self.assertEqual("refresh-secret", loaded.refresh_token)
        rotated = self.db.fetch_one(
            "SELECT access_token,refresh_token FROM google_oauth_tokens WHERE user_id='u1'"
        )
        self.assertTrue(rotated["access_token"].startswith("coachai:v1:new:"))
        self.assertTrue(rotated["refresh_token"].startswith("coachai:v1:new:"))
        # A refresh response commonly omits refresh_token; preserve the
        # already encrypted one without ever asking the caller to resend it.
        updated = rotating_store.save(
            self.u1,
            access_token="refreshed-access",
            refresh_token=None,
            token_expiry=self.NOW + timedelta(hours=2),
        )
        self.assertEqual("refresh-secret", updated.refresh_token)
        self.assertTrue(rotating_store.delete(self.u1))
        self.assertIsNone(rotating_store.get(self.u1))

    def test_operation_enqueue_is_idempotent_tenant_scoped_and_secret_free(self):
        queue = CalendarOperationQueue(self.db)
        first, created = queue.enqueue(
            self.u1,
            operation_type="event.upsert",
            idempotency_key="task-10-rev-2",
            payload={"task_id": 10, "calendar_id": "primary"},
            available_at=self.NOW,
        )
        duplicate, duplicate_created = queue.enqueue(
            self.u1,
            operation_type="event.upsert",
            idempotency_key="task-10-rev-2",
            payload={"calendar_id": "primary", "task_id": 10},
            available_at=self.NOW + timedelta(hours=1),
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.operation_id, duplicate.operation_id)
        with self.assertRaises(IdempotencyConflict):
            queue.enqueue(
                self.u1,
                operation_type="event.upsert",
                idempotency_key="task-10-rev-2",
                payload={"task_id": 11},
            )
        with self.assertRaises(ValidationError):
            queue.enqueue(
                self.u1,
                operation_type="event.delete",
                idempotency_key="secret-leak",
                payload={"nested": {"refresh_token": "must-not-persist"}},
            )
        with self.assertRaises(ResourceNotFound):
            queue.get(self.u2, first.operation_id)

    def test_operation_worker_lease_retry_redaction_and_completion(self):
        queue = CalendarOperationQueue(self.db)
        operation, _ = queue.enqueue(
            self.u1,
            operation_type="event.delete",
            idempotency_key="delete-one",
            payload={"event_id": "evt-1"},
            available_at=self.NOW,
        )
        claimed = queue.claim_ready(worker_id="worker-a", now=self.NOW)
        self.assertEqual([operation.operation_id], [item.operation_id for item in claimed])
        self.assertEqual(1, claimed[0].attempts)
        self.assertEqual(
            [], queue.claim_ready(worker_id="worker-b", now=self.NOW + timedelta(seconds=299))
        )
        with self.assertRaises(VersionConflict):
            queue.mark_completed(operation.operation_id, worker_id="worker-b", now=self.NOW)

        failed = queue.mark_failed(
            operation.operation_id,
            worker_id="worker-a",
            error=RuntimeError("Bearer super-secret-token"),
            now=self.NOW,
        )
        self.assertEqual("failed", failed.status)
        self.assertEqual("Calendar provider operation failed.", failed.last_error)
        self.assertNotIn("super-secret", failed.last_error)
        self.assertEqual(
            [], queue.claim_ready(worker_id="worker-b", now=self.NOW + timedelta(seconds=29))
        )
        retried = queue.claim_ready(
            worker_id="worker-b", now=self.NOW + timedelta(seconds=30)
        )
        self.assertEqual(2, retried[0].attempts)
        completed = queue.mark_completed(
            operation.operation_id,
            worker_id="worker-b",
            now=self.NOW + timedelta(seconds=31),
        )
        self.assertEqual("completed", completed.status)
        self.assertEqual([], queue.claim_ready(worker_id="worker-c", now=self.NOW + timedelta(days=1)))

    def test_operation_stale_lease_is_recoverable_and_permanent_failure_stops(self):
        queue = CalendarOperationQueue(self.db)
        operation, _ = queue.enqueue(
            self.u1,
            operation_type="sync.range",
            idempotency_key="sync-one",
            payload={"range_start": "2026-10-08", "range_end": "2026-10-14"},
            available_at=self.NOW,
        )
        queue.claim_ready(worker_id="abandoned", now=self.NOW, stale_after_seconds=60)
        recovered = queue.claim_ready(
            worker_id="recovery",
            now=self.NOW + timedelta(seconds=61),
            stale_after_seconds=60,
        )
        self.assertEqual(operation.operation_id, recovered[0].operation_id)
        stopped = queue.mark_failed(
            operation.operation_id,
            worker_id="recovery",
            error="Calendar authorization was revoked.",
            now=self.NOW + timedelta(seconds=61),
            retryable=False,
        )
        self.assertEqual(5, stopped.attempts)
        self.assertEqual("Calendar authorization was revoked.", stopped.last_error)
        self.assertEqual(
            [], queue.claim_ready(worker_id="later", now=self.NOW + timedelta(days=1))
        )

    def test_sync_state_is_bounded_tenant_scoped_and_errors_are_redacted(self):
        store = CalendarSyncStateStore(self.db)
        pending = store.mark_pending(
            self.u1,
            calendar_id="primary",
            range_start=date(2026, 10, 8),
            range_end=date(2026, 12, 30),
            now=self.NOW,
        )
        self.assertEqual("pending", pending["status"])
        self.assertIsNone(store.get(self.u2, calendar_id="primary"))
        self.assertEqual("running", store.mark_running(
            self.u1, calendar_id="primary", now=self.NOW
        )["status"])
        failed = store.mark_error(
            self.u1,
            calendar_id="primary",
            error=RuntimeError("access_token=secret"),
            now=self.NOW,
        )
        self.assertEqual("Calendar provider operation failed.", failed["last_error"])
        self.assertEqual("running", store.mark_running(
            self.u1, calendar_id="primary", now=self.NOW
        )["status"])
        fresh = store.mark_fresh(
            self.u1,
            calendar_id="primary",
            sync_token="opaque-incremental-cursor",
            now=self.NOW,
        )
        self.assertEqual("fresh", fresh["status"])
        with self.assertRaises(ValidationError):
            store.mark_pending(
                self.u1,
                calendar_id="primary",
                range_start=date(2026, 10, 8),
                range_end=date(2027, 1, 1),
            )

    def test_google_authorization_transport_carries_pkce_and_hides_provider_body(self):
        url = GoogleCalendarClient.build_auth_url(
            "client-id",
            "https://app.example.test/callback",
            state="opaque-state",
            code_challenge="pkce-challenge",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(["opaque-state"], query["state"])
        self.assertEqual(["pkce-challenge"], query["code_challenge"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        with self.assertRaises(GoogleCalendarError):
            GoogleCalendarClient.build_auth_url(
                "client-id", "https://app.example.test/callback", state="only-state"
            )

        failure = Mock(status_code=400, text="refresh_token=provider-secret")
        with patch("requests.post", return_value=failure):
            with self.assertRaises(GoogleCalendarError) as caught:
                GoogleCalendarClient.exchange_code(
                    "code", "client", "secret", "https://app.example.test/callback",
                    code_verifier="verifier",
                )
        self.assertNotIn("provider-secret", str(caught.exception))

        success = Mock(
            status_code=200,
            json=Mock(return_value={"access_token": "a", "refresh_token": "r", "expires_in": 60}),
        )
        with patch("requests.post", return_value=success) as post:
            result = GoogleCalendarClient.exchange_code(
                "code", "client", "secret", "https://app.example.test/callback",
                code_verifier="verifier",
            )
        self.assertEqual("a", result["access_token"])
        self.assertEqual("verifier", post.call_args.kwargs["data"]["code_verifier"])
        self.assertEqual(20.0, post.call_args.kwargs["timeout"])

        with patch("requests.post", return_value=success) as bounded_post:
            GoogleCalendarClient.exchange_code(
                "code", "client", "secret", "https://app.example.test/callback",
                timeout_seconds=999,
            )
        self.assertEqual(120.0, bounded_post.call_args.kwargs["timeout"])


if __name__ == "__main__":
    unittest.main()
