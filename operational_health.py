"""Liveness/readiness primitives without an HTTP framework dependency."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import threading
import time
from typing import Any

from observability import redact
from operational_metrics import BackendMetrics


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    status: HealthStatus
    summary: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "details": redact(dict(self.details)),
        }


HealthCheck = Callable[[], HealthCheckResult]


class HealthRegistry:
    """Collect named checks and provide separate liveness/readiness views."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._live: dict[str, HealthCheck] = {}
        self._ready: dict[str, HealthCheck] = {}

    def register(self, name: str, check: HealthCheck, *, readiness: bool = True) -> None:
        if not name or len(name) > 64 or not name.replace("_", "").isalnum():
            raise ValueError("Health check names must be short alphanumeric identifiers.")
        if not callable(check):
            raise TypeError("Health check must be callable.")
        with self._lock:
            target = self._ready if readiness else self._live
            target[name] = check

    @staticmethod
    def _evaluate(checks: Mapping[str, HealthCheck]) -> dict[str, Any]:
        results: dict[str, dict[str, Any]] = {}
        overall = HealthStatus.HEALTHY
        for name, check in tuple(checks.items()):
            try:
                result = check()
                if not isinstance(result, HealthCheckResult):
                    raise TypeError("check returned an invalid result")
            except Exception as exc:
                result = HealthCheckResult(
                    HealthStatus.UNHEALTHY,
                    "Health check failed.",
                    {"exception_type": type(exc).__name__},
                )
            results[name] = result.public()
            if result.status == HealthStatus.UNHEALTHY:
                overall = HealthStatus.UNHEALTHY
            elif result.status == HealthStatus.DEGRADED and overall == HealthStatus.HEALTHY:
                overall = HealthStatus.DEGRADED
        return {
            "status": overall.value,
            "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checks": results,
        }

    def liveness(self) -> dict[str, Any]:
        with self._lock:
            checks = dict(self._live)
        return self._evaluate(checks)

    def readiness(self) -> dict[str, Any]:
        with self._lock:
            checks = {**self._live, **self._ready}
        return self._evaluate(checks)


def database_ping_check(ping: Callable[[], None]) -> HealthCheckResult:
    """Run a caller-supplied bounded DB ping without leaking its exception."""

    try:
        ping()
    except Exception as exc:
        return HealthCheckResult(
            HealthStatus.UNHEALTHY,
            "Database connection failed.",
            {"exception_type": type(exc).__name__},
        )
    return HealthCheckResult(HealthStatus.HEALTHY, "Database connection succeeded.")


def database_pool_snapshot(engine: Any, metrics: BackendMetrics | None = None) -> dict[str, int]:
    """Read SQLAlchemy pool counters using only public pool methods."""

    pool = engine.pool
    values: dict[str, int] = {}
    for name, method_name in {
        "size": "size",
        "checked_out": "checkedout",
        "checked_in": "checkedin",
        "overflow": "overflow",
    }.items():
        method = getattr(pool, method_name, None)
        try:
            values[name] = max(0, int(method())) if callable(method) else 0
        except (TypeError, ValueError, NotImplementedError):
            values[name] = 0
    if metrics is not None:
        metrics.db_pool(
            size=values["size"],
            checked_out=values["checked_out"],
            overflow=values["overflow"],
        )
    return values


class WorkerHeartbeat:
    """Process-local health state exportable by a supervisor or sidecar."""

    def __init__(
        self,
        worker_name: str,
        *,
        stale_after_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not worker_name or stale_after_seconds <= 0:
            raise ValueError("worker_name and a positive stale threshold are required.")
        self.worker_name = worker_name
        self.stale_after_seconds = float(stale_after_seconds)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._started_at = monotonic()
        self._last_success: float | None = None
        self._last_error_type: str | None = None
        self._consecutive_failures = 0
        self._stopped = False

    def success(self) -> None:
        with self._lock:
            self._last_success = self._monotonic()
            self._last_error_type = None
            self._consecutive_failures = 0

    def failure(self, error: BaseException) -> None:
        with self._lock:
            self._last_error_type = type(error).__name__
            self._consecutive_failures += 1

    def stop(self) -> None:
        with self._lock:
            self._stopped = True

    def snapshot(self) -> dict[str, Any]:
        now = self._monotonic()
        with self._lock:
            reference = self._last_success if self._last_success is not None else self._started_at
            lag = max(0.0, now - reference)
            if self._stopped:
                status = HealthStatus.UNHEALTHY
            elif lag > self.stale_after_seconds:
                status = HealthStatus.UNHEALTHY
            elif self._consecutive_failures:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
            return {
                "worker": self.worker_name,
                "status": status.value,
                "ready": status == HealthStatus.HEALTHY and self._last_success is not None,
                "lag_seconds": lag,
                "consecutive_failures": self._consecutive_failures,
                "last_error_type": self._last_error_type,
            }
