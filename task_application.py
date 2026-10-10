"""Principal-bound, retry-safe task and timer application commands."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
import uuid

from backend_errors import (
    InvalidStateTransition,
    ResourceNotFound,
    ValidationError,
    VersionConflict,
)
from config import FAILURE_REASONS
from database import Database, OwnershipError
from domain_validation import validate_task_fields
from identity import AuthenticatedPrincipal
from mobile_repository import MobileRepository, public_task
from timezone_utils import user_today


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value)).replace(tzinfo=None)


class TaskApplicationService:
    """The production task boundary; it never accepts an acting ``user_id``."""

    def __init__(
        self,
        db: Database,
        principal: AuthenticatedPrincipal,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.db = db
        self.principal = principal
        self.user_id = principal.user_id
        self.mobile = MobileRepository(db)
        self.clock = clock

    def _task(self, task_id: int) -> dict:
        task = self.db.get_task(int(task_id), self.user_id)
        if task is None:
            # One response for absent and foreign resources avoids enumeration.
            raise ResourceNotFound("Task was not found.")
        return task

    def _require_plan(self, plan_id: int) -> None:
        if self.db.get_plan_by_id(int(plan_id), self.user_id) is None:
            raise ResourceNotFound("Plan was not found.")

    def _require_category(self, category_id: int | None) -> None:
        if category_id is None:
            return
        row = self.db.fetch_one(
            "SELECT category_id FROM categories WHERE category_id=? AND user_id=?",
            (int(category_id), self.user_id),
        )
        if row is None:
            raise ResourceNotFound("Category was not found.")

    def _execute_idempotent(self, **kwargs: Any) -> tuple[dict, bool]:
        """Translate repository ownership failures into the stable error model."""
        try:
            return self.mobile.execute_idempotent(**kwargs)
        except OwnershipError as exc:
            # Do not reveal whether the referenced identifier exists for a
            # different tenant.
            raise ResourceNotFound("A referenced resource was not found.") from exc

    @staticmethod
    def _require_version(task: dict, expected_version: int) -> None:
        if int(task.get("version") or 0) != int(expected_version):
            raise VersionConflict()

    def _emit_task_change(self, task_id: int, *, include_plan: bool = False) -> dict:
        task = self._task(task_id)
        revision = self.mobile.record_change(
            user_id=self.user_id,
            entity_type="task",
            entity_id=task_id,
            change_type="upsert",
            entity_version=int(task.get("version") or 0),
            payload=public_task(task),
        )
        if include_plan:
            revision = self.mobile.record_plan_bundle(
                user_id=self.user_id, plan_id=int(task["plan_id"]),
            )
        result = public_task(task)
        result["sync_revision"] = revision
        return result

    def create_task(
        self,
        *,
        plan_id: int,
        title: str,
        estimated_minutes: int,
        idempotency_key: str,
        description: str = "",
        category_id: int | None = None,
        priority: int = 3,
        order_index: int = 0,
        is_fixed_time: bool = False,
        is_break: bool = False,
        scheduled_start: str | None = None,
        scheduled_end: str | None = None,
        earliest_start: datetime | None = None,
        deadline: datetime | None = None,
    ) -> dict:
        clean_title, clean_description, minutes = validate_task_fields(
            title=title, description=description, estimated_minutes=estimated_minutes,
        )
        if int(priority) not in range(1, 6):
            raise ValidationError("Priority must be between 1 and 5.")
        self._require_plan(plan_id)
        self._require_category(category_id)
        request = {
            "plan_id": int(plan_id), "title": clean_title,
            "description": clean_description, "estimated_minutes": minutes,
            "category_id": category_id, "priority": int(priority),
            "order_index": int(order_index), "is_fixed_time": bool(is_fixed_time),
            "is_break": bool(is_break), "scheduled_start": scheduled_start,
            "scheduled_end": scheduled_end, "earliest_start": earliest_start,
            "deadline": deadline,
        }

        def mutation() -> dict:
            task_id = self.db._add_task_no_commit(
                plan_id=int(plan_id), user_id=self.user_id, title=clean_title,
                category_id=category_id, description=clean_description,
                priority=int(priority), estimated_minutes=minutes,
                scheduled_start=scheduled_start, scheduled_end=scheduled_end,
                order_index=int(order_index), is_fixed_time=bool(is_fixed_time),
                is_break=bool(is_break), earliest_start=earliest_start,
                deadline=deadline,
            )
            return self._emit_task_change(int(task_id), include_plan=True)

        result, replayed = self._execute_idempotent(
            user_id=self.user_id, operation="task.create",
            idempotency_key=idempotency_key, request_payload=request, mutation=mutation,
        )
        result["idempotent_replay"] = replayed
        return result

    def update_task(
        self,
        task_id: int,
        *,
        expected_version: int,
        idempotency_key: str,
        changes: dict[str, Any],
    ) -> dict:
        allowed = {
            "title", "description", "priority", "estimated_minutes", "order_index",
            "category_id", "is_fixed_time", "is_break", "earliest_start", "deadline",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"Unsupported task fields: {', '.join(sorted(unknown))}.")
        normalized = dict(changes)
        current = self._task(task_id)
        if any(key in normalized for key in ("title", "description", "estimated_minutes")):
            title, description, minutes = validate_task_fields(
                title=normalized.get("title", current["title"]),
                description=normalized.get("description", current.get("description")),
                estimated_minutes=normalized.get("estimated_minutes", current["estimated_minutes"]),
            )
            if "title" in normalized:
                normalized["title"] = title
            if "description" in normalized:
                normalized["description"] = description
            if "estimated_minutes" in normalized:
                normalized["estimated_minutes"] = minutes
        if "priority" in normalized and int(normalized["priority"]) not in range(1, 6):
            raise ValidationError("Priority must be between 1 and 5.")
        if "category_id" in normalized:
            self._require_category(normalized["category_id"])
        request = {
            "task_id": int(task_id), "expected_version": int(expected_version),
            "changes": normalized,
        }

        def mutation() -> dict:
            before = self._task(task_id)
            self._require_version(before, expected_version)
            if before["status"] in {"completed", "failed"}:
                raise InvalidStateTransition(
                    "Terminal tasks cannot be edited.",
                    current=str(before["status"]), requested="update",
                )
            # Existing update logic owns segment/revision/outbox consistency.
            self.db.update_task(task_id, self.user_id, **normalized)
            cas = self.db.execute(
                """UPDATE tasks SET version=version+1,updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=? AND plan_id IN
                     (SELECT plan_id FROM plans WHERE user_id=?) AND version=?""",
                (int(task_id), self.user_id, int(expected_version)),
            )
            if cas.rowcount != 1:
                raise VersionConflict()
            return self._emit_task_change(task_id, include_plan=True)

        result, replayed = self._execute_idempotent(
            user_id=self.user_id, operation="task.update",
            idempotency_key=idempotency_key, request_payload=request, mutation=mutation,
        )
        result["idempotent_replay"] = replayed
        return result

    def delete_task(
        self, task_id: int, *, expected_version: int, idempotency_key: str,
    ) -> dict:
        request = {"task_id": int(task_id), "expected_version": int(expected_version)}

        def mutation() -> dict:
            task = self._task(task_id)
            plan_id = int(task["plan_id"])
            self._require_version(task, expected_version)
            if task["status"] == "in_progress":
                raise InvalidStateTransition(
                    "A running or paused task must be finished before deletion.",
                    current="in_progress", requested="delete",
                )
            lock = self.db.execute(
                """UPDATE tasks SET version=version
                   WHERE task_id=? AND plan_id IN
                     (SELECT plan_id FROM plans WHERE user_id=?) AND version=?""",
                (int(task_id), self.user_id, int(expected_version)),
            )
            if lock.rowcount != 1:
                raise VersionConflict()
            segments = self.db.get_task_segments(task_id, self.user_id)
            owned = [
                {
                    "calendar_id": row.get("google_calendar_id") or "primary",
                    "google_event_id": row.get("google_event_id"),
                    "segment_id": row.get("segment_id"),
                }
                for row in segments
                if row.get("calendar_provenance") == "coachai_created"
                and row.get("google_event_id")
            ]
            if owned:
                self.db.execute(
                    """INSERT INTO calendar_operations
                       (operation_id,user_id,operation_type,idempotency_key,payload)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(user_id,operation_type,idempotency_key) DO NOTHING""",
                    (
                        uuid.uuid4().hex, self.user_id, "delete_task_events",
                        idempotency_key,
                        __import__("json").dumps(
                            {"task_id": task_id, "events": owned}, sort_keys=True
                        ),
                    ),
                )
            self.db.delete_task(task_id, self.user_id)
            revision = self.mobile.record_change(
                user_id=self.user_id, entity_type="task", entity_id=task_id,
                change_type="delete", entity_version=int(expected_version) + 1,
                payload={"task_id": int(task_id), "deleted": True},
            )
            revision = self.mobile.record_plan_bundle(
                user_id=self.user_id, plan_id=plan_id,
            )
            return {
                "task_id": int(task_id), "deleted": True,
                "version": int(expected_version) + 1, "sync_revision": revision,
                "calendar_reconciliation_pending": bool(owned),
            }

        result, replayed = self._execute_idempotent(
            user_id=self.user_id, operation="task.delete",
            idempotency_key=idempotency_key, request_payload=request, mutation=mutation,
        )
        result["idempotent_replay"] = replayed
        return result

    def _lifecycle(
        self,
        task_id: int,
        *,
        command: str,
        expected_version: int,
        idempotency_key: str,
        failure_reason: str | None = None,
    ) -> dict:
        request = {
            "task_id": int(task_id), "command": command,
            "expected_version": int(expected_version), "failure_reason": failure_reason,
        }

        def mutation() -> dict:
            task = self._task(task_id)
            self._require_version(task, expected_version)
            status = str(task["status"])
            running = bool(task.get("timer_segment_started_at"))
            paused = status == "in_progress" and not running and bool(task.get("paused_at"))
            now = self.clock().replace(tzinfo=None)
            now_text = now.isoformat(sep=" ", timespec="microseconds")
            params: tuple[Any, ...]

            if command == "start":
                if status != "pending":
                    raise InvalidStateTransition(
                        "Only a pending task can be started.", current=status, requested=command,
                    )
                sql = """UPDATE tasks SET status='in_progress',started_at=COALESCE(started_at,?),
                         timer_accumulated_seconds=0,timer_segment_started_at=?,paused_at=NULL,
                         timer_total_paused_seconds=0,timer_version=timer_version+1,
                         version=version+1,updated_at=?
                         WHERE task_id=? AND plan_id IN
                           (SELECT plan_id FROM plans WHERE user_id=?) AND version=? AND status='pending'"""
                params = (now_text, now_text, now_text, task_id, self.user_id, expected_version)
                event_type = "TASK_STARTED"
            elif command == "pause":
                if status != "in_progress" or not running:
                    raise InvalidStateTransition(
                        "Only a running task can be paused.",
                        current="paused" if paused else status, requested=command,
                    )
                started = _as_naive_datetime(task.get("timer_segment_started_at"))
                elapsed = int(task.get("timer_accumulated_seconds") or 0) + max(
                    0, int((now - started).total_seconds()) if started else 0
                )
                sql = """UPDATE tasks SET timer_accumulated_seconds=?,timer_segment_started_at=NULL,
                         paused_at=?,pause_count=pause_count+1,timer_version=timer_version+1,
                         version=version+1,updated_at=?
                         WHERE task_id=? AND plan_id IN
                           (SELECT plan_id FROM plans WHERE user_id=?) AND version=?
                           AND status='in_progress' AND timer_segment_started_at IS NOT NULL"""
                params = (elapsed, now_text, now_text, task_id, self.user_id, expected_version)
                event_type = None
            elif command == "resume":
                if not paused:
                    raise InvalidStateTransition(
                        "Only a paused task can be resumed.", current=status, requested=command,
                    )
                paused_at = _as_naive_datetime(task.get("paused_at"))
                total_paused = int(task.get("timer_total_paused_seconds") or 0) + max(
                    0, int((now - paused_at).total_seconds()) if paused_at else 0
                )
                sql = """UPDATE tasks SET timer_segment_started_at=?,paused_at=NULL,
                         timer_total_paused_seconds=?,timer_version=timer_version+1,
                         version=version+1,updated_at=?
                         WHERE task_id=? AND plan_id IN
                           (SELECT plan_id FROM plans WHERE user_id=?) AND version=?
                           AND status='in_progress' AND timer_segment_started_at IS NULL
                           AND paused_at IS NOT NULL"""
                params = (now_text, total_paused, now_text, task_id, self.user_id, expected_version)
                event_type = None
            elif command in {"complete", "fail"}:
                if status not in {"pending", "in_progress"}:
                    raise InvalidStateTransition(
                        "Only a pending, running, or paused task can be finished.",
                        current=status, requested=command,
                    )
                if command == "fail" and failure_reason not in FAILURE_REASONS:
                    raise ValidationError("A supported failure reason is required.")
                accumulated = int(task.get("timer_accumulated_seconds") or 0)
                segment_start = _as_naive_datetime(task.get("timer_segment_started_at"))
                if segment_start:
                    accumulated += max(0, int((now - segment_start).total_seconds()))
                total_paused = int(task.get("timer_total_paused_seconds") or 0)
                paused_at = _as_naive_datetime(task.get("paused_at"))
                if paused_at:
                    total_paused += max(0, int((now - paused_at).total_seconds()))
                actual_minutes = (
                    max(1, round(accumulated / 60))
                    if accumulated > 0 else int(task["estimated_minutes"])
                )
                terminal = "completed" if command == "complete" else "failed"
                sql = """UPDATE tasks SET status=?,failure_reason=?,actual_minutes=?,completed_at=?,
                         timer_accumulated_seconds=?,timer_segment_started_at=NULL,paused_at=NULL,
                         timer_total_paused_seconds=?,timer_version=timer_version+1,
                         version=version+1,updated_at=?
                         WHERE task_id=? AND plan_id IN
                           (SELECT plan_id FROM plans WHERE user_id=?) AND version=?
                           AND status IN ('pending','in_progress')"""
                params = (
                    terminal, failure_reason if terminal == "failed" else None,
                    actual_minutes, now_text, accumulated, total_paused, now_text,
                    task_id, self.user_id, expected_version,
                )
                event_type = "TASK_COMPLETED" if terminal == "completed" else "TASK_FAILED"
            else:
                raise ValidationError(f"Unsupported lifecycle command: {command}.")

            changed = self.db.execute(sql, params)
            if changed.rowcount != 1:
                raise VersionConflict()
            if event_type:
                self.db._enqueue_notification_event_no_commit(
                    user_id=self.user_id, event_type=event_type, task_id=task_id,
                    payload={
                        "task_title": str(task["title"]), "previous_status": status,
                        "failure_reason": failure_reason,
                    },
                    idempotency_key=f"task-command:{self.user_id}:{idempotency_key}",
                    bump_sync_revision=True,
                )
            return self._emit_task_change(task_id)

        result, replayed = self._execute_idempotent(
            user_id=self.user_id, operation=f"task.{command}",
            idempotency_key=idempotency_key, request_payload=request, mutation=mutation,
        )
        result["idempotent_replay"] = replayed
        return result

    def start(self, task_id: int, *, expected_version: int, idempotency_key: str) -> dict:
        return self._lifecycle(
            task_id, command="start", expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def pause(self, task_id: int, *, expected_version: int, idempotency_key: str) -> dict:
        return self._lifecycle(
            task_id, command="pause", expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def resume(self, task_id: int, *, expected_version: int, idempotency_key: str) -> dict:
        return self._lifecycle(
            task_id, command="resume", expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def complete(self, task_id: int, *, expected_version: int, idempotency_key: str) -> dict:
        return self._lifecycle(
            task_id, command="complete", expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def fail(
        self, task_id: int, *, failure_reason: str,
        expected_version: int, idempotency_key: str,
    ) -> dict:
        return self._lifecycle(
            task_id, command="fail", failure_reason=failure_reason,
            expected_version=expected_version, idempotency_key=idempotency_key,
        )

    def defer_to_tomorrow(
        self, task_id: int, *, expected_version: int, idempotency_key: str,
    ) -> dict:
        request = {"task_id": int(task_id), "expected_version": int(expected_version)}

        def mutation() -> dict:
            task = self._task(task_id)
            source_plan_id = int(task["plan_id"])
            self._require_version(task, expected_version)
            if task["status"] != "pending":
                raise InvalidStateTransition(
                    "Only a pending task can be deferred.",
                    current=str(task["status"]), requested="defer",
                )
            lock = self.db.execute(
                """UPDATE tasks SET version=version+1,updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=? AND plan_id IN
                     (SELECT plan_id FROM plans WHERE user_id=?) AND version=? AND status='pending'""",
                (task_id, self.user_id, expected_version),
            )
            if lock.rowcount != 1:
                raise VersionConflict()
            tomorrow = user_today(self.db, self.user_id) + timedelta(days=1)
            target = self.db.get_plan_by_date(self.user_id, tomorrow)
            if target is None:
                destination = self.db.create_plan(
                    self.user_id, tomorrow, "Tasks deferred from a previous day."
                )
            else:
                destination = int(target["plan_id"])
            order_index = len(self.db.get_tasks_by_plan(destination, self.user_id))
            self.db.move_task_unscheduled(task_id, self.user_id, destination, order_index)
            result = self._emit_task_change(task_id, include_plan=True)
            if source_plan_id != int(destination):
                result["sync_revision"] = self.mobile.record_plan_bundle(
                    user_id=self.user_id, plan_id=source_plan_id,
                )
            return result

        result, replayed = self._execute_idempotent(
            user_id=self.user_id, operation="task.defer",
            idempotency_key=idempotency_key, request_payload=request, mutation=mutation,
        )
        result["idempotent_replay"] = replayed
        return result
