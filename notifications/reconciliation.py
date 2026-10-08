"""Schedule-derived reminder reconciliation with no mobile/provider dependency."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, TYPE_CHECKING

from timezone_utils import safe_zoneinfo

from .domain import DomainEvent, NotificationEventType, NotificationType, utc_now_naive
from .repository import NotificationRepository

if TYPE_CHECKING:
    from database import Database


def _local_wall_to_utc(day: date, value: time | str, timezone_name: str) -> datetime:
    parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    wall = datetime.combine(day, parsed.replace(tzinfo=None))
    zone = safe_zoneinfo(timezone_name)
    # Advance nonexistent spring-forward wall times. For an ambiguous fold,
    # choose the later occurrence so reminders never fire unexpectedly early.
    for _ in range(181):
        candidates = []
        for fold in (0, 1):
            aware = wall.replace(tzinfo=zone, fold=fold)
            if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall:
                candidates.append(aware)
        if candidates:
            return max(candidate.astimezone(timezone.utc) for candidate in candidates).replace(tzinfo=None)
        wall += timedelta(minutes=1)
    raise ValueError("Could not resolve task segment time in the user's timezone.")


class NotificationReconciler:
    def __init__(self, db: "Database") -> None:
        self.db = db
        self.repository = NotificationRepository(db)

    def _lead_minutes(self, user_id: str) -> Optional[int]:
        preferences = self.repository.get_preferences(user_id)
        override = self.repository.get_type_preference(user_id, NotificationType.TASK_REMINDER)
        lead = override.get("lead_minutes") if override and override.get("lead_minutes") is not None else preferences.get("default_reminder_lead_minutes")
        return None if lead in (None, 0) else int(lead)

    def current_identity(self, user_id: str, task_id: int, segment_index: int) -> Optional[tuple[int, int, int]]:
        row = self.db.fetch_one(
            """SELECT s.segment_index,p.schedule_revision
               FROM task_segments s JOIN tasks t ON t.task_id=s.task_id
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? AND t.task_id=? AND s.segment_index=?""",
            (user_id, int(task_id), int(segment_index)),
        )
        if row is None:
            return None
        return (int(task_id), int(row["segment_index"]), int(row["schedule_revision"] or 0))

    def reminder_is_current(self, event: DomainEvent, *, now_utc: Optional[datetime] = None) -> bool:
        if event.task_id is None or event.segment_index is None or event.schedule_revision is None:
            return False
        identity = self.current_identity(event.user_id, event.task_id, event.segment_index)
        if identity != (event.task_id, event.segment_index, event.schedule_revision):
            return False
        row = self.db.fetch_one(
            """SELECT t.status,s.scheduled_date,s.scheduled_end,u.timezone
               FROM task_segments s JOIN tasks t ON t.task_id=s.task_id
               JOIN plans p ON p.plan_id=t.plan_id JOIN users u ON u.user_id=p.user_id
               WHERE p.user_id=? AND t.task_id=? AND s.segment_index=?""",
            (event.user_id, event.task_id, event.segment_index),
        )
        if row is None or row["status"] != "pending":
            return False
        end_utc = _local_wall_to_utc(date.fromisoformat(str(row["scheduled_date"])), row["scheduled_end"], row["timezone"])
        return end_utc > (now_utc or utc_now_naive()).replace(tzinfo=None)

    def cancel_task_notifications(self, user_id: str, task_id: int, *, reason: str = "task_state_changed") -> int:
        self.repository.ensure_user(user_id)
        self.db.execute(
            """UPDATE notification_outbox SET status='cancelled',processed_at=CURRENT_TIMESTAMP,
                   last_error=? WHERE user_id=? AND task_id=? AND event_type='TASK_REMINDER_DUE'
                   AND status IN ('queued','failed')""",
            (reason, user_id, int(task_id)),
        )
        return self.repository.cancel_local_schedules(user_id, task_id=task_id, reason=reason)

    def reconcile_task_notifications(
        self, user_id: str, task_id: int, *, operation_id: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> dict:
        task = self.repository.ensure_task(user_id, task_id)
        if task["status"] != "pending":
            return {"scheduled": 0, "cancelled": self.cancel_task_notifications(user_id, task_id)}
        lead = self._lead_minutes(user_id)
        if lead is None:
            return {"scheduled": 0, "cancelled": self.cancel_task_notifications(user_id, task_id, reason="reminders_off")}
        preferences = self.repository.get_preferences(user_id)
        segments = self.db.fetch_all(
            """SELECT s.*,p.schedule_revision,p.plan_date
               FROM task_segments s JOIN tasks t ON t.task_id=s.task_id
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? AND t.task_id=? ORDER BY s.segment_index""",
            (user_id, int(task_id)),
        )
        current: set[tuple[int, int, int]] = set()
        queued = 0
        now = (now_utc or utc_now_naive()).replace(tzinfo=None)
        for segment in segments:
            revision = int(segment["schedule_revision"] or 0)
            index = int(segment["segment_index"])
            current.add((int(task_id), index, revision))
            day = date.fromisoformat(str(segment.get("scheduled_date") or segment["plan_date"]))
            start_utc = _local_wall_to_utc(day, segment["scheduled_start"], preferences["timezone"])
            end_utc = _local_wall_to_utc(day, segment["scheduled_end"], preferences["timezone"])
            if end_utc <= now:
                continue
            fire_at = max(now, start_utc - timedelta(minutes=lead))
            identity = f"task:{task_id}:{day.isoformat()}:{index}:reminder:rev{revision}"
            event = DomainEvent.create(
                user_id=user_id, event_type=NotificationEventType.TASK_REMINDER_DUE,
                task_id=task_id, segment_id=int(segment["segment_id"]),
                scheduled_date=day, segment_index=index, schedule_revision=revision,
                schedule_operation_id=operation_id,
                idempotency_key=identity,
                payload={
                    "task_title": str(task["title"]),
                    "scheduled_for": fire_at.isoformat(),
                    "expires_at": end_utc.isoformat(),
                    "dedupe_key": f"notification:{identity}",
                    "actions": {"open_task": int(task_id)},
                },
                occurred_at=now,
            )
            _, created, _ = self.repository.enqueue_event(event)
            queued += int(created)
        cancelled = self.repository.cancel_local_schedules(
            user_id, task_id=task_id, except_identities=current, reason="schedule_revision_changed",
        )
        self.db.execute(
            """UPDATE notification_outbox SET status='cancelled',processed_at=CURRENT_TIMESTAMP,
                   last_error='schedule_revision_changed'
               WHERE user_id=? AND task_id=? AND event_type='TASK_REMINDER_DUE'
                 AND status IN ('queued','failed')
                 AND (schedule_revision IS NULL OR schedule_revision NOT IN (
                     SELECT p.schedule_revision FROM tasks t JOIN plans p ON p.plan_id=t.plan_id
                     WHERE t.task_id=? AND p.user_id=?))""",
            (user_id, int(task_id), int(task_id), user_id),
        )
        return {"scheduled": queued, "cancelled": cancelled}

    def reconcile_schedule_notifications(
        self, user_id: str, task_ids: list[int], *, operation_id: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> dict:
        scheduled = cancelled = 0
        for task_id in sorted({int(value) for value in task_ids}):
            task = self.db.fetch_one(
                """SELECT t.task_id FROM tasks t JOIN plans p ON p.plan_id=t.plan_id
                   WHERE t.task_id=? AND p.user_id=?""",
                (task_id, user_id),
            )
            if task is None:
                cancelled += self.cancel_task_notifications(user_id, task_id, reason="task_deleted")
                continue
            result = self.reconcile_task_notifications(
                user_id, task_id, operation_id=operation_id, now_utc=now_utc,
            )
            scheduled += result["scheduled"]
            cancelled += result["cancelled"]
        return {"scheduled": scheduled, "cancelled": cancelled}
