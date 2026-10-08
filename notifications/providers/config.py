"""Environment-only push provider configuration. Secrets are never logged."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(name: str, default: float) -> float:
    """Read a finite positive duration without letting bad config stop workers."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


@dataclass(frozen=True)
class FCMConfig:
    enabled: bool = False
    credentials_path: str | None = None
    project_id: str | None = None
    dry_run: bool = False
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "FCMConfig":
        return cls(
            enabled=_enabled("COACHAI_FCM_ENABLED"),
            credentials_path=os.getenv("COACHAI_FCM_CREDENTIALS") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            project_id=os.getenv("COACHAI_FCM_PROJECT_ID"),
            dry_run=_enabled("COACHAI_FCM_DRY_RUN"),
            timeout_seconds=_positive_float("COACHAI_PUSH_TIMEOUT_SECONDS", 10.0),
        )


@dataclass(frozen=True)
class APNsConfig:
    enabled: bool = False
    environment: str = "sandbox"
    key_id: str | None = None
    team_id: str | None = None
    bundle_id: str | None = None
    private_key_path: str | None = None
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "APNsConfig":
        environment = os.getenv("COACHAI_APNS_ENVIRONMENT", "sandbox").strip().lower()
        if environment not in {"sandbox", "production"}:
            environment = "sandbox"
        return cls(
            enabled=_enabled("COACHAI_APNS_ENABLED"),
            environment=environment,
            key_id=os.getenv("COACHAI_APNS_KEY_ID"),
            team_id=os.getenv("COACHAI_APNS_TEAM_ID"),
            bundle_id=os.getenv("COACHAI_APNS_BUNDLE_ID"),
            private_key_path=os.getenv("COACHAI_APNS_PRIVATE_KEY"),
            timeout_seconds=_positive_float("COACHAI_PUSH_TIMEOUT_SECONDS", 10.0),
        )
