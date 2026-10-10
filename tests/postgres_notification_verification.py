"""Disposable PostgreSQL 16 verification for the notification foundation.

This is intentionally outside unittest discovery. It refuses non-loopback or
non-disposable database URLs and is run explicitly by the Patch 1 verification.
"""

from __future__ import annotations

from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from database import Database, OwnershipError
from notifications import (
    DomainEvent,
    NotificationEventType,
    NotificationRepository,
    NotificationService,
    RiskState,
)


EXPECTED_TABLES = {
    "notification_preferences", "notification_type_preferences",
    "notification_devices", "notification_outbox", "notifications",
    "notification_deliveries", "notification_local_schedules",
    "notification_sync_state", "task_notification_state",
}


def main() -> None:
    raw_url = os.environ["COACHAI_TEST_DATABASE_URL"]
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("PostgreSQL verification requires a PostgreSQL URL.")
    if url.host not in {"127.0.0.1", "localhost"} or url.database != "coachai_notifications":
        raise RuntimeError("Refusing to run against a non-disposable PostgreSQL target.")

    migration_engine = create_engine(raw_url, future=True)
    try:
        with migration_engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        migration_engine.dispose()

    db = Database(raw_url)
    try:
        tables = set(inspect(db.engine).get_table_names())
        assert EXPECTED_TABLES <= tables
        version = db.connection.execute(text("SHOW server_version")).scalar_one()

        for user_id in ("pg-u1", "pg-u2"):
            db.execute(
                "INSERT INTO users(user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user_id, f"{user_id}@example.com", user_id, "Africa/Cairo"),
            )
        db.connection.commit()
        plan1 = db.create_plan("pg-u1", date(2026, 10, 8), "postgres test")
        db.create_plan("pg-u2", date(2026, 10, 8), "tenant test")
        task_id = db.add_task(plan1, "pg-u1", "PostgreSQL task", estimated_minutes=60)

        repo = NotificationRepository(db)
        service = NotificationService(db)
        prefs = repo.update_preferences(
            "pg-u1", locale="ar", tone="friendly", default_reminder_lead_minutes=10,
        )
        assert prefs["locale"] == "ar"
        repo.register_device(
            "pg-u1", device_id="pg-phone", platform="android", provider="fcm",
            push_token="disposable-token", is_primary=True,
        )
        try:
            repo.get_device("pg-u2", "pg-phone")
        except OwnershipError:
            pass
        else:
            raise AssertionError("Cross-tenant device access was not rejected.")

        revision = int(db.get_plan_by_id(plan1, "pg-u1")["schedule_revision"])
        operation_id = db.replace_plan_segments(
            plan1, "pg-u1",
            {task_id: [{
                "segment_index": 0, "scheduled_start": "09:00:00",
                "scheduled_end": "10:00:00", "scheduled_date": "2026-10-08",
            }]},
            revision,
        )
        events = repo.list_outbox("pg-u1")
        assert len([row for row in events if row["event_type"] == "SCHEDULE_OPERATION_APPLIED"]) == 1
        assert repo.get_sync_revision("pg-u1") == 2

        runtime = service.reconcile_task_notifications(
            "pg-u1", task_id, now_utc=datetime(2026, 10, 8, 5, 0),
        )
        assert runtime["scheduled"] == 1

        risk_changed = service.transition_deadline_risk("pg-u1", task_id, RiskState.AT_RISK)
        risk_repeated = service.transition_deadline_risk("pg-u1", task_id, RiskState.AT_RISK)
        assert risk_changed is True and risk_repeated is False
        result = service.process_outbox(limit=100, now_utc=datetime(2026, 10, 8, 5, 0))
        assert result["failed"] == 0
        assert repo.list_notifications("pg-u1", include_suppressed=True)
        assert repo.list_local_schedules("pg-u1", status="scheduled")

        claim_event = DomainEvent.create(
            user_id="pg-u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="postgres-atomic-claim", occurred_at=datetime(2026, 10, 8, 5, 0),
        )
        repo.enqueue_event(claim_event)
        db.connection.commit()
        claimed = repo.claim_outbox(now=datetime(2026, 10, 8, 5, 0), worker_id="pg-a")
        assert len([row for row in claimed if row["outbox_id"] == claim_event.event_id]) == 1
        assert repo.claim_outbox(now=datetime(2026, 10, 8, 5, 1), worker_id="pg-b") == []
        repo.mark_outbox_processed(claim_event.event_id, "pg-u1", worker_id="pg-a")
        db.connection.commit()

        # Two independent PostgreSQL connections racing the same event must
        # produce exactly one winner. The conditional UPDATE is the final
        # arbitration even when both workers observed the queued candidate.
        race_event = DomainEvent.create(
            user_id="pg-u1", event_type=NotificationEventType.TASK_UNPLACED,
            idempotency_key="postgres-worker-race", occurred_at=datetime(2026, 10, 8, 6, 0),
        )
        repo.enqueue_event(race_event)
        db.connection.commit()
        barrier = threading.Barrier(2)

        def claim_once(worker_id: str) -> list[str]:
            worker_db = Database(raw_url)
            try:
                barrier.wait(timeout=5)
                claimed_rows = NotificationRepository(worker_db).claim_outbox(
                    limit=1, now=datetime(2026, 10, 8, 6, 0), worker_id=worker_id,
                )
                return [row["outbox_id"] for row in claimed_rows]
            finally:
                worker_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim_once, ("race-a", "race-b")))
        winners = [ids for ids in claims if race_event.event_id in ids]
        assert len(winners) == 1

        raced = db.fetch_one(
            "SELECT * FROM notification_outbox WHERE outbox_id=? AND user_id=?",
            (race_event.event_id, "pg-u1"),
        )
        assert raced is not None
        first_worker = raced["locked_by"]
        recovered = repo.claim_outbox(
            limit=1, now=datetime(2026, 10, 8, 6, 6),
            stale_after_seconds=300, worker_id="lease-recovery",
        )
        assert [row["outbox_id"] for row in recovered] == [race_event.event_id]
        assert first_worker != "lease-recovery"
        repo.mark_outbox_processed(
            race_event.event_id, "pg-u1", worker_id="lease-recovery",
        )
        db.connection.commit()

        db.undo_schedule_operation(operation_id, "pg-u1")
        assert db.get_schedule_operation(operation_id, "pg-u1")["status"] == "undone"
        assert any(
            row["event_type"] == "SCHEDULE_OPERATION_UNDONE"
            for row in repo.list_outbox("pg-u1")
        )

        before_tasks = db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"]
        before_events = db.fetch_one("SELECT COUNT(*) AS n FROM notification_outbox")["n"]
        try:
            with db.transaction():
                db.execute(
                    "INSERT INTO tasks(plan_id,title,estimated_minutes) VALUES (?,?,?)",
                    (plan1, "rollback", 30),
                )
                repo.enqueue_event(DomainEvent.create(
                    user_id="pg-u1", event_type=NotificationEventType.TASK_UNPLACED,
                    idempotency_key="rollback-event",
                ))
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        assert db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == before_tasks
        assert db.fetch_one("SELECT COUNT(*) AS n FROM notification_outbox")["n"] == before_events

        db.connection.commit()
        db.close()
        db = Database(raw_url)
        assert EXPECTED_TABLES <= set(inspect(db.engine).get_table_names())
        print(json.dumps({
            "postgresql_server": version,
            "tables": len(EXPECTED_TABLES),
            "schema_idempotent": True,
            "outbox_atomic": True,
            "tenant_isolation": True,
            "schedule_apply_undo": True,
            "policy_processing": True,
            "runtime_local_schedule": True,
            "atomic_claim": True,
            "worker_claim_race": True,
            "worker_lease_recovery": True,
        }, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
