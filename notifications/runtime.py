"""Small framework-independent entry points for future API and job runners."""

from __future__ import annotations

from typing import Any

from .service import NotificationService


def process_pending_notifications(db, **kwargs: Any) -> dict:
    return NotificationService(db).process_pending_notifications(**kwargs)


def process_pending_push_deliveries(db, **kwargs: Any) -> dict:
    return NotificationService(db).process_push_deliveries(**kwargs)


def get_user_notifications(db, user_id: str, **kwargs: Any) -> list[dict]:
    return NotificationService(db).get_user_notifications(user_id, **kwargs)


def mark_notification_read(db, user_id: str, notification_id: str) -> None:
    NotificationService(db).mark_notification_read(user_id, notification_id)


def mark_notification_acted(db, user_id: str, notification_id: str) -> None:
    NotificationService(db).mark_notification_acted(user_id, notification_id)


def get_notification_sync_state(db, user_id: str) -> dict:
    return NotificationService(db).get_notification_sync_state(user_id)


def sync_notifications_since_revision(db, user_id: str, revision: int, **kwargs: Any) -> dict:
    return NotificationService(db).sync_notifications_since_revision(user_id, revision, **kwargs)


def reconcile_task_notifications(db, user_id: str, task_id: int, **kwargs: Any) -> dict:
    return NotificationService(db).reconcile_task_notifications(user_id, task_id, **kwargs)


def reconcile_schedule_notifications(db, user_id: str, task_ids: list[int], **kwargs: Any) -> dict:
    return NotificationService(db).reconcile_schedule_notifications(user_id, task_ids, **kwargs)


def cancel_task_notifications(db, user_id: str, task_id: int, **kwargs: Any) -> int:
    return NotificationService(db).cancel_task_notifications(user_id, task_id, **kwargs)


def schedule_local_notifications(db, user_id: str, task_id: int, **kwargs: Any) -> dict:
    return NotificationService(db).schedule_local_notifications(user_id, task_id, **kwargs)
