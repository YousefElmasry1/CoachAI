"""Opt-in principal-bound backend-core verification on disposable PostgreSQL.

The target is guarded to the loopback database ``coachai_backend_core`` and is
not part of normal unittest discovery because it resets that database schema.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import os
import threading

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from application_services import CoachAIApplication
from backend_errors import IdempotencyConflict, ResourceNotFound, VersionConflict
from database import Database
from identity import AuthenticatedPrincipal
from planner import DayPlanOutput, TaskOutput
from rate_limiter import DatabaseRateLimiter, RateLimitExceeded
from task_application import TaskApplicationService


RAW_URL = os.environ.get("COACHAI_BACKEND_CORE_POSTGRES_URL", "")


def _checked_url() -> str:
    parsed = make_url(RAW_URL) if RAW_URL else None
    if (
        parsed is None
        or parsed.drivername != "postgresql+psycopg2"
        or parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "coachai_backend_core"
    ):
        raise RuntimeError(
            "Refusing to reset anything except localhost/coachai_backend_core."
        )
    return RAW_URL


def _reset_and_migrate() -> None:
    engine = create_engine(_checked_url(), future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def _principal(user_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id,
        f"{user_id}@example.test",
        user_id,
        {"sub": user_id},
    )


def main() -> None:
    _reset_and_migrate()
    db = Database(_checked_url())
    try:
        server_version = db.connection.execute(
            text("SHOW server_version")
        ).scalar_one()
        db.connection.commit()
        assert str(server_version) == "16.15"
        for user_id in ("core-u1", "core-u2"):
            db.create_user(
                user_id,
                f"{user_id}@example.test",
                user_id,
                "Africa/Cairo",
            )
        plan_u1 = db.create_plan(
            "core-u1", date(2099, 1, 2), "backend-core verification"
        )
        plan_u2 = db.create_plan(
            "core-u2", date(2099, 1, 2), "other tenant"
        )
        foreign_category = db.create_category("core-u2", "Private", "#112233")

        app = CoachAIApplication(db, _principal("core-u1"))
        try:
            app.tasks.create_task(
                plan_id=plan_u1,
                title="Forbidden category",
                estimated_minutes=30,
                category_id=foreign_category,
                idempotency_key="foreign-category",
            )
        except ResourceNotFound:
            pass
        else:
            raise AssertionError("Cross-tenant category reference was accepted.")

        created = app.tasks.create_task(
            plan_id=plan_u1,
            title="Atomic timer",
            estimated_minutes=45,
            idempotency_key="create-atomic-timer",
        )
        replay = app.tasks.create_task(
            plan_id=plan_u1,
            title="Atomic timer",
            estimated_minutes=45,
            idempotency_key="create-atomic-timer",
        )
        assert replay["idempotent_replay"] is True
        assert replay["task_id"] == created["task_id"]
        try:
            app.tasks.create_task(
                plan_id=plan_u1,
                title="Different request",
                estimated_minutes=45,
                idempotency_key="create-atomic-timer",
            )
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("Mismatched idempotency replay was accepted.")

        foreign_task = db.add_task(
            plan_u2, "core-u2", "Private task", estimated_minutes=30
        )
        try:
            app.tasks.start(
                foreign_task, expected_version=0, idempotency_key="foreign-start"
            )
        except ResourceNotFound:
            pass
        else:
            raise AssertionError("Cross-tenant task command was accepted.")

        moments = iter(
            (
                datetime(2099, 1, 2, 8, 0),
                datetime(2099, 1, 2, 8, 10),
                datetime(2099, 1, 2, 8, 15),
                datetime(2099, 1, 2, 8, 45),
                datetime(2099, 1, 2, 8, 46),
            )
        )
        timers = TaskApplicationService(
            db, _principal("core-u1"), clock=lambda: next(moments)
        )
        task_id = int(created["task_id"])
        started = timers.start(task_id, expected_version=0, idempotency_key="start")
        start_replay = timers.start(
            task_id, expected_version=0, idempotency_key="start"
        )
        assert start_replay["idempotent_replay"] is True
        paused = timers.pause(
            task_id, expected_version=int(started["version"]), idempotency_key="pause"
        )
        resumed = timers.resume(
            task_id, expected_version=int(paused["version"]), idempotency_key="resume"
        )
        completed = timers.complete(
            task_id,
            expected_version=int(resumed["version"]),
            idempotency_key="complete",
        )
        assert completed["status"] == "completed"
        assert int(completed["timer_accumulated_seconds"]) == 2400
        try:
            timers.complete(
                task_id,
                expected_version=int(completed["version"]),
                idempotency_key="complete-again",
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "INVALID_STATE_TRANSITION"
        else:
            raise AssertionError("Duplicate terminal transition was accepted.")
        try:
            timers.update_task(
                task_id,
                expected_version=0,
                idempotency_key="stale-edit",
                changes={"title": "Stale"},
            )
        except VersionConflict:
            pass
        else:
            raise AssertionError("Stale task edit was accepted.")

        queued = app.calendar.queue_mutation(
            operation_type="event.upsert",
            payload={"task_id": task_id},
            idempotency_key="calendar-upsert",
        )
        queued_replay = app.calendar.queue_mutation(
            operation_type="event.upsert",
            payload={"task_id": task_id},
            idempotency_key="calendar-upsert",
        )
        assert queued["created"] is True and queued_replay["created"] is False
        try:
            app.calendar.queue_mutation(
                operation_type="event.upsert",
                payload={"task_id": foreign_task},
                idempotency_key="foreign-calendar-upsert",
            )
        except ResourceNotFound:
            pass
        else:
            raise AssertionError("Cross-tenant Calendar mutation was accepted.")

        snapshot = app.full_resync()
        assert snapshot["user"]["user_id"] == "core-u1"
        assert all(item["plan_id"] != plan_u2 for item in snapshot["plans"])
        delta = app.sync_since(0)
        assert delta["current_revision"] >= delta["revision"] > 0

        draft = DayPlanOutput(
            planning_notes="Validated draft",
            tasks=[TaskOutput(
                title="Draft-only persistence",
                category_name="Work",
                estimated_minutes=30,
            )],
        )
        applied = app.planning.apply_draft(
            draft,
            raw_input="Draft-only persistence",
            idempotency_key="ai-apply",
        )
        applied_replay = app.planning.apply_draft(
            draft,
            raw_input="Draft-only persistence",
            idempotency_key="ai-apply",
        )
        assert applied_replay["idempotent_replay"] is True
        assert applied_replay["plan_id"] == applied["plan_id"]

        limiter_a = DatabaseRateLimiter(
            db, scope="postgres.shared", max_requests=2, window_seconds=60,
            wall_clock=lambda: 120.0,
        )
        second_db = Database(_checked_url())
        try:
            limiter_b = DatabaseRateLimiter(
                second_db,
                scope="postgres.shared",
                max_requests=2,
                window_seconds=60,
                wall_clock=lambda: 120.0,
            )
            limiter_a.check("core-u1")
            limiter_b.check("core-u1")
            try:
                limiter_a.check("core-u1")
            except RateLimitExceeded:
                pass
            else:
                raise AssertionError("Shared rate limit was bypassed.")
        finally:
            second_db.close()

        # Two independent request-scoped connections racing the same command
        # converge on one durable result and one task row.
        barrier = threading.Barrier(2)

        def create_once() -> tuple[int, bool]:
            worker_db = Database(_checked_url())
            try:
                service = TaskApplicationService(worker_db, _principal("core-u1"))
                barrier.wait(timeout=5)
                result = service.create_task(
                    plan_id=plan_u1,
                    title="Concurrent idempotency",
                    estimated_minutes=30,
                    idempotency_key="concurrent-create",
                )
                return int(result["task_id"]), bool(result["idempotent_replay"])
            finally:
                worker_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _value: create_once(), range(2)))
        assert len({item[0] for item in results}) == 1
        assert sorted(item[1] for item in results) == [False, True]
        assert db.fetch_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE title=?",
            ("Concurrent idempotency",),
        )["n"] == 1

        assert db.fetch_one(
            "SELECT version_num FROM alembic_version"
        )["version_num"] == "20261009_0002"
        print(json.dumps({
            "postgresql_server": server_version,
            "principal_tenant_boundary": True,
            "task_state_machine": True,
            "timer_atomicity": True,
            "durable_idempotency": True,
            "concurrent_idempotency": True,
            "application_sync": True,
            "calendar_queue": True,
            "ai_apply_boundary": True,
            "shared_rate_limit": True,
        }, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
