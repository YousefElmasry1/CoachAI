from __future__ import annotations

from datetime import datetime, timezone
import signal
import threading
import unittest
from unittest.mock import patch

from notifications import DomainEvent, NotificationEventType, NotificationWorker
from notifications.worker_runtime import (
    NotificationWorkerRuntime,
    NotificationWorkerSettings,
    WorkerConfigurationError,
)
from tests.notification_test_support import NotificationTestCase


class _FakeWorker:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run_once(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0) if self.responses else {
            "processed": 0,
            "failed": 0,
        }
        if isinstance(response, BaseException):
            raise response
        return response


class _RecordingStopEvent:
    def __init__(self, *, stop_on_wait: bool = False):
        self.flag = False
        self.stop_on_wait = stop_on_wait
        self.waits = []

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.stop_on_wait:
            self.flag = True
        return self.flag


def _settings(**overrides):
    values = {
        "database_url": "sqlite:///unused-worker-runtime.db",
        "worker_id": "runtime-test",
        "poll_interval_seconds": 0.1,
        "error_backoff_seconds": 0.25,
        "batch_size": 2,
        "lease_seconds": 60,
        "max_attempts": 4,
        "push_timeout_seconds": 5,
        "lease_safety_margin_seconds": 10,
        "health_stale_after_seconds": 60,
    }
    values.update(overrides)
    return NotificationWorkerSettings(**values)


class NotificationWorkerSettingsTests(unittest.TestCase):
    def test_environment_configuration_is_validated(self):
        settings = NotificationWorkerSettings.from_env({
            "DATABASE_URL": "postgresql+psycopg2://example.invalid/coachai",
            "COACHAI_NOTIFICATION_WORKER_ID": "notifications-1",
            "COACHAI_NOTIFICATION_POLL_SECONDS": "2",
            "COACHAI_NOTIFICATION_ERROR_BACKOFF_SECONDS": "7.5",
            "COACHAI_NOTIFICATION_BATCH_SIZE": "10",
            "COACHAI_NOTIFICATION_LEASE_SECONDS": "180",
            "COACHAI_NOTIFICATION_MAX_ATTEMPTS": "8",
            "COACHAI_PUSH_TIMEOUT_SECONDS": "12",
            "COACHAI_NOTIFICATION_LEASE_SAFETY_MARGIN_SECONDS": "30",
            "COACHAI_NOTIFICATION_HEALTH_STALE_SECONDS": "180",
            "COACHAI_NOTIFICATION_LOG_LEVEL": "warning",
        })
        self.assertEqual("notifications-1", settings.worker_id)
        self.assertEqual(10, settings.batch_size)
        self.assertEqual(180, settings.lease_seconds)
        self.assertEqual(8, settings.max_attempts)
        self.assertEqual("WARNING", settings.log_level)
        self.assertEqual((), settings.enabled_push_providers)

    def test_database_url_is_required(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "DATABASE_URL"):
            NotificationWorkerSettings.from_env({})

    def test_invalid_values_fail_fast(self):
        base = {"DATABASE_URL": "sqlite:///test.db"}
        invalid = {
            "COACHAI_NOTIFICATION_POLL_SECONDS": "nan",
            "COACHAI_NOTIFICATION_BATCH_SIZE": "0",
            "COACHAI_NOTIFICATION_MAX_ATTEMPTS": "many",
            "COACHAI_FCM_ENABLED": "sometimes",
            "COACHAI_NOTIFICATION_LOG_LEVEL": "verbose",
        }
        for name, value in invalid.items():
            with self.subTest(name=name), self.assertRaises(WorkerConfigurationError):
                NotificationWorkerSettings.from_env({**base, name: value})

    def test_lease_must_cover_worst_case_serial_push_batch(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "too short"):
            NotificationWorkerSettings.from_env({
                "DATABASE_URL": "sqlite:///test.db",
                "COACHAI_NOTIFICATION_BATCH_SIZE": "50",
                "COACHAI_NOTIFICATION_LEASE_SECONDS": "300",
                "COACHAI_PUSH_TIMEOUT_SECONDS": "10",
            })

    def test_enabled_provider_paths_are_validated_without_loading_provider(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "FCM credentials"):
            NotificationWorkerSettings.from_env({
                "DATABASE_URL": "sqlite:///test.db",
                "COACHAI_FCM_ENABLED": "true",
                "COACHAI_FCM_CREDENTIALS": "definitely-does-not-exist.json",
            })
        with self.assertRaisesRegex(WorkerConfigurationError, "APNs is enabled"):
            NotificationWorkerSettings.from_env({
                "DATABASE_URL": "sqlite:///test.db",
                "COACHAI_APNS_ENABLED": "true",
            })


class NotificationWorkerLoopTests(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def test_continuous_loop_uses_bounded_batches_and_aggregates_metrics(self):
        worker = _FakeWorker(
            {
                "processed": 2,
                "failed": 1,
                "expired": 1,
                "push": {
                    "attempted": 2,
                    "accepted": 1,
                    "retrying": 1,
                    "failed": 0,
                    "invalid_tokens": 0,
                    "cancelled": 1,
                    "provider_latency_ms": 12.5,
                },
            },
            {"processed": 1, "failed": 0},
        )
        stop_event = _RecordingStopEvent()
        snapshots = []
        runtime = None

        def collect_and_stop(health, metrics):
            snapshots.append((health, metrics))
            if metrics["cycles_total"] == 2:
                runtime.request_shutdown()

        runtime = NotificationWorkerRuntime(
            worker,
            _settings(),
            stop_event=stop_event,
            utcnow=lambda: self.NOW,
            snapshot_hooks=(collect_and_stop,),
        )
        metrics = runtime.run_forever()

        self.assertEqual(2, len(worker.calls))
        self.assertEqual(
            {"limit": 2, "lease_seconds": 60, "max_attempts": 4},
            worker.calls[0],
        )
        self.assertEqual([0.1], stop_event.waits)
        self.assertEqual(3, metrics["outbox_processed_total"])
        self.assertEqual(1, metrics["outbox_failed_total"])
        self.assertEqual(1, metrics["outbox_expired_total"])
        self.assertEqual(2, metrics["push_attempted_total"])
        self.assertEqual(1, metrics["push_accepted_total"])
        self.assertEqual(1, metrics["push_retrying_total"])
        self.assertEqual(1, metrics["push_cancelled_total"])
        self.assertEqual(12.5, metrics["provider_latency_ms_total"])
        health = runtime.health_snapshot()
        self.assertEqual("stopped", health["status"])
        self.assertFalse(health["live"])
        self.assertFalse(health["ready"])
        self.assertEqual("requested", health["shutdown_reason"])
        self.assertGreaterEqual(len(snapshots), 3)

    def test_worker_failure_uses_error_backoff_and_does_not_log_exception_text(self):
        secret = "postgresql://user:do-not-log@example.invalid/db"
        worker = _FakeWorker(RuntimeError(secret))
        stop_event = _RecordingStopEvent(stop_on_wait=True)
        observed_health = []
        runtime = NotificationWorkerRuntime(
            worker,
            _settings(),
            stop_event=stop_event,
            utcnow=lambda: self.NOW,
            snapshot_hooks=(lambda health, _metrics: observed_health.append(health),),
        )
        with self.assertLogs("coachai.notifications.worker", level="ERROR") as logs:
            metrics = runtime.run_forever()

        self.assertEqual([0.25], stop_event.waits)
        self.assertEqual(1, metrics["cycles_failed_total"])
        self.assertEqual(1, metrics["consecutive_failures"])
        self.assertEqual("degraded", observed_health[0]["status"])
        self.assertEqual("RuntimeError", observed_health[0]["last_error_type"])
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_snapshot_hook_failure_isolated_from_delivery_loop(self):
        worker = _FakeWorker({"processed": 1, "failed": 0})
        runtime = None

        def broken_hook(_health, _metrics):
            raise RuntimeError("exporter secret must not escape")

        def stop_hook(_health, _metrics):
            runtime.request_shutdown()

        runtime = NotificationWorkerRuntime(
            worker,
            _settings(),
            snapshot_hooks=(broken_hook, stop_hook),
        )
        with self.assertLogs("coachai.notifications.worker", level="WARNING") as logs:
            metrics = runtime.run_forever()
        self.assertEqual(1, metrics["cycles_succeeded_total"])
        self.assertNotIn("exporter secret", "\n".join(logs.output))

    def test_shutdown_interrupts_idle_wait_without_starting_another_batch(self):
        called = threading.Event()

        class Worker:
            calls = 0

            def run_once(self, **_kwargs):
                self.calls += 1
                called.set()
                return {"processed": 0, "failed": 0}

        worker = Worker()
        runtime = NotificationWorkerRuntime(
            worker,
            _settings(
                poll_interval_seconds=60,
                health_stale_after_seconds=180,
            ),
        )
        thread = threading.Thread(target=runtime.run_forever)
        thread.start()
        self.assertTrue(called.wait(1))
        runtime.request_shutdown("SIGTERM")
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, worker.calls)
        self.assertEqual("SIGTERM", runtime.health_snapshot()["shutdown_reason"])

    def test_signal_handler_requests_shutdown_and_handlers_are_restored(self):
        runtime = NotificationWorkerRuntime(_FakeWorker(), _settings())
        registrations = []

        def record_signal(signum, handler):
            registrations.append((int(signum), handler))

        with patch("notifications.worker_runtime.signal.getsignal", return_value="previous"), patch(
            "notifications.worker_runtime.signal.signal", side_effect=record_signal
        ):
            runtime.install_signal_handlers()
            installed = [entry for entry in registrations if callable(entry[1])]
            self.assertTrue(installed)
            installed[0][1](installed[0][0], None)
            self.assertTrue(runtime._stop_event.is_set())
            runtime.restore_signal_handlers()

        restored = [entry for entry in registrations if entry[1] == "previous"]
        self.assertEqual(len(installed), len(restored))


class NotificationWorkerProcessIntegrationTests(NotificationTestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def test_runtime_processes_an_event_once_without_real_push_providers(self):
        event = DomainEvent.create(
            user_id="u1",
            event_type=NotificationEventType.TASK_UNPLACED,
            payload={"task_title": "Worker runtime"},
            idempotency_key="worker-runtime-once",
            occurred_at=self.NOW.replace(tzinfo=None),
        )
        self.repo.enqueue_event(event)
        self.db.commit()

        with patch("notifications.service.providers_from_environment", return_value={}):
            worker = NotificationWorker(self.db, worker_id="runtime-integration")
        runtime = None

        def stop_after_cycle(_health, metrics):
            if metrics["cycles_total"] == 1:
                runtime.request_shutdown()

        runtime = NotificationWorkerRuntime(
            worker,
            _settings(worker_id="runtime-integration"),
            utcnow=lambda: self.NOW,
            snapshot_hooks=(stop_after_cycle,),
        )
        metrics = runtime.run_forever()

        self.assertEqual(1, metrics["outbox_processed_total"])
        self.assertEqual(1, len(self.repo.list_notifications("u1")))
        self.assertEqual(
            {
                "processed": 0,
                "failed": 0,
                "queue_lag_seconds": {"outbox": 0.0, "push": 0.0},
            },
            worker.run_once(now_utc=self.NOW.replace(tzinfo=None)),
        )


if __name__ == "__main__":
    unittest.main()
