"""Channel-intent abstractions; provider execution is isolated in providers/."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .domain import DeliveryChannel


@dataclass(frozen=True)
class ChannelResult:
    accepted: bool
    provider_message_id: str | None = None
    error: str | None = None


class NotificationChannel(Protocol):
    channel: DeliveryChannel

    def deliver(self, notification: Mapping, delivery: Mapping) -> ChannelResult:
        ...


class InAppChannel:
    """In-app persistence is itself the delivery; no external call occurs."""

    channel = DeliveryChannel.IN_APP

    def deliver(self, notification: Mapping, delivery: Mapping) -> ChannelResult:
        return ChannelResult(accepted=True)


class IntentChannel:
    """Accept a durable local/push intent without contacting a provider."""

    def __init__(self, channel: DeliveryChannel) -> None:
        if channel is DeliveryChannel.IN_APP:
            raise ValueError("Use InAppChannel for in-app delivery.")
        self.channel = channel

    def deliver(self, notification: Mapping, delivery: Mapping) -> ChannelResult:
        return ChannelResult(accepted=True)


class LocalScheduleChannel(IntentChannel):
    def __init__(self) -> None:
        super().__init__(DeliveryChannel.LOCAL)


class PushIntentChannel(IntentChannel):
    """Persist a push intent; the leased push worker invokes its provider later."""

    def __init__(self) -> None:
        super().__init__(DeliveryChannel.PUSH)


# Kept as a compatibility alias for Patch 1 callers.
DeferredChannel = IntentChannel
