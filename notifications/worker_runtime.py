"""Standalone operational runtime for the durable notification worker.

This module intentionally has no web-framework dependency.  Run it as a
separate process with ``python -m notifications``; it must not be embedded in
each API process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
from pathlib import Path
import signal
import socket
import threading
import time
from types import FrameType
from typing import Any, Protocol
import uuid

from .worker import NotificationWorker


LOGGER = logging.getLogger("coachai.notifications.worker")


class WorkerConfigurationError(ValueError):
    """Raised when the standalone worker configuration is unsafe or invalid."""


def _parse_bool(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WorkerConfigurationError(f"{name} must be a boolean.")


def _parse_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise WorkerConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _parse_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerConfigurationError(f"{name} must be a number.") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise WorkerConfigurationError(
            f"{name} must be between {minimum:g} and {maximum:g}."
        )
    return value


def _generated_worker_id() -> str:
    host = socket.gethostname().strip() or "unknown-host"
    # Host, PID and a random suffix are operational identifiers, not secrets.
    return f"notification-{host}-{os.getpid()}-{uuid.uuid4().hex[:12]}"


def _enabled_provider_names(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Validate enabled push-provider settings without loading credentials."""

    enabled: list[str] = []
    if _parse_bool(environ, "COACHAI_FCM_ENABLED"):
        credentials_path = (
            environ.get("COACHAI_FCM_CREDENTIALS")
            or environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or ""
        ).strip()
        # An omitted path is valid when Application Default Credentials are
        # available.  An explicitly named, missing file is always a typo.
        if credentials_path and not Path(credentials_path).is_file():
            raise WorkerConfigurationError(
                "The configured FCM credentials file does not exist."
            )
        enabled.append("fcm")

    if _parse_bool(environ, "COACHAI_APNS_ENABLED"):
        environment = environ.get("COACHAI_APNS_ENVIRONMENT", "sandbox").strip().lower()
        if environment not in {"sandbox", "production"}:
            raise WorkerConfigurationError(
                "COACHAI_APNS_ENVIRONMENT must be sandbox or production."
            )
        required = (
            "COACHAI_APNS_KEY_ID",
            "COACHAI_APNS_TEAM_ID",
            "COACHAI_APNS_BUNDLE_ID",
            "COACHAI_APNS_PRIVATE_KEY",
        )
        missing = [name for name in required if not environ.get(name, "").strip()]
        if missing:
            raise WorkerConfigurationError(
                "APNs is enabled but required configuration is missing: "
                + ", ".join(missing)
            )
        if not Path(environ["COACHAI_APNS_PRIVATE_KEY"].strip()).is_file():
            raise WorkerConfigurationError(
                "The configured APNs private-key file does not exist."
            )
        enabled.append("apns")
    return tuple(enabled)


@dataclass(frozen=True, slots=True)
class NotificationWorkerSettings:
    """Validated settings for one standalone worker process."""

    database_url: str
    worker_id: str
    poll_interval_seconds: float = 1.0
    error_backoff_seconds: float = 5.0
    batch_size: int = 20
    lease_seconds: int = 300
    max_attempts: int = 5
    push_timeout_seconds: float = 10.0
    lease_safety_margin_seconds: int = 30
    health_stale_after_seconds: float = 300.0
    log_level: str = "INFO"
    enabled_push_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.database_url, str) or not self.database_url.strip():
            raise WorkerConfigurationError("DATABASE_URL is required.")
        if not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise WorkerConfigurationError("COACHAI_NOTIFICATION_WORKER_ID cannot be empty.")
        if len(self.worker_id) > 160 or any(char.isspace() for char in self.worker_id):
            raise WorkerConfigurationError(
                "COACHAI_NOTIFICATION_WORKER_ID must be at most 160 characters without whitespace."
            )
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise WorkerConfigurationError("poll_interval_seconds must be positive and finite.")
        if not math.isfinite(self.error_backoff_seconds) or self.error_backoff_seconds <= 0:
            raise WorkerConfigurationError("error_backoff_seconds must be positive and finite.")
        if not isinstance(self.batch_size, int) or isinstance(self.batch_size, bool) or self.batch_size < 1:
            raise WorkerConfigurationError("batch_size must be a positive integer.")
        if not isinstance(self.lease_seconds, int) or isinstance(self.lease_seconds, bool) or self.lease_seconds < 1:
            raise WorkerConfigurationError("lease_seconds must be a positive integer.")
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise WorkerConfigurationError("max_attempts must be a positive integer.")
        if not math.isfinite(self.push_timeout_seconds) or self.push_timeout_seconds <= 0:
            raise WorkerConfigurationError("push_timeout_seconds must be positive and finite.")
        if (
            not isinstance(self.lease_safety_margin_seconds, int)
            or isinstance(self.lease_safety_margin_seconds, bool)
            or self.lease_safety_margin_seconds < 1
        ):
            raise WorkerConfigurationError(
                "lease_safety_margin_seconds must be a positive integer."
            )
        minimum_safe_lease = (
            self.batch_size * self.push_timeout_seconds
            + self.lease_safety_margin_seconds
        )
        if self.lease_seconds < minimum_safe_lease:
            raise WorkerConfigurationError(
                "COACHAI_NOTIFICATION_LEASE_SECONDS is too short for the configured "
                "batch size and push timeout."
            )
        minimum_health_window = max(
            self.poll_interval_seconds * 2,
            minimum_safe_lease,
        )
        if (
            not math.isfinite(self.health_stale_after_seconds)
            or self.health_stale_after_seconds < minimum_health_window
        ):
            raise WorkerConfigurationError(
                "health_stale_after_seconds must cover the configured batch timeout "
                "and at least two polling intervals."
            )
        if self.log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise WorkerConfigurationError("COACHAI_NOTIFICATION_LOG_LEVEL is invalid.")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "NotificationWorkerSettings":
        source = os.environ if environ is None else environ
        database_url = source.get("DATABASE_URL", "").strip()
        worker_id = (
            source.get("COACHAI_NOTIFICATION_WORKER_ID", "").strip()
            or _generated_worker_id()
        )
        poll_interval = _parse_float(
            source,
            "COACHAI_NOTIFICATION_POLL_SECONDS",
            1.0,
            minimum=0.05,
            maximum=300.0,
        )
        error_backoff = _parse_float(
            source,
            "COACHAI_NOTIFICATION_ERROR_BACKOFF_SECONDS",
            5.0,
            minimum=0.05,
            maximum=3600.0,
        )
        batch_size = _parse_int(
            source,
            "COACHAI_NOTIFICATION_BATCH_SIZE",
            20,
            minimum=1,
            maximum=1000,
        )
        lease_seconds = _parse_int(
            source,
            "COACHAI_NOTIFICATION_LEASE_SECONDS",
            300,
            minimum=1,
            maximum=86400,
        )
        max_attempts = _parse_int(
            source,
            "COACHAI_NOTIFICATION_MAX_ATTEMPTS",
            5,
            minimum=1,
            maximum=100,
        )
        push_timeout = _parse_float(
            source,
            "COACHAI_PUSH_TIMEOUT_SECONDS",
            10.0,
            minimum=0.1,
            maximum=120.0,
        )
        safety_margin = _parse_int(
            source,
            "COACHAI_NOTIFICATION_LEASE_SAFETY_MARGIN_SECONDS",
            30,
            minimum=1,
            maximum=3600,
        )
        stale_after = _parse_float(
            source,
            "COACHAI_NOTIFICATION_HEALTH_STALE_SECONDS",
            300.0,
            minimum=0.1,
            maximum=86400.0,
        )
        log_level = source.get("COACHAI_NOTIFICATION_LOG_LEVEL", "INFO").strip().upper()
        return cls(
            database_url=database_url,
            worker_id=worker_id,
            poll_interval_seconds=poll_interval,
            error_backoff_seconds=error_backoff,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            push_timeout_seconds=push_timeout,
            lease_safety_margin_seconds=safety_margin,
            health_stale_after_seconds=stale_after,
            log_level=log_level,
            enabled_push_providers=_enabled_provider_names(source),
        )


class NotificationBatchWorker(Protocol):
    """Narrow protocol used by the operational loop and deterministic tests."""

    def run_once(
        self,
        *,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]: ...


class PooledNotificationBatchWorker:
    """Acquire one short-lived pooled connection for each bounded cycle."""

    def __init__(self, factory, *, worker_id: str, push_providers=None) -> None:
        self.factory = factory
        self.worker_id = worker_id
        if push_providers is None:
            from .providers import providers_from_environment

            push_providers = providers_from_environment()
        self.push_providers = push_providers

    def run_once(
        self,
        *,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        from unit_of_work import UnitOfWork

        with UnitOfWork(self.factory) as uow:
            return NotificationWorker(
                uow.db,
                worker_id=self.worker_id,
                push_providers=self.push_providers,
            ).run_once(
                limit=limit,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
            )


SnapshotHook = Callable[[dict[str, Any], dict[str, Any]], None]


class NotificationWorkerRuntime:
    """Continuously execute bounded, lease-protected notification batches.

    Shutdown is cooperative: the active bounded batch is allowed to finish,
    then no new jobs are claimed.  If the process is killed during a batch,
    the repository's stale-lease recovery makes those jobs claimable later.
    """

    def __init__(
        self,
        worker: NotificationBatchWorker,
        settings: NotificationWorkerSettings,
        *,
        stop_event: threading.Event | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
        snapshot_hooks: tuple[SnapshotHook, ...] = (),
    ) -> None:
        self.worker = worker
        self.settings = settings
        self._stop_event = stop_event or threading.Event()
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._snapshot_hooks = list(snapshot_hooks)
        self._lock = threading.RLock()
        self._signal_handlers: dict[int, Any] = {}
        self._state: dict[str, Any] = {
            "started": False,
            "stopping": False,
            "stopped": False,
            "shutdown_reason": None,
            "started_at": None,
            "last_poll_started_at": None,
            "last_poll_completed_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_type": None,
            "last_cycle_completed_monotonic": None,
            "started_monotonic": None,
        }
        self._metrics: dict[str, int | float] = {
            "cycles_total": 0,
            "cycles_succeeded_total": 0,
            "cycles_failed_total": 0,
            "consecutive_failures": 0,
            "outbox_processed_total": 0,
            "outbox_failed_total": 0,
            "outbox_expired_total": 0,
            "push_attempted_total": 0,
            "push_accepted_total": 0,
            "push_retrying_total": 0,
            "push_failed_total": 0,
            "push_invalid_tokens_total": 0,
            "push_cancelled_total": 0,
            "provider_latency_ms_total": 0.0,
            "outbox_lag_seconds": 0.0,
            "push_lag_seconds": 0.0,
            "cycle_duration_ms_total": 0.0,
            "last_cycle_duration_ms": 0.0,
        }

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _counter(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _duration(value: Any) -> float:
        try:
            parsed = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, parsed) if math.isfinite(parsed) else 0.0

    def add_snapshot_hook(self, hook: SnapshotHook) -> None:
        """Register a non-blocking observer called after each cycle and stop."""
        if not callable(hook):
            raise TypeError("snapshot hook must be callable")
        with self._lock:
            self._snapshot_hooks.append(hook)

    def request_shutdown(self, reason: str = "requested") -> None:
        """Request graceful shutdown and wake an idle polling wait."""
        safe_reason = reason if reason in {"requested", "SIGINT", "SIGTERM"} else "requested"
        with self._lock:
            if not self._state["stopped"]:
                self._state["stopping"] = True
                self._state["shutdown_reason"] = safe_reason
        self._stop_event.set()

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = "requested"
        self.request_shutdown(name)

    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers; call from the process main thread."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("signal handlers must be installed from the main thread")
        if self._signal_handlers:
            return
        signals = {signal.SIGINT}
        if hasattr(signal, "SIGTERM"):
            signals.add(signal.SIGTERM)
        for signum in signals:
            self._signal_handlers[int(signum)] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def restore_signal_handlers(self) -> None:
        """Restore process handlers installed by :meth:`install_signal_handlers`."""
        if not self._signal_handlers:
            return
        for signum, previous in tuple(self._signal_handlers.items()):
            signal.signal(signum, previous)
        self._signal_handlers.clear()

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return a secret-free copy suitable for a metrics exporter."""
        with self._lock:
            return {
                "worker_id": self.settings.worker_id,
                **self._metrics,
            }

    def health_snapshot(self) -> dict[str, Any]:
        """Return liveness/readiness state without accessing the database."""
        now = self._monotonic()
        with self._lock:
            state = dict(self._state)
            reference = (
                state["last_cycle_completed_monotonic"]
                if state["last_cycle_completed_monotonic"] is not None
                else state["started_monotonic"]
            )
            stale = bool(
                state["started"]
                and not state["stopped"]
                and reference is not None
                and now - float(reference) > self.settings.health_stale_after_seconds
            )
            if state["stopped"]:
                status = "stopped"
            elif state["stopping"]:
                status = "stopping"
            elif not state["started"]:
                status = "starting"
            elif self._metrics["consecutive_failures"] or stale:
                status = "degraded"
            else:
                status = "running"
            ready = bool(
                status == "running"
                and state["last_success_at"] is not None
            )
            return {
                "worker_id": self.settings.worker_id,
                "status": status,
                "live": not state["stopped"],
                "ready": ready,
                "stale": stale,
                "started_at": state["started_at"],
                "last_poll_started_at": state["last_poll_started_at"],
                "last_poll_completed_at": state["last_poll_completed_at"],
                "last_success_at": state["last_success_at"],
                "last_error_at": state["last_error_at"],
                "last_error_type": state["last_error_type"],
                "consecutive_failures": self._metrics["consecutive_failures"],
                "shutdown_reason": state["shutdown_reason"],
                "enabled_push_providers": list(self.settings.enabled_push_providers),
            }

    def _emit_snapshots(self) -> None:
        with self._lock:
            hooks = tuple(self._snapshot_hooks)
        if not hooks:
            return
        health = self.health_snapshot()
        metrics = self.metrics_snapshot()
        for hook in hooks:
            try:
                hook(dict(health), dict(metrics))
            except Exception as exc:
                # Hook failure must never interrupt notification delivery.  Do
                # not include exception text, which may contain exporter data.
                LOGGER.warning(
                    "notification_worker_snapshot_hook_failed error_type=%s",
                    type(exc).__name__,
                )

    def _record_success(
        self,
        result: Mapping[str, Any],
        *,
        completed_at: datetime,
        completed_monotonic: float,
        duration_ms: float,
    ) -> None:
        push = result.get("push")
        push_summary = push if isinstance(push, Mapping) else {}
        lag = result.get("queue_lag_seconds")
        lag_summary = lag if isinstance(lag, Mapping) else {}
        with self._lock:
            self._metrics["cycles_total"] += 1
            self._metrics["cycles_succeeded_total"] += 1
            self._metrics["consecutive_failures"] = 0
            self._metrics["outbox_processed_total"] += self._counter(result.get("processed"))
            self._metrics["outbox_failed_total"] += self._counter(result.get("failed"))
            self._metrics["outbox_expired_total"] += self._counter(result.get("expired"))
            for source, target in (
                ("attempted", "push_attempted_total"),
                ("accepted", "push_accepted_total"),
                ("retrying", "push_retrying_total"),
                ("failed", "push_failed_total"),
                ("invalid_tokens", "push_invalid_tokens_total"),
                ("cancelled", "push_cancelled_total"),
            ):
                self._metrics[target] += self._counter(push_summary.get(source))
            self._metrics["provider_latency_ms_total"] += self._duration(
                push_summary.get("provider_latency_ms")
            )
            self._metrics["outbox_lag_seconds"] = self._duration(
                lag_summary.get("outbox")
            )
            self._metrics["push_lag_seconds"] = self._duration(
                lag_summary.get("push")
            )
            self._metrics["cycle_duration_ms_total"] += duration_ms
            self._metrics["last_cycle_duration_ms"] = duration_ms
            completed_iso = self._iso(completed_at)
            self._state["last_poll_completed_at"] = completed_iso
            self._state["last_success_at"] = completed_iso
            self._state["last_error_type"] = None
            self._state["last_cycle_completed_monotonic"] = completed_monotonic

    def _record_failure(
        self,
        exc: Exception,
        *,
        completed_at: datetime,
        completed_monotonic: float,
        duration_ms: float,
    ) -> None:
        with self._lock:
            self._metrics["cycles_total"] += 1
            self._metrics["cycles_failed_total"] += 1
            self._metrics["consecutive_failures"] += 1
            self._metrics["cycle_duration_ms_total"] += duration_ms
            self._metrics["last_cycle_duration_ms"] = duration_ms
            completed_iso = self._iso(completed_at)
            self._state["last_poll_completed_at"] = completed_iso
            self._state["last_error_at"] = completed_iso
            self._state["last_error_type"] = type(exc).__name__
            self._state["last_cycle_completed_monotonic"] = completed_monotonic

    def run_cycle(self) -> bool:
        """Run one bounded batch and contain transient worker failures."""
        started_monotonic = self._monotonic()
        with self._lock:
            self._state["last_poll_started_at"] = self._iso(self._utcnow())
        try:
            result = self.worker.run_once(
                limit=self.settings.batch_size,
                lease_seconds=self.settings.lease_seconds,
                max_attempts=self.settings.max_attempts,
            )
            if not isinstance(result, Mapping):
                raise TypeError("notification worker result must be a mapping")
        except Exception as exc:
            completed_monotonic = self._monotonic()
            duration_ms = round(max(0.0, completed_monotonic - started_monotonic) * 1000, 3)
            self._record_failure(
                exc,
                completed_at=self._utcnow(),
                completed_monotonic=completed_monotonic,
                duration_ms=duration_ms,
            )
            LOGGER.error(
                "notification_worker_cycle_failed worker_id=%s error_type=%s",
                self.settings.worker_id,
                type(exc).__name__,
            )
            self._emit_snapshots()
            return False

        completed_monotonic = self._monotonic()
        duration_ms = round(max(0.0, completed_monotonic - started_monotonic) * 1000, 3)
        self._record_success(
            result,
            completed_at=self._utcnow(),
            completed_monotonic=completed_monotonic,
            duration_ms=duration_ms,
        )
        work_count = sum(
            self._counter(result.get(name))
            for name in ("processed", "failed", "expired")
        )
        push = result.get("push")
        if isinstance(push, Mapping):
            work_count += self._counter(push.get("attempted"))
            work_count += self._counter(push.get("cancelled"))
        if work_count:
            LOGGER.info(
                "notification_worker_cycle_completed worker_id=%s work_count=%s duration_ms=%.3f",
                self.settings.worker_id,
                work_count,
                duration_ms,
            )
        self._emit_snapshots()
        return True

    def run_forever(self) -> dict[str, Any]:
        """Poll until graceful shutdown is requested, then return metrics."""
        with self._lock:
            if self._state["started"] and not self._state["stopped"]:
                raise RuntimeError("notification worker runtime is already running")
            if self._state["stopped"]:
                raise RuntimeError("notification worker runtime cannot be restarted")
            now = self._utcnow()
            self._state["started"] = True
            self._state["started_at"] = self._iso(now)
            self._state["started_monotonic"] = self._monotonic()

        LOGGER.info(
            "notification_worker_started worker_id=%s batch_size=%s lease_seconds=%s poll_seconds=%s providers=%s",
            self.settings.worker_id,
            self.settings.batch_size,
            self.settings.lease_seconds,
            self.settings.poll_interval_seconds,
            ",".join(self.settings.enabled_push_providers) or "none",
        )
        try:
            while not self._stop_event.is_set():
                succeeded = self.run_cycle()
                if self._stop_event.is_set():
                    break
                delay = (
                    self.settings.poll_interval_seconds
                    if succeeded
                    else self.settings.error_backoff_seconds
                )
                if self._stop_event.wait(delay):
                    break
        finally:
            with self._lock:
                self._state["stopping"] = False
                self._state["stopped"] = True
                if self._state["shutdown_reason"] is None:
                    self._state["shutdown_reason"] = "requested"
            self._emit_snapshots()
            LOGGER.info(
                "notification_worker_stopped worker_id=%s reason=%s cycles=%s",
                self.settings.worker_id,
                self._state["shutdown_reason"],
                self._metrics["cycles_total"],
            )
        return self.metrics_snapshot()


def main() -> int:
    """Run the notification worker as its own supervised process."""
    factory = None
    runtime: NotificationWorkerRuntime | None = None
    try:
        settings = NotificationWorkerSettings.from_env()
        from observability import configure_structured_logging

        configure_structured_logging(level=settings.log_level)
        # Imported here so configuration failures do not open a pool.
        from unit_of_work import DatabaseFactory

        factory = DatabaseFactory(settings.database_url)
        worker = PooledNotificationBatchWorker(
            factory, worker_id=settings.worker_id,
        )
        runtime = NotificationWorkerRuntime(worker, settings)
        runtime.install_signal_handlers()
        runtime.run_forever()
        return 0
    except WorkerConfigurationError as exc:
        from observability import configure_structured_logging

        configure_structured_logging(level="ERROR")
        LOGGER.error(
            "notification_worker_configuration_error error_type=%s",
            type(exc).__name__,
        )
        return 2
    except KeyboardInterrupt:
        if runtime is not None:
            runtime.request_shutdown("SIGINT")
        return 0
    except Exception as exc:
        # Never log exception text: database/provider exceptions can embed a
        # connection URL, token, file path or provider response.
        LOGGER.error(
            "notification_worker_fatal error_type=%s",
            type(exc).__name__,
        )
        return 1
    finally:
        if runtime is not None:
            runtime.restore_signal_handlers()
        if factory is not None:
            factory.close()


if __name__ == "__main__":  # pragma: no cover - exercised by process runner
    raise SystemExit(main())
