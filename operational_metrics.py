"""Small metrics facade usable by API processes and standalone workers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import re
import threading
from typing import Mapping, Protocol


_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_LABEL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_LABELS = {"token", "device_token", "authorization", "email", "password", "secret"}


def _normalise_labels(labels: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
    clean: list[tuple[str, str]] = []
    for raw_name, raw_value in (labels or {}).items():
        name = str(raw_name)
        if not _LABEL_NAME.fullmatch(name):
            raise ValueError(f"Invalid metric label name: {name}")
        if name in _SENSITIVE_LABELS or name.endswith(("_token", "_secret", "_password")):
            raise ValueError(f"Sensitive metric label is forbidden: {name}")
        value = str(raw_value)
        if len(value) > 64:
            raise ValueError(f"Metric label {name} exceeds 64 characters.")
        clean.append((name, value))
    return tuple(sorted(clean))


def _number(value: int | float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Metric values must be finite.")
    return result


class MetricSink(Protocol):
    """Exporter-neutral metric contract (Prometheus/OTel adapters plug in here)."""

    def increment(
        self, name: str, value: int | float = 1, *, labels: Mapping[str, object] | None = None
    ) -> None: ...

    def observe(
        self, name: str, value: int | float, *, labels: Mapping[str, object] | None = None
    ) -> None: ...

    def gauge(
        self, name: str, value: int | float, *, labels: Mapping[str, object] | None = None
    ) -> None: ...


class NullMetricSink:
    def increment(self, name: str, value: int | float = 1, *, labels=None) -> None:
        return None

    def observe(self, name: str, value: int | float, *, labels=None) -> None:
        return None

    def gauge(self, name: str, value: int | float, *, labels=None) -> None:
        return None


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    count: int
    total: float
    maximum: float


class InMemoryMetricSink:
    """Thread-safe test/dev sink; production should attach an exporter adapter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._histograms: dict[tuple[str, tuple], list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels) -> tuple[str, tuple[tuple[str, str], ...]]:
        if not _METRIC_NAME.fullmatch(name):
            raise ValueError(f"Invalid metric name: {name}")
        return name, _normalise_labels(labels)

    def increment(self, name, value=1, *, labels=None) -> None:
        value = _number(value)
        if value < 0:
            raise ValueError("Counters cannot be decremented.")
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def observe(self, name, value, *, labels=None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._histograms[key].append(_number(value))

    def gauge(self, name, value, *, labels=None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = _number(value)

    def snapshot(self) -> dict[str, dict]:
        """Return a deterministic, JSON-safe snapshot for tests/health sidecars."""

        def label_key(labels: tuple[tuple[str, str], ...]) -> str:
            return ",".join(f"{name}={value}" for name, value in labels) or "_"

        with self._lock:
            counters: dict[str, dict[str, float]] = defaultdict(dict)
            gauges: dict[str, dict[str, float]] = defaultdict(dict)
            histograms: dict[str, dict[str, dict[str, float | int]]] = defaultdict(dict)
            for (name, labels), value in self._counters.items():
                counters[name][label_key(labels)] = value
            for (name, labels), value in self._gauges.items():
                gauges[name][label_key(labels)] = value
            for (name, labels), values in self._histograms.items():
                histograms[name][label_key(labels)] = {
                    "count": len(values),
                    "total": sum(values),
                    "maximum": max(values),
                }
            return {
                "counters": dict(counters),
                "gauges": dict(gauges),
                "histograms": dict(histograms),
            }


class BackendMetrics:
    """Canonical low-cardinality hooks required by CoachAI operations."""

    def __init__(self, sink: MetricSink | None = None) -> None:
        self.sink = sink or NullMetricSink()

    def worker_cycle(self, worker: str, *, succeeded: bool, duration_seconds: float) -> None:
        labels = {"worker": worker, "result": "success" if succeeded else "failure"}
        self.sink.increment("coachai_worker_cycles_total", labels=labels)
        self.sink.observe("coachai_worker_cycle_duration_seconds", duration_seconds, labels={"worker": worker})

    def worker_lag(self, worker: str, lag_seconds: float) -> None:
        self.sink.gauge("coachai_worker_lag_seconds", max(0.0, lag_seconds), labels={"worker": worker})

    def notification_lag(self, channel: str, lag_seconds: float) -> None:
        self.sink.gauge("coachai_notification_oldest_queued_seconds", max(0.0, lag_seconds), labels={"channel": channel})

    def retry(self, component: str, failure_class: str) -> None:
        self.sink.increment("coachai_external_retries_total", labels={"component": component, "failure_class": failure_class})

    def db_pool(self, *, size: int, checked_out: int, overflow: int) -> None:
        for name, value in {
            "size": size,
            "checked_out": checked_out,
            "overflow": overflow,
        }.items():
            self.sink.gauge("coachai_db_pool_connections", value, labels={"state": name})

    def calendar_failure(self, operation: str, failure_class: str) -> None:
        self.sink.increment("coachai_calendar_failures_total", labels={"operation": operation, "failure_class": failure_class})

    def ai_call(
        self,
        provider: str,
        model: str,
        *,
        succeeded: bool,
        duration_seconds: float,
    ) -> None:
        labels = {
            "provider": provider,
            "model": model,
            "result": "success" if succeeded else "failure",
        }
        self.sink.increment("coachai_ai_calls_total", labels=labels)
        self.sink.observe(
            "coachai_ai_call_duration_seconds",
            max(0.0, duration_seconds),
            labels={"provider": provider, "model": model},
        )

    def ai_usage(
        self,
        provider: str,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_microusd: int = 0,
    ) -> None:
        labels = {"provider": provider, "model": model}
        self.sink.increment("coachai_ai_input_tokens_total", max(0, input_tokens), labels=labels)
        self.sink.increment("coachai_ai_output_tokens_total", max(0, output_tokens), labels=labels)
        self.sink.increment("coachai_ai_estimated_cost_microusd_total", max(0, estimated_cost_microusd), labels=labels)
