"""Bounded retry, failure classification and circuit-breaker contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import random
import threading
import time
from typing import Any, TypeVar

from backend_errors import ExternalProviderFailure
from operational_metrics import BackendMetrics


T = TypeVar("T")


class FailureClass(StrEnum):
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    AUTHENTICATION = "authentication"
    PERMANENT = "permanent"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


def classify_external_failure(error: BaseException) -> FailureClass:
    """Classify common provider errors without retaining provider payloads."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if status is None and response is not None:
            status = getattr(
                response, "status_code", getattr(response, "status", None)
            )
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        if status == 429:
            return FailureClass.RATE_LIMITED
        if status in {408, 425} or (status is not None and 500 <= status <= 599):
            return FailureClass.TRANSIENT
        if status in {401, 403}:
            return FailureClass.AUTHENTICATION
        if status is not None and 400 <= status <= 499:
            return FailureClass.PERMANENT
        if isinstance(current, (TimeoutError, ConnectionError)):
            return FailureClass.TRANSIENT
        if isinstance(current, (KeyError, ValueError)):
            return FailureClass.CONFIGURATION
        current = current.__cause__ or current.__context__
    return FailureClass.UNKNOWN


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10.")
        if self.base_delay_seconds <= 0 or self.max_delay_seconds <= 0:
            raise ValueError("Retry delays must be positive.")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base delay cannot exceed maximum delay.")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1.")

    def delay(self, failed_attempt: int, *, random_value: float | None = None) -> float:
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, failed_attempt - 1)))
        sample = random.random() if random_value is None else max(0.0, min(1.0, random_value))
        jitter = raw * self.jitter_ratio * ((sample * 2) - 1)
        return max(0.0, min(self.max_delay_seconds, raw + jitter))


@dataclass(frozen=True, slots=True)
class ExternalCallPolicy:
    timeout_seconds: float = 20.0
    retry: RetryPolicy = RetryPolicy()

    def __post_init__(self) -> None:
        if not 0.1 <= self.timeout_seconds <= 120:
            raise ValueError("External timeouts must be between 0.1 and 120 seconds.")


class CircuitOpenError(ExternalProviderFailure):
    def __init__(self) -> None:
        super().__init__("The external provider is temporarily unavailable.")


class CircuitBreaker:
    """Thread-safe closed/open/half-open circuit breaker."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or recovery_seconds <= 0:
            raise ValueError("Circuit thresholds must be positive.")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if self._monotonic() - self._opened_at < self.recovery_seconds:
                raise CircuitOpenError()
            if self._probe_in_flight:
                raise CircuitOpenError()
            self._probe_in_flight = True

    def succeeded(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def failed(self) -> None:
        with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._monotonic()


def call_with_resilience(
    operation: Callable[[float], T],
    *,
    component: str,
    policy: ExternalCallPolicy,
    circuit: CircuitBreaker | None = None,
    metrics: BackendMetrics | None = None,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    """Call ``operation(timeout_seconds)`` with bounded safe retries.

    The timeout is passed explicitly on every attempt.  The primitive cannot
    retroactively make an unbounded provider SDK safe; adapters must honour the
    supplied timeout.
    """

    last_failure: FailureClass = FailureClass.UNKNOWN
    for attempt in range(1, policy.retry.max_attempts + 1):
        if circuit is not None:
            circuit.before_call()
        try:
            result = operation(policy.timeout_seconds)
        except Exception as exc:
            failure = classify_external_failure(exc)
            last_failure = failure
            retryable = failure in {
                FailureClass.RATE_LIMITED,
                FailureClass.TRANSIENT,
                FailureClass.UNKNOWN,
            }
            if circuit is not None:
                circuit.failed()
            if not retryable or attempt >= policy.retry.max_attempts:
                raise ExternalProviderFailure(
                    f"{component} operation failed ({failure.value})."
                ) from exc
            if metrics is not None:
                metrics.retry(component, failure.value)
            sleep(policy.retry.delay(attempt, random_value=random_value()))
            continue
        if circuit is not None:
            circuit.succeeded()
        return result
    raise ExternalProviderFailure(f"{component} operation failed ({last_failure.value}).")
