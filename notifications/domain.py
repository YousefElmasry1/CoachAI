"""Notification domain types with no UI or provider dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
import uuid


class NotificationEventType(str, Enum):
    TASK_REMINDER_DUE = "TASK_REMINDER_DUE"
    TASK_CREATED = "TASK_CREATED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_DELETED = "TASK_DELETED"
    TASK_DEFERRED = "TASK_DEFERRED"
    SCHEDULE_OPERATION_APPLIED = "SCHEDULE_OPERATION_APPLIED"
    SCHEDULE_OPERATION_UNDONE = "SCHEDULE_OPERATION_UNDONE"
    TASK_UNPLACED = "TASK_UNPLACED"
    DEADLINE_RISK_CHANGED = "DEADLINE_RISK_CHANGED"
    DEADLINE_PASSED = "DEADLINE_PASSED"
    FOCUS_SESSION_STARTED = "FOCUS_SESSION_STARTED"
    FOCUS_SESSION_ENDED = "FOCUS_SESSION_ENDED"
    CALENDAR_ATTENTION_REQUIRED = "CALENDAR_ATTENTION_REQUIRED"
    CALENDAR_RECONCILED = "CALENDAR_RECONCILED"
    DAILY_SUMMARY_READY = "DAILY_SUMMARY_READY"


class NotificationType(str, Enum):
    TASK_REMINDER = "task_reminder"
    TASK_LIFECYCLE = "task_lifecycle"
    SCHEDULE_CHANGE = "schedule_change"
    TASK_UNPLACED = "task_unplaced"
    DEADLINE_RISK = "deadline_risk"
    COACH_CHECK_IN = "coach_check_in"
    CALENDAR_ISSUE = "calendar_issue"
    DAILY_SUMMARY = "daily_summary"


class DeliveryChannel(str, Enum):
    IN_APP = "in_app"
    LOCAL = "local"
    PUSH = "push"


class Urgency(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class InterruptionLevel(str, Enum):
    TIME_SENSITIVE = "time_sensitive"
    ACTIVE = "active"
    PASSIVE = "passive"


class Tone(str, Enum):
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    COACH = "coach"


class RiskState(str, Enum):
    SAFE = "SAFE"
    AT_RISK = "AT_RISK"
    CRITICAL = "CRITICAL"
    PASSED = "PASSED"


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    user_id: str
    event_type: NotificationEventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    task_id: Optional[int] = None
    segment_id: Optional[int] = None
    scheduled_date: Optional[date] = None
    segment_index: Optional[int] = None
    schedule_revision: Optional[int] = None
    schedule_operation_id: Optional[str] = None
    idempotency_key: str = ""
    occurred_at: datetime = field(default_factory=utc_now_naive)

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        event_type: NotificationEventType | str,
        payload: Optional[Mapping[str, Any]] = None,
        task_id: Optional[int] = None,
        segment_id: Optional[int] = None,
        scheduled_date: Optional[date | str] = None,
        segment_index: Optional[int] = None,
        schedule_revision: Optional[int] = None,
        schedule_operation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        event_id: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
    ) -> "DomainEvent":
        parsed_type = NotificationEventType(event_type)
        parsed_date = (
            date.fromisoformat(scheduled_date)
            if isinstance(scheduled_date, str)
            else scheduled_date
        )
        generated_id = event_id or uuid.uuid4().hex
        return cls(
            event_id=generated_id,
            user_id=str(user_id),
            event_type=parsed_type,
            payload=dict(payload or {}),
            task_id=int(task_id) if task_id is not None else None,
            segment_id=int(segment_id) if segment_id is not None else None,
            scheduled_date=parsed_date,
            segment_index=int(segment_index) if segment_index is not None else None,
            schedule_revision=(
                int(schedule_revision) if schedule_revision is not None else None
            ),
            schedule_operation_id=(
                str(schedule_operation_id) if schedule_operation_id else None
            ),
            idempotency_key=idempotency_key or f"event:{generated_id}",
            occurred_at=(occurred_at or utc_now_naive()).replace(tzinfo=None),
        )


@dataclass(frozen=True)
class NotificationDecision:
    notification_type: NotificationType
    urgency: Urgency
    interruption_level: InterruptionLevel
    channel: DeliveryChannel
    template_key: str
    context: Mapping[str, Any]
    dedupe_key: str
    scheduled_for: datetime
    expires_at: datetime
    suppression_reason: Optional[str] = None
    action_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def should_deliver(self) -> bool:
        return self.suppression_reason is None
