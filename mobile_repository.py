"""Durable command replay and application-data synchronization.

Notification synchronization intentionally remains in ``notifications``;
this repository tracks business entities and deletion tombstones.
"""

from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
from typing import Any, Callable
import uuid

from backend_errors import IdempotencyConflict, ValidationError, VersionConflict
from database import Database, OwnershipError


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=_json_default,
    )


def public_task(task: dict) -> dict:
    """Allowlisted mobile-safe task representation."""
    keys = (
        "task_id", "plan_id", "category_id", "title", "description", "priority",
        "estimated_minutes", "scheduled_start", "scheduled_end", "is_fixed_time",
        "is_break", "order_index", "status", "failure_reason", "actual_minutes",
        "started_at", "completed_at", "timer_accumulated_seconds",
        "timer_segment_started_at", "pause_count", "paused_at",
        "timer_total_paused_seconds", "timer_version", "version", "earliest_start",
        "deadline", "created_at", "updated_at",
    )
    return {key: task.get(key) for key in keys}


def public_plan(plan: dict) -> dict:
    keys = (
        "plan_id", "plan_date", "raw_input", "ai_summary", "status",
        "schedule_revision", "created_at",
    )
    return {key: plan.get(key) for key in keys}


def public_segment(segment: dict) -> dict:
    # Calendar provider identifiers are data, not credentials.  Sync status
    # is included so clients can display pending/conflict state without ever
    # receiving OAuth tokens.
    keys = (
        "segment_id", "task_id", "segment_index", "scheduled_date",
        "scheduled_start", "scheduled_end", "google_event_id",
        "google_calendar_id", "calendar_provenance", "calendar_sync_status",
        "calendar_sync_error", "created_at", "updated_at",
    )
    return {key: segment.get(key) for key in keys}


class MobileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def request_hash(payload: Any) -> str:
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def execute_idempotent(
        self,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_payload: Any,
        mutation: Callable[[], dict],
    ) -> tuple[dict, bool]:
        """Execute ``mutation`` and store its result in the same transaction."""
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValidationError("A non-empty idempotency key of at most 200 characters is required.")
        op = str(operation or "").strip()
        digest = self.request_hash(request_payload)
        with self.db.transaction():
            inserted = self.db.execute(
                """INSERT INTO idempotency_records
                   (record_id,user_id,operation,idempotency_key,request_hash)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id,operation,idempotency_key) DO NOTHING""",
                (uuid.uuid4().hex, user_id, op, key, digest),
            )
            row = self.db.fetch_one(
                """SELECT request_hash,response_json FROM idempotency_records
                   WHERE user_id=? AND operation=? AND idempotency_key=?""",
                (user_id, op, key),
            )
            if row is None:
                raise RuntimeError("Idempotency record could not be created.")
            if str(row["request_hash"]) != digest:
                raise IdempotencyConflict()
            if inserted.rowcount == 0:
                if row.get("response_json") is None:
                    raise VersionConflict("The matching operation is still in progress; retry later.")
                return json.loads(str(row["response_json"])), True

            result = mutation()
            encoded = canonical_json(result)
            self.db.execute(
                """UPDATE idempotency_records
                   SET response_json=?,completed_at=CURRENT_TIMESTAMP
                   WHERE user_id=? AND operation=? AND idempotency_key=?""",
                (encoded, user_id, op, key),
            )
            return json.loads(encoded), False

    def record_change(
        self,
        *,
        user_id: str,
        entity_type: str,
        entity_id: str | int,
        change_type: str,
        entity_version: int | None,
        payload: dict | None = None,
    ) -> int:
        if change_type not in {"upsert", "delete"}:
            raise ValidationError("Application change type must be upsert or delete.")
        self.db.execute(
            """INSERT INTO application_sync_state(user_id,sync_revision,updated_at)
               VALUES (?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 sync_revision=application_sync_state.sync_revision+1,
                 updated_at=CURRENT_TIMESTAMP""",
            (user_id,),
        )
        state = self.db.fetch_one(
            "SELECT sync_revision FROM application_sync_state WHERE user_id=?",
            (user_id,),
        )
        revision = int(state["sync_revision"])
        self.db.execute(
            """INSERT INTO application_changes
               (change_id,user_id,sync_revision,entity_type,entity_id,change_type,entity_version,payload)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex, user_id, revision, entity_type, str(entity_id),
                change_type, entity_version, canonical_json(payload or {}),
            ),
        )
        return revision

    def record_plan_bundle(self, *, user_id: str, plan_id: int) -> int:
        """Publish a plan revision and an authoritative segment replacement.

        Segments are rewritten during scheduling, so a replacement bundle is
        the unambiguous mobile tombstone contract: any locally cached segment
        absent from ``segments`` is deleted at this revision.
        """
        plan = self.db.get_plan_by_id(int(plan_id), user_id)
        if plan is None:
            raise OwnershipError("Plan was not found or is not owned by this user.")
        version = int(plan.get("schedule_revision") or 0)
        self.record_change(
            user_id=user_id,
            entity_type="plan",
            entity_id=plan_id,
            change_type="upsert",
            entity_version=version,
            payload=public_plan(plan),
        )
        segments = self.db.fetch_all(
            """SELECT s.* FROM task_segments s
               JOIN tasks t ON t.task_id=s.task_id
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.plan_id=? AND p.user_id=?
               ORDER BY t.order_index,s.segment_index,s.segment_id""",
            (int(plan_id), user_id),
        )
        return self.record_change(
            user_id=user_id,
            entity_type="plan_segments",
            entity_id=plan_id,
            change_type="upsert",
            entity_version=version,
            payload={
                "plan_id": int(plan_id),
                "schedule_revision": version,
                "replace_all": True,
                "segments": [public_segment(row) for row in segments],
            },
        )

    def current_revision(self, user_id: str) -> int:
        if self.db.get_user(user_id) is None:
            raise OwnershipError("User was not found.")
        row = self.db.fetch_one(
            "SELECT sync_revision FROM application_sync_state WHERE user_id=?",
            (user_id,),
        )
        return int(row["sync_revision"]) if row else 0

    def sync_since(self, user_id: str, cursor: int, *, limit: int = 500) -> dict:
        if cursor < 0:
            raise ValidationError("Sync cursor cannot be negative.")
        bounded_limit = max(1, min(int(limit), 500))
        current = self.current_revision(user_id)
        if cursor > current:
            raise VersionConflict("Sync cursor is ahead of the server revision.")
        state = self.db.fetch_one(
            """SELECT minimum_available_revision FROM application_sync_state
               WHERE user_id=?""",
            (user_id,),
        )
        minimum = int(state["minimum_available_revision"]) if state else 0
        if cursor < minimum:
            return {
                "from_revision": cursor,
                "revision": current,
                "current_revision": current,
                "has_more": False,
                "full_resync_required": True,
                "changes": [],
            }
        rows = self.db.fetch_all(
            """SELECT sync_revision,entity_type,entity_id,change_type,entity_version,payload,created_at
               FROM application_changes
               WHERE user_id=? AND sync_revision>? AND sync_revision<=?
               ORDER BY sync_revision LIMIT ?""",
            (user_id, int(cursor), current, bounded_limit),
        )
        changes = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(str(item.get("payload") or "{}"))
            changes.append(item)
        checkpoint = int(changes[-1]["sync_revision"]) if changes else current
        return {
            "from_revision": cursor,
            "revision": checkpoint,
            "current_revision": current,
            "has_more": checkpoint < current,
            "full_resync_required": False,
            "changes": changes,
        }

    def full_snapshot(self, user_id: str) -> dict:
        """Return a secret-free, revision-consistent mobile resync payload."""
        for _attempt in range(3):
            with self.db.transaction():
                before = self.current_revision(user_id)
                snapshot = self._full_snapshot_unlocked(user_id)
                after = self.current_revision(user_id)
                if before == after:
                    snapshot["revision"] = after
                    return snapshot
        raise VersionConflict("The account changed while its snapshot was being built; retry.")

    def _full_snapshot_unlocked(self, user_id: str) -> dict:
        user = self.db.fetch_one(
            """SELECT user_id,email,display_name,timezone,is_disabled,created_at,updated_at
               FROM users WHERE user_id=?""",
            (user_id,),
        )
        if user is None:
            raise OwnershipError("User was not found.")
        plans = self.db.fetch_all(
            """SELECT plan_id,plan_date,raw_input,ai_summary,status,created_at,schedule_revision
               FROM plans WHERE user_id=? ORDER BY plan_date,plan_id""",
            (user_id,),
        )
        tasks = self.db.fetch_all(
            """SELECT t.* FROM tasks t JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? ORDER BY p.plan_date,t.order_index,t.task_id""",
            (user_id,),
        )
        segments = self.db.fetch_all(
            """SELECT s.* FROM task_segments s
               JOIN tasks t ON t.task_id=s.task_id
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? ORDER BY s.scheduled_date,s.scheduled_start,s.segment_id""",
            (user_id,),
        )
        categories = self.db.get_categories(user_id)
        preferences = self.db.fetch_one(
            """SELECT * FROM notification_preferences WHERE user_id=?""", (user_id,)
        )
        type_preferences = self.db.fetch_all(
            """SELECT notification_type,enabled,preferred_channel,lead_minutes,
                      cooldown_seconds,interruption_level,created_at,updated_at
               FROM notification_type_preferences WHERE user_id=?
               ORDER BY notification_type""",
            (user_id,),
        )
        selected_calendars = self.db.fetch_all(
            """SELECT calendar_id,calendar_name,color,is_primary,created_at
               FROM google_selected_calendars WHERE user_id=?
               ORDER BY is_primary DESC,calendar_name,calendar_id""",
            (user_id,),
        )
        token_state = self.db.fetch_one(
            """SELECT token_expiry,scopes,created_at,updated_at
               FROM google_oauth_tokens WHERE user_id=?""",
            (user_id,),
        )
        calendar_state = self.db.fetch_all(
            """SELECT calendar_id,range_start,range_end,status,last_error,last_success_at,updated_at
               FROM calendar_sync_state WHERE user_id=? ORDER BY calendar_id""",
            (user_id,),
        )
        return {
            "user": user,
            "plans": plans,
            "tasks": [public_task(row) for row in tasks],
            "segments": segments,
            "categories": categories,
            "settings": preferences,
            "notification_type_preferences": type_preferences,
            "selected_calendars": selected_calendars,
            "calendar_connection": {
                "provider": "google",
                "connected": token_state is not None,
                "token_expiry": token_state.get("token_expiry") if token_state else None,
                "scopes": token_state.get("scopes") if token_state else "",
                "created_at": token_state.get("created_at") if token_state else None,
                "updated_at": token_state.get("updated_at") if token_state else None,
            },
            "calendar_state": calendar_state,
        }
