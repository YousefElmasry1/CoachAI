"""Typed, validated backend configuration without framework dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from backend_errors import ConfigurationError


def cors_allowed_origins(value: str | None = None) -> tuple[str, ...]:
    """Return a validated exact browser-origin allowlist."""

    raw = os.getenv("COACHAI_CORS_ALLOWED_ORIGINS", "") if value is None else value
    origins: list[str] = []
    for candidate in raw.split(","):
        origin = candidate.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            "*" in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(
                "COACHAI_CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins."
            )
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


def _int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{name} must be at most {maximum}.")
    return value


@dataclass(frozen=True, slots=True)
class BackendSettings:
    database_url: str
    firebase_project_id: str | None
    token_encryption_key: str | None
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800
    external_timeout_seconds: int = 20
    max_task_title_chars: int = 200
    max_task_description_chars: int = 4000
    max_ai_tasks: int = 50
    max_task_minutes: int = 16 * 60
    max_audio_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_env(
        cls,
        *,
        require_mobile_security: bool = False,
        database_url: str | None = None,
    ) -> "BackendSettings":
        selected_database_url = (database_url or os.getenv("DATABASE_URL") or "").strip()
        if not selected_database_url:
            raise ConfigurationError("DATABASE_URL is required.")
        firebase_project_id = (os.getenv("FIREBASE_PROJECT_ID") or "").strip() or None
        token_key = (os.getenv("COACHAI_TOKEN_ENCRYPTION_KEY") or "").strip() or None
        if require_mobile_security and not firebase_project_id:
            raise ConfigurationError("FIREBASE_PROJECT_ID is required for mobile authentication.")
        if require_mobile_security and not token_key:
            raise ConfigurationError("COACHAI_TOKEN_ENCRYPTION_KEY is required for token protection.")
        return cls(
            database_url=selected_database_url,
            firebase_project_id=firebase_project_id,
            token_encryption_key=token_key,
            db_pool_size=_int("COACHAI_DB_POOL_SIZE", 5, maximum=100),
            db_max_overflow=_int(
                "COACHAI_DB_MAX_OVERFLOW", 10, minimum=0, maximum=200
            ),
            db_pool_timeout_seconds=_int(
                "COACHAI_DB_POOL_TIMEOUT_SECONDS", 30, maximum=300
            ),
            db_pool_recycle_seconds=_int(
                "COACHAI_DB_POOL_RECYCLE_SECONDS", 1800, maximum=86400
            ),
            external_timeout_seconds=_int(
                "COACHAI_EXTERNAL_TIMEOUT_SECONDS", 20, maximum=120
            ),
            max_task_title_chars=_int(
                "COACHAI_MAX_TASK_TITLE_CHARS", 200, maximum=1000
            ),
            max_task_description_chars=_int(
                "COACHAI_MAX_TASK_DESCRIPTION_CHARS", 4000, maximum=20000
            ),
            max_ai_tasks=_int("COACHAI_MAX_AI_TASKS", 50, maximum=100),
            max_task_minutes=_int(
                "COACHAI_MAX_TASK_MINUTES", 16 * 60, maximum=7 * 24 * 60
            ),
            max_audio_bytes=_int(
                "COACHAI_MAX_AUDIO_BYTES", 15 * 1024 * 1024,
                maximum=50 * 1024 * 1024,
            ),
        )
