"""Framework-neutral structured logging with safe correlation context.

The formatter deliberately emits JSON without serialising arbitrary exception
tracebacks.  Tracebacks, provider responses and request objects frequently hold
credentials; callers should log a stable error code and ``exception_type``
instead.  Every value still passes through the redactor as defence in depth.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, timezone
import json
import logging
import re
import sys
from typing import Any, Iterator, TextIO
from uuid import uuid4


_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_operation_id: ContextVar[str | None] = ContextVar("operation_id", default=None)
_principal_id: ContextVar[str | None] = ContextVar("principal_id", default=None)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_URL_USERINFO = re.compile(r"(?i)(://[^\s:/@]+:)[^\s@/]+(@)")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|id_token|api_key|key|secret|signature)=)[^&#\s]+"
)
_REDACTED = "[REDACTED]"

_EXACT_SECRET_KEYS = {
    "authorization",
    "proxy_authorization",
    "token",
    "secret",
    "password",
    "password_hash",
    "passwd",
    "client_secret",
    "api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "device_token",
    "private_key",
    "credentials",
    "credential",
    "cookie",
    "set_cookie",
}

_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def _is_secret_key(key: object) -> bool:
    raw = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key).strip())
    normalised = raw.lower().replace("-", "_")
    if normalised in _EXACT_SECRET_KEYS:
        return True
    if normalised.endswith(("_password", "_secret", "_private_key", "_credentials")):
        return True
    if normalised.endswith("_token") and normalised not in {
        "input_token",
        "output_token",
    }:
        return True
    return False


def redact_text(value: str) -> str:
    """Remove common credential forms from an otherwise safe log string."""

    redacted = _BEARER.sub(lambda match: f"{match.group(1)} {_REDACTED}", value)
    redacted = _JWT.sub(_REDACTED, redacted)
    redacted = _URL_USERINFO.sub(r"\1[REDACTED]\2", redacted)
    return _QUERY_SECRET.sub(r"\1[REDACTED]", redacted)


def redact(value: Any, *, key: object | None = None) -> Any:
    """Return a JSON-safe, recursively redacted copy of ``value``."""

    if key is not None and _is_secret_key(key):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    # Never introspect arbitrary objects: __dict__ may contain credentials.
    return f"<{type(value).__name__}>"


def _validated_context_id(value: str | None, *, field: str, generate: bool) -> str | None:
    if value is None:
        return uuid4().hex if generate else None
    value = str(value)
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{field} must be 1-128 safe identifier characters.")
    return value


@contextmanager
def correlation_context(
    correlation_id: str | None = None,
    *,
    operation_id: str | None = None,
    principal_id: str | None = None,
) -> Iterator[str]:
    """Bind correlation fields for the current thread/async context.

    A correlation ID is generated when one is not supplied.  ContextVar makes
    this safe for concurrent async requests without depending on FastAPI.
    """

    correlation = _validated_context_id(
        correlation_id, field="correlation_id", generate=True
    )
    operation = _validated_context_id(operation_id, field="operation_id", generate=False)
    principal = _validated_context_id(principal_id, field="principal_id", generate=False)
    correlation_token = _correlation_id.set(correlation)
    operation_token = _operation_id.set(operation)
    principal_token = _principal_id.set(principal)
    try:
        yield correlation or ""
    finally:
        _principal_id.reset(principal_token)
        _operation_id.reset(operation_token)
        _correlation_id.reset(correlation_token)


def current_correlation() -> dict[str, str]:
    """Return only populated correlation fields."""

    values = {
        "correlation_id": _correlation_id.get(),
        "operation_id": _operation_id.get(),
        "principal_id": _principal_id.get(),
    }
    return {name: value for name, value in values.items() if value is not None}


class RedactingJsonFormatter(logging.Formatter):
    """Emit one compact, secret-redacted JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            **current_correlation(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _LOG_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["fields"] = redact(extras)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(redact(payload), separators=(",", ":"), sort_keys=True)


class RedactingFilter(logging.Filter):
    """Redact the rendered message before any formatter can observe it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = redact(record.args)
            else:
                record.args = tuple(redact(item) for item in record.args)
        return True


def configure_structured_logging(
    *,
    level: str | int = "INFO",
    stream: TextIO | None = None,
    logger: logging.Logger | None = None,
) -> logging.Logger:
    """Configure a logger with one idempotent redacting JSON handler."""

    target = logger or logging.getLogger()
    numeric_level = logging.getLevelName(str(level).upper()) if isinstance(level, str) else level
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unknown log level: {level}")
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingJsonFormatter())
    handler.addFilter(RedactingFilter())
    # Replace only handlers installed by this helper, preserving embedder-owned
    # handlers (Streamlit and process supervisors may add their own).
    for existing in tuple(target.handlers):
        if getattr(existing, "_coachai_structured", False):
            target.removeHandler(existing)
    setattr(handler, "_coachai_structured", True)
    target.addHandler(handler)
    target.setLevel(numeric_level)
    return target
