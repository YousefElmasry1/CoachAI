"""Standalone, retry-safe maintenance worker for per-user daily jobs."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import signal
import threading
import time
from typing import Callable

from backend_config import BackendSettings
from mobile_repository import MobileRepository, public_task
from operational_health import WorkerHeartbeat
from operational_metrics import BackendMetrics
from timezone_utils import user_today
from unit_of_work import DatabaseFactory, UnitOfWork


LOGGER = logging.getLogger("coachai.maintenance.worker")


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    database_url: str
    poll_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "MaintenanceSettings":
        backend = BackendSettings.from_env()
        try:
            poll = float(os.getenv("COACHAI_MAINTENANCE_POLL_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("COACHAI_MAINTENANCE_POLL_SECONDS must be numeric.") from exc
        if not 1 <= poll <= 3600:
            raise ValueError("COACHAI_MAINTENANCE_POLL_SECONDS must be between 1 and 3600.")
        return cls(backend.database_url, poll)


def _close_user_day(db, user_id: str) -> dict:
    """Run one local-day cleanup and publish resulting task/plan changes."""
    repo = MobileRepository(db)
    local_date = user_today(db, user_id)

    def mutation() -> dict:
        stale = db.fetch_all(
            """SELECT t.task_id,t.plan_id FROM tasks t
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? AND p.plan_date<?
                 AND t.status IN ('pending','in_progress')""",
            (user_id, local_date.isoformat()),
        )
        closed = db.close_out_stale_tasks(user_id)
        revision = repo.current_revision(user_id)
        for item in stale:
            task = db.get_task(int(item["task_id"]), user_id)
            if task is not None:
                revision = repo.record_change(
                    user_id=user_id, entity_type="task",
                    entity_id=int(item["task_id"]), change_type="upsert",
                    entity_version=int(task.get("version") or 0),
                    payload=public_task(task),
                )
        for plan_id in sorted({int(item["plan_id"]) for item in stale}):
            revision = repo.record_plan_bundle(user_id=user_id, plan_id=plan_id)
        return {
            "local_date": local_date.isoformat(),
            "closed_tasks": int(closed),
            "sync_revision": revision,
        }

    result, replayed = repo.execute_idempotent(
        user_id=user_id,
        operation="maintenance.close_stale_tasks",
        idempotency_key=local_date.isoformat(),
        request_payload={"local_date": local_date.isoformat()},
        mutation=mutation,
    )
    result["idempotent_replay"] = replayed
    return result


def run_daily_stale_task_cleanup(factory: DatabaseFactory | None = None) -> dict:
    """One bounded poll, safe to call repeatedly from a supervisor."""
    owned_factory = factory is None
    active_factory = factory or DatabaseFactory(BackendSettings.from_env().database_url)
    totals = {"users": 0, "closed_tasks": 0, "failures": 0}
    try:
        # A separate UoW per account prevents one corrupt account from holding
        # a transaction or connection across the rest of the population.
        with UnitOfWork(active_factory) as listing:
            user_ids = listing.db.get_all_user_ids()
        for user_id in user_ids:
            totals["users"] += 1
            try:
                with UnitOfWork(active_factory) as uow:
                    result = _close_user_day(uow.db, str(user_id))
                if not result.get("idempotent_replay"):
                    totals["closed_tasks"] += int(result["closed_tasks"])
            except Exception:
                totals["failures"] += 1
                LOGGER.exception("maintenance_account_failed", extra={"user_id": str(user_id)})
        LOGGER.info("maintenance_poll_complete", extra=totals)
        return totals
    finally:
        if owned_factory:
            active_factory.close()


class MaintenanceRuntime:
    def __init__(
        self,
        factory: DatabaseFactory,
        *,
        poll_seconds: float,
        stop_event: threading.Event | None = None,
        wait: Callable[[float], bool] | None = None,
        metrics: BackendMetrics | None = None,
        heartbeat: WorkerHeartbeat | None = None,
    ) -> None:
        self.factory = factory
        self.poll_seconds = poll_seconds
        self.stop_event = stop_event or threading.Event()
        self._wait = wait or self.stop_event.wait
        self.metrics = metrics or BackendMetrics()
        self.heartbeat = heartbeat or WorkerHeartbeat(
            "maintenance", stale_after_seconds=max(60.0, poll_seconds * 3)
        )

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                result = run_daily_stale_task_cleanup(self.factory)
                succeeded = int(result.get("failures") or 0) == 0
                if succeeded:
                    self.heartbeat.success()
                else:
                    self.heartbeat.failure(RuntimeError("account cleanup failed"))
                self.metrics.worker_cycle(
                    "maintenance", succeeded=succeeded,
                    duration_seconds=time.monotonic() - started,
                )
            except Exception as exc:
                self.heartbeat.failure(exc)
                self.metrics.worker_cycle(
                    "maintenance", succeeded=False,
                    duration_seconds=time.monotonic() - started,
                )
                LOGGER.exception("maintenance_poll_failed")
            health = self.heartbeat.snapshot()
            self.metrics.worker_lag(
                "maintenance", float(health["lag_seconds"])
            )
            self._wait(self.poll_seconds)

    def stop(self) -> None:
        self.heartbeat.stop()
        self.stop_event.set()


def main() -> int:
    settings = MaintenanceSettings.from_env()
    from observability import configure_structured_logging

    configure_structured_logging(
        level=os.getenv("COACHAI_MAINTENANCE_LOG_LEVEL", "INFO").upper()
    )
    with DatabaseFactory(settings.database_url) as factory:
        runtime = MaintenanceRuntime(factory, poll_seconds=settings.poll_seconds)

        def shutdown(_signum, _frame):
            LOGGER.info("maintenance_shutdown_requested")
            runtime.stop()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        LOGGER.info("maintenance_worker_started")
        runtime.run()
    LOGGER.info("maintenance_worker_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
