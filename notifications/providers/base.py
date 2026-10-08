"""Provider-neutral push contracts and stable payload construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Mapping, Protocol


class ErrorClassification(str, Enum):
    TRANSIENT = "transient"
    PERMANENT_TOKEN = "permanent_token"
    PERMANENT_REQUEST = "permanent_request"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PushMessage:
    token: str
    title: str
    body: str
    data: Mapping[str, str]
    notification_id: str
    delivery_id: str
    app_identifier: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    accepted: bool
    provider_message_id: str | None = None
    classification: ErrorClassification | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class PushProvider(Protocol):
    name: str

    def send(self, message: PushMessage) -> ProviderResult:
        ...


def _action(metadata: Mapping[str, Any]) -> str:
    explicit = str(metadata.get("action") or "").upper()
    if explicit in {"START", "COMPLETE", "SNOOZE", "REPLAN", "OPEN"}:
        return explicit
    if metadata.get("start_task"):
        return "START"
    if metadata.get("complete_task"):
        return "COMPLETE"
    if metadata.get("snooze"):
        return "SNOOZE"
    if metadata.get("replan") or metadata.get("undo") or metadata.get("view_changes"):
        return "REPLAN"
    return "OPEN"


def build_push_payload(notification: Mapping[str, Any]) -> dict[str, str]:
    """Build the minimal versioned payload consumed by a future mobile app."""
    raw_actions = notification.get("action_metadata") or "{}"
    actions = json.loads(raw_actions) if isinstance(raw_actions, str) else dict(raw_actions)
    values = {
        "payload_version": "1",
        "type": str(notification["notification_type"]),
        "notification_id": str(notification["notification_id"]),
        "task_id": notification.get("task_id"),
        "segment_id": notification.get("segment_id"),
        "segment_index": notification.get("segment_index"),
        "schedule_revision": notification.get("schedule_revision"),
        "operation_id": notification.get("schedule_operation_id"),
        "action": _action(actions),
    }
    return {key: str(value) for key, value in values.items() if value is not None}
