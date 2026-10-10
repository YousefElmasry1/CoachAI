from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from backend_config import BackendSettings
from backend_errors import ConfigurationError, ExternalProviderFailure
from external_resilience import (
    CircuitBreaker,
    CircuitOpenError,
    ExternalCallPolicy,
    FailureClass,
    RetryPolicy,
    call_with_resilience,
    classify_external_failure,
)
from observability import (
    configure_structured_logging,
    correlation_context,
    current_correlation,
    redact,
    redact_text,
)
from operational_health import HealthRegistry, database_ping_check
from operational_metrics import BackendMetrics, InMemoryMetricSink


class OperationalControlTests(unittest.TestCase):
    def test_recursive_redaction_covers_camel_case_headers_urls_and_jwts(self):
        token = "eyJhbGciOiJIUzI1NiJ9.c2VjcmV0.c2lnbmF0dXJl"
        value = {
            "passwordHash": "hash-secret",
            "nested": {
                "refreshToken": "refresh-secret",
                "message": (
                    f"Authorization: Bearer access-secret {token} "
                    "https://user:password@example.test/path?api_key=query-secret"
                ),
            },
        }
        encoded = json.dumps(redact(value), sort_keys=True)
        for secret in (
            "hash-secret", "refresh-secret", "access-secret", token,
            "user:password", "query-secret",
        ):
            self.assertNotIn(secret, encoded)
        self.assertIn("[REDACTED]", encoded)
        self.assertNotIn(
            "client-secret",
            redact_text("https://client:client-secret@example.test"),
        )

    def test_structured_logging_keeps_only_exception_type_and_context(self):
        stream = io.StringIO()
        logger = logging.getLogger("coachai.tests.redaction")
        logger.handlers.clear()
        logger.propagate = False
        configure_structured_logging(logger=logger, stream=stream)
        with correlation_context(
            "request-1", operation_id="operation-1", principal_id="user-1"
        ):
            try:
                raise RuntimeError("Bearer provider-secret")
            except RuntimeError:
                logger.exception("provider call failed")
        payload = json.loads(stream.getvalue())
        self.assertEqual("request-1", payload["correlation_id"])
        self.assertEqual("RuntimeError", payload["exception_type"])
        self.assertNotIn("provider-secret", stream.getvalue())
        self.assertNotIn("Traceback", stream.getvalue())
        self.assertEqual({}, current_correlation())

    def test_metrics_are_low_cardinality_and_reject_sensitive_labels(self):
        sink = InMemoryMetricSink()
        metrics = BackendMetrics(sink)
        metrics.worker_cycle("calendar", succeeded=True, duration_seconds=0.25)
        metrics.notification_lag("push", 3.5)
        metrics.ai_call("google", "gemini", succeeded=False, duration_seconds=1.0)
        snapshot = sink.snapshot()
        self.assertEqual(
            1.0,
            snapshot["counters"]["coachai_worker_cycles_total"][
                "result=success,worker=calendar"
            ],
        )
        with self.assertRaises(ValueError):
            sink.increment("coachai_bad_total", labels={"access_token": "secret"})

    def test_health_failures_expose_type_not_exception_message(self):
        secret = "database-password-secret"
        registry = HealthRegistry()

        def broken():
            raise RuntimeError(secret)

        registry.register("database", broken)
        result = registry.readiness()
        encoded = json.dumps(result)
        self.assertEqual("unhealthy", result["status"])
        self.assertEqual(
            "RuntimeError",
            result["checks"]["database"]["details"]["exception_type"],
        )
        self.assertNotIn(secret, encoded)
        ping = database_ping_check(broken).public()
        self.assertNotIn(secret, json.dumps(ping))

    def test_resilience_is_bounded_and_classifies_wrapped_status(self):
        class Response:
            status_code = 429

        class ProviderError(Exception):
            response = Response()

        wrapped = RuntimeError("safe wrapper")
        wrapped.__cause__ = ProviderError("provider body secret")
        self.assertEqual(FailureClass.RATE_LIMITED, classify_external_failure(wrapped))

        attempts = []
        sleeps = []

        def operation(timeout):
            attempts.append(timeout)
            raise TimeoutError("provider secret")

        policy = ExternalCallPolicy(
            timeout_seconds=2,
            retry=RetryPolicy(
                max_attempts=3, base_delay_seconds=0.1,
                max_delay_seconds=1, jitter_ratio=0,
            ),
        )
        with self.assertRaises(ExternalProviderFailure) as caught:
            call_with_resilience(
                operation, component="calendar", policy=policy,
                sleep=sleeps.append, random_value=lambda: 0.5,
            )
        self.assertEqual([2, 2, 2], attempts)
        self.assertEqual([0.1, 0.2], sleeps)
        self.assertNotIn("provider secret", str(caught.exception))

    def test_circuit_breaker_opens_and_allows_one_half_open_probe(self):
        now = [0.0]
        circuit = CircuitBreaker(
            failure_threshold=2,
            recovery_seconds=10,
            monotonic=lambda: now[0],
        )
        circuit.failed()
        circuit.failed()
        with self.assertRaises(CircuitOpenError):
            circuit.before_call()
        now[0] = 10.0
        circuit.before_call()
        with self.assertRaises(CircuitOpenError):
            circuit.before_call()
        circuit.succeeded()
        circuit.before_call()

    def test_typed_configuration_enforces_upper_bounds(self):
        environment = {
            "DATABASE_URL": "sqlite:///test.db",
            "COACHAI_DB_POOL_SIZE": "101",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigurationError):
                BackendSettings.from_env()
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}, clear=True):
            settings = BackendSettings.from_env()
        self.assertEqual(5, settings.db_pool_size)
        self.assertEqual(15 * 1024 * 1024, settings.max_audio_bytes)

    def test_dependency_lock_contains_only_exact_pins(self):
        lock = Path(__file__).resolve().parents[1] / "requirements-lock.txt"
        packages = []
        for line in lock.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            self.assertEqual(1, cleaned.count("=="), cleaned)
            name, version = cleaned.split("==", 1)
            self.assertTrue(name and version)
            self.assertNotIn("*", version)
            packages.append(name.casefold())
        self.assertEqual(len(packages), len(set(packages)))
        self.assertIn("alembic", packages)


if __name__ == "__main__":
    unittest.main()
