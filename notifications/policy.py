"""Provider-independent notification routing and anti-fatigue policy."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional

from timezone_utils import safe_zoneinfo

from .domain import (
    DeliveryChannel,
    DomainEvent,
    InterruptionLevel,
    NotificationDecision,
    NotificationEventType,
    NotificationType,
    RiskState,
    Urgency,
    utc_now_naive,
)


def _parse_time(value: Any) -> Optional[time]:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(microsecond=0)
    return time.fromisoformat(str(value)).replace(microsecond=0)


def _valid_local(naive: datetime, zone, fold: int = 0) -> datetime | None:
    aware = naive.replace(tzinfo=zone, fold=fold)
    round_trip = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    return aware if round_trip == naive else None


def quiet_hours_end_utc(
    now_utc: datetime,
    timezone_name: str,
    quiet_start: Any,
    quiet_end: Any,
) -> datetime | None:
    """Return the UTC instant when current quiet hours end, or ``None``.

    Nonexistent DST wall times are advanced to the first valid minute. For an
    ambiguous fold, the later occurrence is used so quiet hours never end
    earlier than the user intended.
    """
    start, end = _parse_time(quiet_start), _parse_time(quiet_end)
    if start is None or end is None or start == end:
        return None
    zone = safe_zoneinfo(timezone_name)
    aware_utc = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc.astimezone(timezone.utc)
    local = aware_utc.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    wraps = start > end
    inside = current >= start or current < end if wraps else start <= current < end
    if not inside:
        return None
    end_date = local.date()
    if wraps and current >= start:
        end_date += timedelta(days=1)
    candidate = datetime.combine(end_date, end)
    for _ in range(181):
        first = _valid_local(candidate, zone, fold=0)
        second = _valid_local(candidate, zone, fold=1)
        aware = second or first
        if aware is not None:
            return aware.astimezone(timezone.utc).replace(tzinfo=None)
        candidate += timedelta(minutes=1)
    raise ValueError("Could not resolve the end of quiet hours in the user's timezone.")


class NotificationPolicy:
    """Convert a durable domain event into one auditable decision."""

    _DEFAULTS: dict[NotificationEventType, tuple] = {
        NotificationEventType.TASK_REMINDER_DUE: (
            NotificationType.TASK_REMINDER, Urgency.NORMAL,
            InterruptionLevel.PASSIVE, DeliveryChannel.LOCAL,
            "task_reminder", timedelta(hours=2),
        ),
        NotificationEventType.SCHEDULE_OPERATION_APPLIED: (
            NotificationType.SCHEDULE_CHANGE, Urgency.NORMAL,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "schedule_updated", timedelta(hours=24),
        ),
        NotificationEventType.SCHEDULE_OPERATION_UNDONE: (
            NotificationType.SCHEDULE_CHANGE, Urgency.NORMAL,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "schedule_undone", timedelta(hours=24),
        ),
        NotificationEventType.TASK_UNPLACED: (
            NotificationType.TASK_UNPLACED, Urgency.HIGH,
            InterruptionLevel.ACTIVE, DeliveryChannel.IN_APP,
            "task_unplaced", timedelta(hours=48),
        ),
        NotificationEventType.DEADLINE_RISK_CHANGED: (
            NotificationType.DEADLINE_RISK, Urgency.HIGH,
            InterruptionLevel.ACTIVE, DeliveryChannel.IN_APP,
            "deadline_risk", timedelta(hours=12),
        ),
        NotificationEventType.DEADLINE_PASSED: (
            NotificationType.DEADLINE_RISK, Urgency.HIGH,
            InterruptionLevel.ACTIVE, DeliveryChannel.IN_APP,
            "deadline_passed", timedelta(hours=24),
        ),
        NotificationEventType.CALENDAR_ATTENTION_REQUIRED: (
            NotificationType.CALENDAR_ISSUE, Urgency.HIGH,
            InterruptionLevel.ACTIVE, DeliveryChannel.IN_APP,
            "calendar_attention", timedelta(hours=24),
        ),
        NotificationEventType.CALENDAR_RECONCILED: (
            NotificationType.CALENDAR_ISSUE, Urgency.LOW,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "calendar_reconciled", timedelta(hours=12),
        ),
        NotificationEventType.DAILY_SUMMARY_READY: (
            NotificationType.DAILY_SUMMARY, Urgency.LOW,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "daily_summary", timedelta(hours=12),
        ),
        NotificationEventType.FOCUS_SESSION_STARTED: (
            NotificationType.COACH_CHECK_IN, Urgency.LOW,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "focus_started", timedelta(hours=4),
        ),
        NotificationEventType.FOCUS_SESSION_ENDED: (
            NotificationType.COACH_CHECK_IN, Urgency.LOW,
            InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
            "focus_ended", timedelta(hours=4),
        ),
    }

    _LIFECYCLE_TEMPLATE = {
        NotificationEventType.TASK_CREATED: "task_created",
        NotificationEventType.TASK_UPDATED: "task_updated",
        NotificationEventType.TASK_STARTED: "task_started",
        NotificationEventType.TASK_COMPLETED: "task_completed",
        NotificationEventType.TASK_FAILED: "task_failed",
        NotificationEventType.TASK_DELETED: "task_deleted",
        NotificationEventType.TASK_DEFERRED: "task_deferred",
    }

    def decide(
        self,
        event: DomainEvent,
        preferences: Mapping[str, Any],
        type_preference: Mapping[str, Any] | None = None,
        *,
        now_utc: datetime | None = None,
        app_is_open: bool = False,
        cooldown_active: bool = False,
    ) -> NotificationDecision:
        now = (now_utc or utc_now_naive()).replace(tzinfo=None, microsecond=0)
        override = dict(type_preference or {})
        suppression: str | None = None

        if event.event_type in self._LIFECYCLE_TEMPLATE:
            values = (
                NotificationType.TASK_LIFECYCLE, Urgency.LOW,
                InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
                self._LIFECYCLE_TEMPLATE[event.event_type], timedelta(hours=6),
            )
            suppression = "lifecycle_state_only"
        else:
            values = self._DEFAULTS.get(event.event_type)
            if values is None:
                values = (
                    NotificationType.TASK_LIFECYCLE, Urgency.LOW,
                    InterruptionLevel.PASSIVE, DeliveryChannel.IN_APP,
                    "task_updated", timedelta(hours=6),
                )
                suppression = "no_matching_policy"

        notification_type, urgency, interruption, channel, template_key, ttl = values
        if override:
            if not bool(override.get("enabled", 1)):
                suppression = "disabled_by_user"
            if override.get("preferred_channel"):
                channel = DeliveryChannel(str(override["preferred_channel"]))
            if override.get("interruption_level"):
                interruption = InterruptionLevel(str(override["interruption_level"]))

        if event.event_type in {
            NotificationEventType.FOCUS_SESSION_STARTED,
            NotificationEventType.FOCUS_SESSION_ENDED,
        } and not bool(preferences.get("coach_checkins_enabled", 0)):
            suppression = "coach_checkins_disabled"
        if event.event_type is NotificationEventType.DAILY_SUMMARY_READY and not bool(
            preferences.get("daily_summary_enabled", 0)
        ):
            suppression = "daily_summary_disabled"
        if event.event_type is NotificationEventType.DEADLINE_RISK_CHANGED:
            state = str(event.payload.get("state") or "")
            if state == RiskState.SAFE.value:
                suppression = "risk_resolved_in_app_state_only"
        if app_is_open and channel is not DeliveryChannel.IN_APP:
            channel = DeliveryChannel.IN_APP
        if cooldown_active and suppression is None:
            suppression = "cooldown_active"

        explicit_schedule = event.payload.get("scheduled_for")
        scheduled_for = (
            datetime.fromisoformat(str(explicit_schedule)).replace(tzinfo=None)
            if explicit_schedule
            else now
        )
        quiet_end = quiet_hours_end_utc(
            now,
            str(preferences.get("timezone") or "UTC"),
            preferences.get("quiet_hours_start"),
            preferences.get("quiet_hours_end"),
        )
        if quiet_end is not None and urgency in {Urgency.NORMAL, Urgency.LOW}:
            scheduled_for = max(scheduled_for, quiet_end)

        explicit_expiry = event.payload.get("expires_at")
        expires_at = (
            datetime.fromisoformat(str(explicit_expiry)).replace(tzinfo=None)
            if explicit_expiry
            else max(now, scheduled_for) + ttl
        )
        if scheduled_for >= expires_at and suppression is None:
            suppression = "expired_before_delivery"
        dedupe_key = str(
            event.payload.get("dedupe_key")
            or f"notification:{event.idempotency_key}"
        )
        context = dict(event.payload)
        context.setdefault("task_title", "Task")
        context.setdefault("adjusted_count", 0)
        context.setdefault("reason", "the current constraints")
        context.setdefault("completed_count", 0)
        context.setdefault("total_count", 0)
        context.setdefault("focused_duration", "0m")
        return NotificationDecision(
            notification_type=notification_type,
            urgency=urgency,
            interruption_level=interruption,
            channel=channel,
            template_key=template_key,
            context=context,
            dedupe_key=dedupe_key,
            scheduled_for=scheduled_for,
            expires_at=expires_at,
            suppression_reason=suppression,
            action_metadata=dict(event.payload.get("actions") or {}),
        )
