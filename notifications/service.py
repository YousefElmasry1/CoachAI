"""Application service for outbox processing and in-app notification state."""

from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any, Optional, TYPE_CHECKING

import logging
from time import perf_counter
import uuid

from database import OwnershipError

from .channels import InAppChannel, LocalScheduleChannel, PushIntentChannel
from .domain import (
    DeliveryChannel,
    DomainEvent,
    NotificationEventType,
    RiskState,
    Tone,
    utc_now_naive,
)
from .policy import NotificationPolicy
from .repository import NotificationRepository
from .reconciliation import NotificationReconciler
from .providers import (
    ErrorClassification, ProviderResult, PushMessage, PushProvider,
    build_push_payload, providers_from_environment,
)
from .templates import TemplateCatalog

if TYPE_CHECKING:
    from database import Database


class NotificationService:
    def __init__(
        self,
        db: "Database",
        *,
        policy: Optional[NotificationPolicy] = None,
        templates: Optional[TemplateCatalog] = None,
        push_providers: Optional[dict[str, PushProvider]] = None,
    ) -> None:
        self.db = db
        self.repository = NotificationRepository(db)
        self.policy = policy or NotificationPolicy()
        self.templates = templates or TemplateCatalog()
        self.reconciler = NotificationReconciler(db)
        self.push_providers = push_providers if push_providers is not None else providers_from_environment()
        self.channels = {
            DeliveryChannel.IN_APP: InAppChannel(),
            DeliveryChannel.LOCAL: LocalScheduleChannel(),
            DeliveryChannel.PUSH: PushIntentChannel(),
        }
        self.logger = logging.getLogger("coachai.notifications")

    @staticmethod
    def _event_from_row(row: dict) -> DomainEvent:
        payload = json.loads(row["event_payload"] or "{}")
        return DomainEvent.create(
            event_id=str(row["outbox_id"]),
            user_id=str(row["user_id"]),
            event_type=str(row["event_type"]),
            payload=payload,
            task_id=row.get("task_id"),
            segment_id=row.get("segment_id"),
            scheduled_date=row.get("scheduled_date"),
            segment_index=row.get("segment_index"),
            schedule_revision=row.get("schedule_revision"),
            schedule_operation_id=row.get("schedule_operation_id"),
            idempotency_key=str(row["idempotency_key"]),
            occurred_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def process_event(
        self,
        event: DomainEvent,
        *,
        app_is_open: bool = False,
        now_utc: Optional[datetime] = None,
    ) -> dict:
        preferences = self.repository.get_preferences(event.user_id)
        initial = self.policy.decide(
            event, preferences, None, now_utc=now_utc, app_is_open=app_is_open,
        )
        type_preference = self.repository.get_type_preference(
            event.user_id, initial.notification_type,
        )
        cooldown_seconds = int(
            type_preference.get("cooldown_seconds") or 0
        ) if type_preference else 0
        cooldown = self.repository.cooldown_active(
            event.user_id,
            initial.notification_type,
            cooldown_seconds,
            task_id=event.task_id,
            now=now_utc,
        )
        decision = self.policy.decide(
            event,
            preferences,
            type_preference,
            now_utc=now_utc,
            app_is_open=app_is_open,
            cooldown_active=cooldown,
        )
        recent = self.repository.recent_template_ids(
            event.user_id, decision.notification_type,
        )
        rendered = self.templates.select(
            decision.template_key,
            locale=str(preferences.get("locale") or "en"),
            tone=Tone(str(preferences.get("tone") or Tone.NEUTRAL.value)),
            context=decision.context,
            recent_template_ids=recent,
            seed=event.idempotency_key,
        )
        notification, created = self.repository.create_notification(
            event,
            decision,
            title=rendered.title,
            body=rendered.body,
            locale=rendered.locale,
            template_id=rendered.template_id,
            template_version=rendered.template_version,
            now=now_utc,
        )
        if created and decision.should_deliver and notification["status"] == "active":
            channel = self.channels[decision.channel]
            targets = (
                [None] if decision.channel is DeliveryChannel.IN_APP
                else self.repository.delivery_targets(event.user_id, channel=decision.channel)
            )
            for device_id in targets:
                initial_status = "delivered" if decision.channel is DeliveryChannel.IN_APP else "queued"
                delivery, _ = self.repository.create_delivery(
                    event.user_id, notification["notification_id"], decision.channel,
                    device_id=device_id, status=initial_status,
                )
                if decision.channel is DeliveryChannel.LOCAL:
                    self.repository.create_local_schedule(
                        event.user_id, notification["notification_id"], device_id=device_id,
                    )
                channel.deliver(notification, delivery)
            self.logger.info(
                "notification_created event_id=%s notification_id=%s user_id=%s channel=%s",
                event.event_id, notification["notification_id"], event.user_id, decision.channel.value,
            )
        elif created:
            self.logger.info(
                "notification_suppressed event_id=%s notification_id=%s user_id=%s reason=%s",
                event.event_id, notification["notification_id"], event.user_id,
                notification.get("suppression_reason") or notification.get("status"),
            )
        return notification

    def process_outbox(
        self, *, limit: int = 50, now_utc: Optional[datetime] = None,
        worker_id: Optional[str] = None, lease_seconds: int = 300, max_attempts: int = 5,
        foreground_user_ids: Optional[set[str]] = None,
    ) -> dict:
        owner = worker_id or f"worker-{uuid.uuid4().hex}"
        processed = 0
        failed = 0
        expired = 0
        claimed = self.repository.claim_outbox(
            limit=limit, now=now_utc, stale_after_seconds=lease_seconds,
            worker_id=owner, max_attempts=max_attempts,
        )
        for row in claimed:
            self.logger.info(
                "notification_event_claimed outbox_id=%s user_id=%s worker_id=%s attempt=%s",
                row["outbox_id"], row["user_id"], owner, row["attempts"],
            )
            try:
                with self.db.transaction():
                    event = self._event_from_row(row)
                    expiry = row.get("expires_at") or event.payload.get("expires_at")
                    if expiry and datetime.fromisoformat(str(expiry)).replace(tzinfo=None) <= (now_utc or utc_now_naive()).replace(tzinfo=None):
                        self.repository.mark_outbox_expired(row["outbox_id"], row["user_id"], worker_id=owner)
                        expired += 1
                        self.logger.info("notification_expired outbox_id=%s user_id=%s", row["outbox_id"], row["user_id"])
                        continue
                    if event.event_type is NotificationEventType.TASK_REMINDER_DUE and not self.reconciler.reminder_is_current(event, now_utc=now_utc):
                        self.repository.mark_outbox_expired(row["outbox_id"], row["user_id"], worker_id=owner)
                        expired += 1
                        self.logger.info("notification_cancelled outbox_id=%s reason=stale_schedule", row["outbox_id"])
                        continue
                    if event.event_type in {
                        NotificationEventType.SCHEDULE_OPERATION_APPLIED,
                        NotificationEventType.SCHEDULE_OPERATION_UNDONE,
                    }:
                        self.reconciler.reconcile_schedule_notifications(
                            event.user_id,
                            list(event.payload.get("adjusted_task_ids") or []) + list(event.payload.get("unplaced_task_ids") or []),
                            operation_id=event.schedule_operation_id, now_utc=now_utc,
                        )
                    if event.event_type in {
                        NotificationEventType.TASK_STARTED, NotificationEventType.TASK_COMPLETED,
                        NotificationEventType.TASK_FAILED, NotificationEventType.TASK_DELETED,
                        NotificationEventType.TASK_DEFERRED,
                    } and event.task_id is not None:
                        self.reconciler.cancel_task_notifications(
                            event.user_id, event.task_id, reason=event.event_type.value.lower(),
                        )
                    self.process_event(
                        event, now_utc=now_utc,
                        app_is_open=event.user_id in (foreground_user_ids or set()),
                    )
                    self.repository.mark_outbox_processed(
                        row["outbox_id"], row["user_id"], worker_id=owner,
                    )
                processed += 1
                self.logger.info("notification_event_processed outbox_id=%s user_id=%s", row["outbox_id"], row["user_id"])
            except Exception as exc:
                self.db.connection.rollback()
                permanent = isinstance(exc, (ValueError, OwnershipError, json.JSONDecodeError))
                self.repository.mark_outbox_failed(
                    row["outbox_id"], row["user_id"], str(exc), now=now_utc,
                    worker_id=owner, permanent=permanent,
                    max_attempts=max_attempts,
                )
                failed += 1
                self.logger.warning(
                    "%s outbox_id=%s user_id=%s worker_id=%s error_type=%s",
                    "notification_event_permanent_failure" if permanent or int(row["attempts"]) >= max_attempts else "notification_event_retry",
                    row["outbox_id"], row["user_id"], owner, type(exc).__name__,
                )
        self.repository.expire_due(now=now_utc)
        self.repository.expire_local_schedules(now=now_utc)
        result = {"processed": processed, "failed": failed}
        if expired:
            result["expired"] = expired
        return result

    def transition_deadline_risk(
        self,
        user_id: str,
        task_id: int,
        new_state: RiskState | str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        state = RiskState(new_state)
        task = self.repository.ensure_task(user_id, task_id)
        with self.db.transaction():
            current = self.repository.get_task_notification_state(user_id, task_id)
            previous = RiskState(current["state"]) if current else RiskState.SAFE
            changed = previous is not state
            self.repository.set_task_notification_state_no_commit(
                user_id, task_id, state, transitioned=changed,
            )
            if not changed:
                return False
            event_type = (
                NotificationEventType.DEADLINE_PASSED
                if state is RiskState.PASSED
                else NotificationEventType.DEADLINE_RISK_CHANGED
            )
            payload = dict(context or {})
            payload.update({
                "previous_state": previous.value,
                "state": state.value,
                "task_title": str(task["title"]),
            })
            event = DomainEvent.create(
                user_id=user_id,
                event_type=event_type,
                task_id=task_id,
                payload=payload,
            )
            self.repository.enqueue_event(event)
        return True

    def list_in_app(self, user_id: str, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
        return self.repository.list_notifications(
            user_id, unread_only=unread_only, limit=limit,
        )

    def unread_count(self, user_id: str) -> int:
        return self.repository.unread_count(user_id)

    def mark_read(self, user_id: str, notification_id: str) -> None:
        self.repository.mark_read(user_id, notification_id)

    def mark_acted(self, user_id: str, notification_id: str) -> None:
        self.repository.mark_acted(user_id, notification_id)

    # Framework-independent API surface for a future HTTP layer.
    def process_pending_notifications(self, **kwargs: Any) -> dict:
        return self.process_outbox(**kwargs)

    def _push_is_actionable(self, row: dict, *, now_utc: Optional[datetime] = None) -> bool:
        now = (now_utc or utc_now_naive()).replace(tzinfo=None)
        if row.get("notification_status") != "active":
            return False
        if datetime.fromisoformat(str(row["expires_at"])).replace(tzinfo=None) <= now:
            return False
        task_id = row.get("task_id")
        if task_id is None:
            return True
        task = self.db.fetch_one(
            """SELECT t.status,p.schedule_revision FROM tasks t
               JOIN plans p ON p.plan_id=t.plan_id
               WHERE t.task_id=? AND p.user_id=?""",
            (int(task_id), row["user_id"]),
        )
        if task is None or task["status"] in {"completed", "failed"}:
            return False
        revision = row.get("schedule_revision")
        return revision is None or int(revision) == int(task["schedule_revision"] or 0)

    def process_push_deliveries(
        self, *, limit: int = 100, now_utc: Optional[datetime] = None,
        worker_id: Optional[str] = None, lease_seconds: int = 300, max_attempts: int = 5,
    ) -> dict:
        owner = worker_id or f"push-{uuid.uuid4().hex}"
        summary = {
            "attempted": 0, "accepted": 0, "retrying": 0, "failed": 0,
            "invalid_tokens": 0, "cancelled": 0, "provider_latency_ms": 0.0,
        }
        rows = self.repository.claim_push_deliveries(
            limit=limit, now=now_utc, worker_id=owner,
            stale_after_seconds=lease_seconds, max_attempts=max_attempts,
        )
        for row in rows:
            if not self._push_is_actionable(row, now_utc=now_utc):
                with self.db.transaction():
                    self.repository.cancel_push_delivery(
                        row["user_id"], row["delivery_id"], worker_id=owner,
                        reason="notification_not_actionable",
                    )
                summary["cancelled"] += 1
                continue
            provider_name = str(row.get("provider") or "")
            provider = self.push_providers.get(provider_name)
            notification = {
                key: row.get(key) for key in (
                    "notification_id", "notification_type", "task_id", "segment_id",
                    "segment_index", "schedule_revision", "schedule_operation_id", "action_metadata",
                )
            }
            message = PushMessage(
                token=str(row["push_token"]), title=str(row["title"]), body=str(row["body"]),
                data=build_push_payload(notification),
                notification_id=str(row["notification_id"]), delivery_id=str(row["delivery_id"]),
                app_identifier=row.get("app_identifier"),
            )
            started = perf_counter()
            try:
                result = provider.send(message) if provider is not None else ProviderResult(
                    False, classification=ErrorClassification.CONFIGURATION,
                    error_code=f"{provider_name or 'push'}_provider_disabled",
                )
            except Exception as exc:
                result = ProviderResult(
                    False, classification=ErrorClassification.UNKNOWN,
                    error_code=type(exc).__name__,
                )
            latency_ms = (perf_counter() - started) * 1000
            summary["attempted"] += 1
            summary["provider_latency_ms"] += latency_ms
            with self.db.transaction():
                if result.accepted:
                    self.repository.mark_push_accepted(
                        row["user_id"], row["delivery_id"], worker_id=owner,
                        provider_message_id=result.provider_message_id, provider=provider_name,
                    )
                    summary["accepted"] += 1
                else:
                    classification = result.classification or ErrorClassification.UNKNOWN
                    outcome = self.repository.mark_push_failed(
                        row["user_id"], row["delivery_id"], worker_id=owner,
                        classification=classification.value,
                        error_code=result.error_code or "provider_error", now=now_utc,
                        retry_after_seconds=result.retry_after_seconds,
                        max_attempts=max_attempts,
                    )
                    if classification is ErrorClassification.PERMANENT_TOKEN:
                        summary["invalid_tokens"] += 1
                    summary["retrying" if outcome["retrying"] else "failed"] += 1
            self.logger.info(
                "push_delivery_result notification_id=%s delivery_id=%s user_id=%s provider=%s "
                "platform=%s attempt=%s accepted=%s classification=%s latency_ms=%.2f",
                row["notification_id"], row["delivery_id"], row["user_id"],
                provider_name, row.get("platform"), row["retry_count"], result.accepted,
                result.classification.value if result.classification else "accepted", latency_ms,
            )
        summary["provider_latency_ms"] = round(summary["provider_latency_ms"], 3)
        return summary

    def get_user_notifications(self, user_id: str, **kwargs: Any) -> list[dict]:
        return self.list_in_app(user_id, **kwargs)

    def mark_notification_read(self, user_id: str, notification_id: str) -> None:
        self.mark_read(user_id, notification_id)

    def mark_notification_acted(self, user_id: str, notification_id: str) -> None:
        self.mark_acted(user_id, notification_id)

    def get_notification_sync_state(self, user_id: str) -> dict:
        return {"user_id": user_id, "revision": self.repository.get_sync_revision(user_id)}

    def sync_notifications_since_revision(self, user_id: str, revision: int, *, limit: int = 500) -> dict:
        return self.repository.sync_since(user_id, revision, limit=limit)

    def reconcile_task_notifications(self, user_id: str, task_id: int, **kwargs: Any) -> dict:
        with self.db.transaction():
            return self.reconciler.reconcile_task_notifications(user_id, task_id, **kwargs)

    def reconcile_schedule_notifications(self, user_id: str, task_ids: list[int], **kwargs: Any) -> dict:
        with self.db.transaction():
            return self.reconciler.reconcile_schedule_notifications(user_id, task_ids, **kwargs)

    def cancel_task_notifications(self, user_id: str, task_id: int, *, reason: str = "cancelled") -> int:
        with self.db.transaction():
            return self.reconciler.cancel_task_notifications(user_id, task_id, reason=reason)

    def schedule_local_notifications(self, user_id: str, task_id: int, **kwargs: Any) -> dict:
        return self.reconcile_task_notifications(user_id, task_id, **kwargs)

    def generate_daily_summary(
        self, user_id: str, summary_date: date, *, now_utc: Optional[datetime] = None,
    ) -> bool:
        preferences = self.repository.get_preferences(user_id)
        if not bool(preferences.get("daily_summary_enabled")):
            return False
        counts = self.db.fetch_one(
            """SELECT COUNT(*) AS total_count,
                      SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS completed_count
               FROM tasks t JOIN plans p ON p.plan_id=t.plan_id
               WHERE p.user_id=? AND p.plan_date=?""",
            (user_id, summary_date.isoformat()),
        )
        total = int(counts["total_count"] or 0)
        if total == 0:
            return False
        event = DomainEvent.create(
            user_id=user_id, event_type=NotificationEventType.DAILY_SUMMARY_READY,
            scheduled_date=summary_date,
            idempotency_key=f"daily-summary:{summary_date.isoformat()}",
            occurred_at=now_utc,
            payload={
                "total_count": total,
                "completed_count": int(counts["completed_count"] or 0),
            },
        )
        _, created, _ = self.repository.enqueue_event(event)
        self.db.commit()
        return created
