"""Standalone durable Google Calendar reconciliation worker.

Claims database intents with leases, releases the database connection before
network I/O, then records provider results in a fresh transaction. Run as a
separate supervised process; never embed it in a web or Streamlit process.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import logging
import os
import signal
import socket
import threading
import time
from typing import Any
import uuid

from calendar_security import (
    CalendarOperation,
    CalendarOperationQueue,
    CalendarSyncStateStore,
    EncryptedGoogleTokenStore,
    VersionedTokenCipher,
)
from google_calendar import (
    GoogleCalendarAuthorizationRevoked,
    GoogleCalendarClient,
    GoogleCalendarError,
    GoogleCalendarEventIdGone,
)
from external_resilience import FailureClass, classify_external_failure
from identity import AuthenticatedPrincipal
from mobile_repository import MobileRepository
from operational_health import WorkerHeartbeat
from operational_metrics import BackendMetrics
from timezone_utils import get_user_timezone
from unit_of_work import DatabaseFactory, UnitOfWork


LOGGER = logging.getLogger("coachai.calendar.worker")
SAFE_PROVIDER_ERROR = "Calendar provider operation failed."


@dataclass(frozen=True, slots=True)
class CalendarWorkerSettings:
    database_url: str
    worker_id: str
    poll_seconds: float = 2.0
    batch_size: int = 10
    lease_seconds: int = 300
    max_attempts: int = 5

    @classmethod
    def from_env(cls) -> "CalendarWorkerSettings":
        database_url = (os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            raise ValueError("DATABASE_URL is required.")
        worker_id = (
            os.getenv("COACHAI_CALENDAR_WORKER_ID")
            or f"calendar-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        ).strip()
        try:
            poll = float(os.getenv("COACHAI_CALENDAR_POLL_SECONDS", "2"))
            batch = int(os.getenv("COACHAI_CALENDAR_BATCH_SIZE", "10"))
            lease = int(os.getenv("COACHAI_CALENDAR_LEASE_SECONDS", "300"))
            attempts = int(os.getenv("COACHAI_CALENDAR_MAX_ATTEMPTS", "5"))
        except ValueError as exc:
            raise ValueError("Calendar worker numeric configuration is invalid.") from exc
        if not (0.1 <= poll <= 300 and 1 <= batch <= 100 and 30 <= lease <= 86400):
            raise ValueError("Calendar worker polling, batch, or lease configuration is invalid.")
        if not 1 <= attempts <= 100:
            raise ValueError("Calendar worker attempts must be between 1 and 100.")
        return cls(database_url, worker_id, poll, batch, lease, attempts)


def _principal(db, user_id: str) -> AuthenticatedPrincipal:
    row = db.fetch_one(
        "SELECT user_id,email,display_name,is_disabled FROM users WHERE user_id=?",
        (user_id,),
    )
    if row is None or bool(row.get("is_disabled") or 0):
        raise RuntimeError("Calendar account is unavailable.")
    return AuthenticatedPrincipal(
        str(row["user_id"]), str(row.get("email") or ""),
        str(row.get("display_name") or "CoachAI User"),
        {"source": "calendar_worker"},
    )


def _deterministic_event_id(user_id: str, segment_id: int, *, generation: str = "v1") -> str:
    digest = hashlib.sha256(
        f"{user_id}:{segment_id}:{generation}".encode("utf-8")
    ).digest()
    return "coachai" + base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")[:40]


class CalendarWorker:
    def __init__(
        self,
        factory: DatabaseFactory,
        settings: CalendarWorkerSettings,
        *,
        metrics: BackendMetrics | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.metrics = metrics or BackendMetrics()

    def _load_client(
        self, user_id: str,
    ) -> tuple[GoogleCalendarClient, AuthenticatedPrincipal]:
        with UnitOfWork(self.factory) as uow:
            principal = _principal(uow.db, user_id)
            credentials = EncryptedGoogleTokenStore(
                uow.db, VersionedTokenCipher.from_env()
            ).get(principal)
        if credentials is None:
            raise RuntimeError("Calendar account is not connected.")
        client = GoogleCalendarClient(
            credentials.access_token,
            credentials.refresh_token,
            credentials.token_expiry.replace(tzinfo=None),
            timeout_seconds=float(os.getenv("COACHAI_EXTERNAL_TIMEOUT_SECONDS", "20")),
        )
        # Refresh is network I/O and happens after the read UoW was released.
        refreshed = client.refresh_if_expired()
        if refreshed:
            with UnitOfWork(self.factory) as uow:
                EncryptedGoogleTokenStore(
                    uow.db, VersionedTokenCipher.from_env()
                ).save(
                    principal,
                    access_token=str(refreshed["access_token"]),
                    refresh_token=None,
                    token_expiry=refreshed["token_expiry"],
                    scopes=credentials.scopes,
                )
        return client, principal

    def _complete(self, operation_id: str) -> None:
        with UnitOfWork(self.factory) as uow:
            CalendarOperationQueue(uow.db).mark_completed(
                operation_id, worker_id=self.settings.worker_id,
            )

    def _fail(
        self, operation: CalendarOperation, error: BaseException, *, retryable: bool = True,
    ) -> None:
        with UnitOfWork(self.factory) as uow:
            if isinstance(error, GoogleCalendarAuthorizationRevoked):
                try:
                    EncryptedGoogleTokenStore(
                        uow.db, VersionedTokenCipher.from_env()
                    ).delete(_principal(uow.db, operation.user_id))
                except Exception:
                    LOGGER.warning("calendar_revoked_token_cleanup_failed")
            if operation.operation_type == "sync.range":
                calendar_id = str(operation.payload.get("calendar_id") or "")
                if calendar_id:
                    try:
                        CalendarSyncStateStore(uow.db).mark_error(
                            _principal(uow.db, operation.user_id),
                            calendar_id=calendar_id, error=SAFE_PROVIDER_ERROR,
                        )
                    except Exception:
                        LOGGER.warning("calendar_sync_state_error_update_failed")
            CalendarOperationQueue(uow.db).mark_failed(
                operation.operation_id,
                worker_id=self.settings.worker_id,
                error=SAFE_PROVIDER_ERROR,
                retryable=retryable,
                max_attempts=self.settings.max_attempts,
            )
        LOGGER.warning(
            "calendar_operation_failed",
            extra={
                "operation_id": operation.operation_id,
                "operation_type": operation.operation_type,
                "retryable": retryable,
            },
        )

    def _sync_range(
        self,
        operation: CalendarOperation,
        client: GoogleCalendarClient,
        principal: AuthenticatedPrincipal,
    ) -> None:
        calendar_id = str(operation.payload["calendar_id"])
        range_start = date.fromisoformat(str(operation.payload["range_start"]))
        range_end = date.fromisoformat(str(operation.payload["range_end"]))
        with UnitOfWork(self.factory) as uow:
            timezone_name = get_user_timezone(uow.db, operation.user_id)
            state_store = CalendarSyncStateStore(uow.db, max_range_days=84)
            state = state_store.get(principal, calendar_id=calendar_id)
            if state is None:
                state_store.mark_pending(
                    principal, calendar_id=calendar_id,
                    range_start=range_start, range_end=range_end,
                )
            if state is None or str(state.get("status")) != "running":
                state_store.mark_running(principal, calendar_id=calendar_id)

        events = client.fetch_events_range(
            calendar_id, range_start, range_end, timezone=timezone_name,
        )
        by_date: dict[str, list[str]] = {}
        current = range_start
        while current <= range_end:
            by_date[current.isoformat()] = []
            current = date.fromordinal(current.toordinal() + 1)
        for event in events:
            by_date[event["event_date"]].append(event["google_event_id"])

        with UnitOfWork(self.factory) as uow:
            with uow.transaction():
                for event in events:
                    uow.db.upsert_google_calendar_event(
                        user_id=operation.user_id,
                        google_event_id=event["google_event_id"],
                        title=event["title"],
                        start_time=event["start_time"],
                        end_time=event["end_time"],
                        event_date=event["event_date"],
                        calendar_id=calendar_id,
                    )
                for event_date, event_ids in by_date.items():
                    uow.db.delete_google_events_not_in(
                        operation.user_id, event_date, calendar_id, event_ids,
                    )
                state = CalendarSyncStateStore(uow.db).mark_fresh(
                    principal, calendar_id=calendar_id, sync_token=None,
                )
                MobileRepository(uow.db).record_change(
                    user_id=operation.user_id,
                    entity_type="calendar_sync_state",
                    entity_id=calendar_id,
                    change_type="upsert",
                    entity_version=None,
                    payload={key: state.get(key) for key in (
                        "calendar_id", "range_start", "range_end", "status",
                        "last_success_at", "updated_at",
                    )},
                )
                CalendarOperationQueue(uow.db).mark_completed(
                    operation.operation_id, worker_id=self.settings.worker_id,
                )

    def _load_export_specs(
        self, user_id: str, task_ids: list[int], segment_ids: set[int] | None = None,
    ) -> tuple[str, list[dict]]:
        specs: list[dict] = []
        with UnitOfWork(self.factory) as uow:
            timezone_name = get_user_timezone(uow.db, user_id)
            for task_id in sorted(set(task_ids)):
                task = uow.db.get_task(task_id, user_id)
                if task is None:
                    continue
                plan = uow.db.get_plan_by_id(int(task["plan_id"]), user_id)
                for segment in uow.db.get_task_segments(task_id, user_id):
                    if segment_ids is not None and int(segment["segment_id"]) not in segment_ids:
                        continue
                    if (
                        segment.get("google_event_id")
                        and segment.get("calendar_provenance") != "coachai_created"
                    ):
                        continue
                    specs.append({
                        "segment_id": int(segment["segment_id"]),
                        "task_id": task_id,
                        "title": str(task["title"]),
                        "calendar_id": str(segment.get("google_calendar_id") or "primary"),
                        "event_id": segment.get("google_event_id"),
                        "provenance": str(segment.get("calendar_provenance") or "legacy_unknown"),
                        "sync_status": str(segment.get("calendar_sync_status") or "not_synced"),
                        "start": str(segment["scheduled_start"]),
                        "end": str(segment["scheduled_end"]),
                        "event_date": str(segment.get("effective_date") or plan["plan_date"]),
                    })
        return timezone_name, specs

    def _persist_segment_states(self, user_id: str, states: list[dict]) -> None:
        if not states:
            return
        with UnitOfWork(self.factory) as uow:
            with uow.transaction():
                for state in states:
                    uow.db.update_segment_calendar_state(
                        int(state["segment_id"]), user_id,
                        google_event_id=state.get("event_id"),
                        google_calendar_id=state.get("calendar_id"),
                        calendar_provenance="coachai_created",
                        calendar_sync_status=state["status"],
                        calendar_sync_error=state.get("error") or "",
                    )

    def _export_tasks(
        self,
        operation: CalendarOperation,
        client: GoogleCalendarClient,
        task_ids: list[int],
        *,
        segment_ids: set[int] | None = None,
    ) -> None:
        timezone_name, specs = self._load_export_specs(
            operation.user_id, task_ids, segment_ids,
        )
        persisted: list[dict] = []
        try:
            for spec in specs:
                event_id = str(spec.get("event_id") or _deterministic_event_id(
                    operation.user_id, spec["segment_id"],
                ))
                try:
                    if spec.get("event_id") and spec["sync_status"] not in {
                        "pending_create", "create_error",
                    }:
                        client.update_event(
                            spec["calendar_id"], event_id, spec["title"],
                            spec["start"], spec["end"], spec["event_date"],
                            timezone=timezone_name,
                        )
                    else:
                        event_id = client.create_event(
                            spec["calendar_id"], spec["title"], spec["start"],
                            spec["end"], spec["event_date"], timezone_name,
                            event_id=event_id,
                        )
                except GoogleCalendarEventIdGone:
                    event_id = _deterministic_event_id(
                        operation.user_id, spec["segment_id"], generation="replacement-v1",
                    )
                    event_id = client.create_event(
                        spec["calendar_id"], spec["title"], spec["start"],
                        spec["end"], spec["event_date"], timezone_name,
                        event_id=event_id,
                    )
                persisted.append({
                    "segment_id": spec["segment_id"], "event_id": event_id,
                    "calendar_id": spec["calendar_id"], "status": "synced",
                })
        except Exception:
            persisted.append({
                "segment_id": spec["segment_id"], "event_id": event_id,
                "calendar_id": spec["calendar_id"],
                "status": "update_error" if spec.get("event_id") else "create_error",
                "error": SAFE_PROVIDER_ERROR,
            })
            self._persist_segment_states(operation.user_id, persisted)
            raise
        self._persist_segment_states(operation.user_id, persisted)

    def _schedule_reconciliation(
        self, operation: CalendarOperation, client: GoogleCalendarClient,
    ) -> None:
        plan_ids = [int(value) for value in operation.payload.get("plan_ids", [])]
        task_ids: list[int] = []
        orphaned: set[tuple[str, str]] = set()
        schedule_operation_id = operation.payload.get("schedule_operation_id")
        with UnitOfWork(self.factory) as uow:
            for plan_id in plan_ids:
                task_ids.extend(
                    int(row["task_id"])
                    for row in uow.db.get_tasks_by_plan(plan_id, operation.user_id)
                )
            if schedule_operation_id:
                schedule = uow.db.get_schedule_operation(
                    str(schedule_operation_id), operation.user_id,
                )
                if schedule is not None:
                    before = json.loads(str(schedule["before_state"]))
                    after = json.loads(str(schedule["after_state"]))
                    source, target = (
                        (after, before) if schedule["status"] == "undone"
                        else (before, after)
                    )
                    target_ids = {
                        item.get("google_event_id")
                        for item in target.get("segments", [])
                        if item.get("google_event_id")
                    }
                    orphaned = {
                        (
                            str(item.get("google_calendar_id") or "primary"),
                            str(item["google_event_id"]),
                        )
                        for item in source.get("segments", [])
                        if item.get("calendar_provenance") == "coachai_created"
                        and item.get("google_event_id")
                        and item.get("google_event_id") not in target_ids
                    }
        for calendar_id, event_id in sorted(orphaned):
            client.delete_event(calendar_id, event_id)
        self._export_tasks(operation, client, task_ids)
        if schedule_operation_id:
            with UnitOfWork(self.factory) as uow:
                uow.db.mark_schedule_calendar_reconciliation(
                    str(schedule_operation_id), operation.user_id, None,
                )

    def _process(self, operation: CalendarOperation) -> None:
        client, principal = self._load_client(operation.user_id)
        if operation.operation_type == "sync.range":
            self._sync_range(operation, client, principal)
            return
        if operation.operation_type == "event.delete":
            client.delete_event(
                str(operation.payload.get("calendar_id") or "primary"),
                str(operation.payload["event_id"]),
            )
        elif operation.operation_type == "delete_task_events":
            for item in operation.payload.get("events", []):
                client.delete_event(
                    str(item.get("calendar_id") or "primary"),
                    str(item["google_event_id"]),
                )
        elif operation.operation_type == "event.upsert":
            segment_ids = operation.payload.get("segment_ids")
            self._export_tasks(
                operation, client, [int(operation.payload["task_id"])],
                segment_ids={int(value) for value in segment_ids} if segment_ids else None,
            )
        elif operation.operation_type in {
            "reconcile_schedule", "reconcile_schedule_undo",
        }:
            self._schedule_reconciliation(operation, client)
        elif operation.operation_type == "timezone_reconcile":
            with UnitOfWork(self.factory) as uow:
                rows = uow.db.fetch_all(
                    """SELECT DISTINCT t.task_id FROM tasks t
                       JOIN plans p ON p.plan_id=t.plan_id
                       JOIN task_segments s ON s.task_id=t.task_id
                       WHERE p.user_id=? AND s.calendar_provenance='coachai_created'""",
                    (operation.user_id,),
                )
            self._export_tasks(
                operation, client, [int(row["task_id"]) for row in rows],
            )
        else:
            raise ValueError("Unsupported Calendar operation type.")
        self._complete(operation.operation_id)

    def run_once(self) -> dict[str, int]:
        with UnitOfWork(self.factory) as uow:
            operations = CalendarOperationQueue(uow.db).claim_ready(
                worker_id=self.settings.worker_id,
                limit=self.settings.batch_size,
                stale_after_seconds=self.settings.lease_seconds,
                max_attempts=self.settings.max_attempts,
            )
        result = {"claimed": len(operations), "completed": 0, "failed": 0}
        for operation in operations:
            try:
                self._process(operation)
                result["completed"] += 1
            except ValueError as exc:
                self.metrics.calendar_failure(
                    operation.operation_type, FailureClass.PERMANENT.value
                )
                self._fail(operation, exc, retryable=False)
                result["failed"] += 1
            except Exception as exc:
                failure = (
                    FailureClass.AUTHENTICATION
                    if isinstance(exc, GoogleCalendarAuthorizationRevoked)
                    else classify_external_failure(exc)
                )
                retryable = failure in {
                    FailureClass.RATE_LIMITED,
                    FailureClass.TRANSIENT,
                    FailureClass.UNKNOWN,
                }
                self.metrics.calendar_failure(
                    operation.operation_type, failure.value
                )
                self._fail(operation, exc, retryable=retryable)
                result["failed"] += 1
        return result


class CalendarWorkerRuntime:
    def __init__(
        self,
        worker: CalendarWorker,
        *,
        poll_seconds: float,
        stop_event: threading.Event | None = None,
        metrics: BackendMetrics | None = None,
        heartbeat: WorkerHeartbeat | None = None,
    ) -> None:
        self.worker = worker
        self.poll_seconds = poll_seconds
        self.stop_event = stop_event or threading.Event()
        self.metrics = metrics or worker.metrics
        self.heartbeat = heartbeat or WorkerHeartbeat(
            "calendar", stale_after_seconds=max(30.0, poll_seconds * 3)
        )

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                snapshot = self.worker.run_once()
                self.heartbeat.success()
                self.metrics.worker_cycle(
                    "calendar", succeeded=True,
                    duration_seconds=time.monotonic() - started,
                )
                LOGGER.info("calendar_worker_poll", extra=snapshot)
            except Exception as exc:
                self.heartbeat.failure(exc)
                self.metrics.worker_cycle(
                    "calendar", succeeded=False,
                    duration_seconds=time.monotonic() - started,
                )
                LOGGER.exception("calendar_worker_poll_failed")
            health = self.heartbeat.snapshot()
            self.metrics.worker_lag("calendar", float(health["lag_seconds"]))
            self.stop_event.wait(self.poll_seconds)

    def stop(self) -> None:
        self.heartbeat.stop()
        self.stop_event.set()


def main() -> int:
    settings = CalendarWorkerSettings.from_env()
    from observability import configure_structured_logging

    configure_structured_logging(
        level=os.getenv("COACHAI_CALENDAR_LOG_LEVEL", "INFO").upper()
    )
    with DatabaseFactory(settings.database_url) as factory:
        runtime = CalendarWorkerRuntime(
            CalendarWorker(factory, settings), poll_seconds=settings.poll_seconds,
        )

        def stop(_signum, _frame):
            runtime.stop()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        runtime.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
