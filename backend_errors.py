"""Stable backend errors shared by every non-HTTP application surface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ErrorCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SCHEDULING_CONFLICT = "SCHEDULING_CONFLICT"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    EXTERNAL_PROVIDER_FAILURE = "EXTERNAL_PROVIDER_FAILURE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


@dataclass(eq=False)
class BackendError(Exception):
    """A safe, transport-neutral error. ``details`` must never contain secrets."""

    code: ErrorCode
    message: str
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class AuthenticationRequired(BackendError):
    def __init__(self, message: str = "Authentication is required.") -> None:
        super().__init__(ErrorCode.AUTHENTICATION_REQUIRED, message)


class AuthorizationDenied(BackendError):
    def __init__(self, message: str = "The operation is not permitted.") -> None:
        super().__init__(ErrorCode.AUTHORIZATION_DENIED, message)


class ResourceNotFound(BackendError):
    def __init__(self, message: str = "The requested resource was not found.") -> None:
        super().__init__(ErrorCode.RESOURCE_NOT_FOUND, message)


class TenantMismatch(BackendError):
    def __init__(self, message: str = "The referenced resource belongs to another account.") -> None:
        super().__init__(ErrorCode.TENANT_MISMATCH, message)


class InvalidStateTransition(BackendError):
    def __init__(self, message: str, *, current: str, requested: str) -> None:
        super().__init__(
            ErrorCode.INVALID_STATE_TRANSITION,
            message,
            {"current": current, "requested": requested},
        )


class VersionConflict(BackendError):
    def __init__(self, message: str = "The resource changed before the operation was applied.") -> None:
        super().__init__(ErrorCode.VERSION_CONFLICT, message)


class IdempotencyConflict(BackendError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "This idempotency key was already used with a different request.",
        )


class SchedulingConflict(BackendError):
    def __init__(self, message: str = "The schedule changed or cannot be applied safely.") -> None:
        super().__init__(ErrorCode.SCHEDULING_CONFLICT, message)


class CapacityExceeded(BackendError):
    def __init__(self, message: str = "The requested work exceeds available capacity.") -> None:
        super().__init__(ErrorCode.CAPACITY_EXCEEDED, message)


class RateLimited(BackendError):
    def __init__(
        self,
        *,
        retry_after_seconds: float,
        message: str = "Too many requests were submitted. Please retry later.",
    ) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            message,
            {"retry_after_seconds": max(0, int(round(retry_after_seconds)))},
        )


class ExternalProviderFailure(BackendError):
    def __init__(self, message: str = "An external provider operation failed.") -> None:
        super().__init__(ErrorCode.EXTERNAL_PROVIDER_FAILURE, message)


class ValidationError(BackendError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.VALIDATION_ERROR, message)


class ConfigurationError(BackendError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CONFIGURATION_ERROR, message)
