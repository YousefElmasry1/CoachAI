"""Transport-independent security and durable workflow helpers for Calendar.

The objects in this module deliberately know nothing about Streamlit or
FastAPI.  OAuth callbacks bind to an already verified principal, only a hash
of the browser-visible state is persisted, PKCE uses S256, and each state can
be consumed exactly once.  Google credentials are protected by a versioned
Fernet envelope so encryption keys can be rotated without rewriting every
row at once.

Calendar provider mutations are represented by a small durable queue.  A
worker claims operations with a lease, records only redacted failures, and
can safely recover abandoned work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import json
import os
import re
import secrets
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from backend_errors import (
    AuthorizationDenied,
    BackendError,
    ConfigurationError,
    ErrorCode,
    IdempotencyConflict,
    ResourceNotFound,
    ValidationError,
    VersionConflict,
)
from identity import AuthenticatedPrincipal


_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_MAX_OAUTH_TTL_SECONDS = 30 * 60
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_SAFE_ERROR_CHARS = 500
_FORBIDDEN_PAYLOAD_KEYS = {
    "access_token",
    "authorization",
    "client_secret",
    "code_verifier",
    "id_token",
    "refresh_token",
}


class CalendarDatabase(Protocol):
    """The narrow database surface used by these application helpers."""

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any: ...

    def fetch_one(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> dict[str, Any] | None: ...

    def fetch_all(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]: ...

    def transaction(self) -> Any: ...


class CalendarSecurityError(BackendError):
    """A safe error for unavailable/corrupt protected Calendar data."""

    def __init__(self, message: str = "Calendar credentials are unavailable.") -> None:
        super().__init__(ErrorCode.EXTERNAL_PROVIDER_FAILURE, message)


class CalendarOperationError(BackendError):
    """A safe error for corrupt durable Calendar work."""

    def __init__(self, message: str = "The Calendar operation could not be processed.") -> None:
        super().__init__(ErrorCode.EXTERNAL_PROVIDER_FAILURE, message)


def _utc_naive(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _require_principal(principal: AuthenticatedPrincipal) -> str:
    user_id = str(getattr(principal, "user_id", "") or "").strip()
    if not user_id:
        raise AuthorizationDenied("A verified account is required for Calendar access.")
    return user_id


def _validate_identifier(value: str, *, label: str, pattern: re.Pattern[str]) -> str:
    normalized = str(value or "").strip()
    if not pattern.fullmatch(normalized):
        raise ValidationError(f"{label} is invalid.")
    return normalized


def _validate_redirect_uri(value: str) -> str:
    redirect_uri = str(value or "").strip()
    if not redirect_uri or len(redirect_uri) > 2048:
        raise ValidationError("OAuth redirect URI is invalid.")
    try:
        parsed = urlsplit(redirect_uri)
        host = (parsed.hostname or "").lower()
        is_https = parsed.scheme.lower() == "https" and bool(host)
        is_loopback_http = parsed.scheme.lower() == "http" and host in {
            "localhost", "127.0.0.1", "::1",
        }
    except ValueError as exc:
        raise ValidationError("OAuth redirect URI is invalid.") from exc
    if (
        not (is_https or is_loopback_http)
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        raise ValidationError("OAuth redirect URI is invalid.")
    return redirect_uri


def _canonical_payload(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise ValidationError("Calendar operation payload must be an object.")

    def contains_secret(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                raw_key = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key).strip())
                normalized_key = raw_key.lower().replace("-", "_")
                if (
                    normalized_key in _FORBIDDEN_PAYLOAD_KEYS
                    or normalized_key in {"password", "secret", "token"}
                    or normalized_key.endswith(
                        ("_password", "_secret", "_token", "_private_key")
                    )
                ):
                    return True
                if contains_secret(nested):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(contains_secret(item) for item in value)
        return False

    if contains_secret(payload):
        raise ValidationError("Calendar operation payload must not contain credentials.")
    try:
        encoded = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Calendar operation payload is not valid JSON.") from exc
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValidationError("Calendar operation payload is too large.")
    return encoded


def _safe_failure_message(error: BaseException | str) -> str:
    # BackendError messages are expressly designed for transport/storage.
    # Arbitrary provider exceptions may include bearer tokens or response
    # bodies, so none of their text is persisted.
    if isinstance(error, BackendError):
        message = error.message
    elif isinstance(error, str) and error in {
        "Calendar provider request timed out.",
        "Calendar provider temporarily unavailable.",
        "Calendar authorization was revoked.",
    }:
        message = error
    else:
        message = "Calendar provider operation failed."
    return message[:_MAX_SAFE_ERROR_CHARS]


@dataclass(frozen=True, slots=True)
class OAuthAuthorizationRequest:
    state: str
    code_challenge: str
    expires_at: datetime
    code_challenge_method: str = "S256"


@dataclass(frozen=True, slots=True)
class ConsumedOAuthState:
    user_id: str
    provider: str
    redirect_uri: str
    code_verifier: str


class OAuthStateStore:
    """Issues and atomically consumes principal-bound OAuth state."""

    def __init__(self, db: CalendarDatabase) -> None:
        self.db = db

    @staticmethod
    def _hash_state(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def issue(
        self,
        principal: AuthenticatedPrincipal,
        *,
        redirect_uri: str,
        provider: str = "google",
        ttl_seconds: int = 600,
        now: datetime | None = None,
    ) -> OAuthAuthorizationRequest:
        user_id = _require_principal(principal)
        provider = _validate_identifier(provider, label="OAuth provider", pattern=_PROVIDER_RE)
        redirect_uri = _validate_redirect_uri(redirect_uri)
        if not isinstance(ttl_seconds, int) or not (1 <= ttl_seconds <= _MAX_OAUTH_TTL_SECONDS):
            raise ValidationError("OAuth state lifetime must be between 1 and 1800 seconds.")

        issued_at = _utc_naive(now)
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        # token_urlsafe produces only RFC 7636 unreserved verifier characters.
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = self._pkce_challenge(verifier)
        with self.db.transaction():
            if self.db.fetch_one("SELECT user_id FROM users WHERE user_id=?", (user_id,)) is None:
                raise ResourceNotFound("The authenticated account was not found.")
            self.db.execute(
                """INSERT INTO oauth_states
                       (state_hash,user_id,provider,redirect_uri,code_verifier,expires_at,used_at,created_at)
                   VALUES (?,?,?,?,?,?,NULL,?)""",
                (
                    self._hash_state(state), user_id, provider, redirect_uri,
                    verifier, expires_at, issued_at,
                ),
            )
        return OAuthAuthorizationRequest(
            state=state,
            code_challenge=challenge,
            expires_at=expires_at.replace(tzinfo=timezone.utc),
        )

    def consume(
        self,
        principal: AuthenticatedPrincipal,
        *,
        state: str,
        redirect_uri: str,
        provider: str = "google",
        now: datetime | None = None,
    ) -> ConsumedOAuthState:
        user_id = _require_principal(principal)
        provider = _validate_identifier(provider, label="OAuth provider", pattern=_PROVIDER_RE)
        raw_state = str(state or "").strip()
        if not raw_state or len(raw_state) > 1024:
            raise AuthorizationDenied("The Calendar authorization request is invalid or expired.")
        redirect_uri = str(redirect_uri or "").strip()
        checked_at = _utc_naive(now)
        state_hash = self._hash_state(raw_state)

        with self.db.transaction():
            row = self.db.fetch_one(
                """SELECT code_verifier FROM oauth_states
                   WHERE state_hash=? AND user_id=? AND provider=? AND redirect_uri=?
                     AND used_at IS NULL""",
                (state_hash, user_id, provider, redirect_uri),
            )
            if row is None:
                raise AuthorizationDenied(
                    "The Calendar authorization request is invalid or expired."
                )
            updated = self.db.execute(
                """UPDATE oauth_states SET used_at=?
                   WHERE state_hash=? AND user_id=? AND provider=? AND redirect_uri=?
                     AND used_at IS NULL AND expires_at>?""",
                (checked_at, state_hash, user_id, provider, redirect_uri, checked_at),
            )
            if updated.rowcount != 1:
                raise AuthorizationDenied(
                    "The Calendar authorization request is invalid or expired."
                )
            verifier = str(row.get("code_verifier") or "")
            if not verifier:
                raise CalendarSecurityError("Calendar authorization data is unavailable.")
        return ConsumedOAuthState(user_id, provider, redirect_uri, verifier)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Remove expired states and old consumed records; safe for a worker."""
        checked_at = _utc_naive(now)
        consumed_before = checked_at - timedelta(hours=1)
        with self.db.transaction():
            result = self.db.execute(
                """DELETE FROM oauth_states
                   WHERE expires_at<=? OR (used_at IS NOT NULL AND used_at<=?)""",
                (checked_at, consumed_before),
            )
        return max(0, int(result.rowcount or 0))


class VersionedTokenCipher:
    """Fernet encryption with a self-describing, rotation-friendly envelope.

    Envelopes have the form ``coachai:v1:<key-id>:<fernet-token>``.  Supply
    old and new keys together while rotating, set the new key as active, and
    rewrite a value only when :meth:`needs_rotation` returns true.
    """

    PREFIX = "coachai"
    VERSION = "v1"

    def __init__(
        self,
        keys: Mapping[str, str | bytes],
        *,
        active_key_id: str,
    ) -> None:
        if not keys:
            raise ConfigurationError("At least one Calendar encryption key is required.")
        if not _KEY_ID_RE.fullmatch(active_key_id or ""):
            raise ConfigurationError("The active Calendar encryption key ID is invalid.")
        configured: dict[str, Fernet] = {}
        for key_id, key in keys.items():
            if not _KEY_ID_RE.fullmatch(str(key_id)):
                raise ConfigurationError("A Calendar encryption key ID is invalid.")
            raw_key = key.encode("ascii") if isinstance(key, str) else key
            try:
                configured[str(key_id)] = Fernet(raw_key)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError("A Calendar encryption key is invalid.") from exc
        if active_key_id not in configured:
            raise ConfigurationError("The active Calendar encryption key is not configured.")
        self._keys = configured
        self.active_key_id = active_key_id

    @classmethod
    def from_single_key(
        cls, key: str | bytes, *, key_id: str = "primary"
    ) -> "VersionedTokenCipher":
        return cls({key_id: key}, active_key_id=key_id)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None,
    ) -> "VersionedTokenCipher":
        """Load a rotation-capable keyring without ever logging key values."""
        source = os.environ if environ is None else environ
        active = str(source.get("COACHAI_TOKEN_ACTIVE_KEY_ID") or "primary").strip()
        raw_keyring = str(source.get("COACHAI_TOKEN_ENCRYPTION_KEYS") or "").strip()
        if raw_keyring:
            try:
                keys = json.loads(raw_keyring)
            except json.JSONDecodeError as exc:
                raise ConfigurationError(
                    "COACHAI_TOKEN_ENCRYPTION_KEYS must be a JSON key map."
                ) from exc
            if not isinstance(keys, dict):
                raise ConfigurationError(
                    "COACHAI_TOKEN_ENCRYPTION_KEYS must be a JSON key map."
                )
            return cls(keys, active_key_id=active)
        single = str(source.get("COACHAI_TOKEN_ENCRYPTION_KEY") or "").strip()
        if not single:
            raise ConfigurationError(
                "COACHAI_TOKEN_ENCRYPTION_KEY is required for Calendar credentials."
            )
        return cls.from_single_key(single, key_id=active)

    def encrypt(self, plaintext: str) -> str:
        if not isinstance(plaintext, str) or not plaintext:
            raise ValidationError("A non-empty Calendar credential is required.")
        token = self._keys[self.active_key_id].encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{self.PREFIX}:{self.VERSION}:{self.active_key_id}:{token}"

    def _parts(self, envelope: str) -> tuple[str, str]:
        if not isinstance(envelope, str):
            raise CalendarSecurityError()
        parts = envelope.split(":", 3)
        if len(parts) != 4 or parts[0] != self.PREFIX or parts[1] != self.VERSION:
            raise CalendarSecurityError()
        key_id, token = parts[2], parts[3]
        if key_id not in self._keys or not token:
            raise CalendarSecurityError()
        return key_id, token

    def decrypt(self, envelope: str) -> str:
        key_id, token = self._parts(envelope)
        try:
            return self._keys[key_id].decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise CalendarSecurityError() from exc

    def needs_rotation(self, envelope: str) -> bool:
        key_id, _ = self._parts(envelope)
        return key_id != self.active_key_id

    def rotate(self, envelope: str) -> str:
        plaintext = self.decrypt(envelope)
        return self.encrypt(plaintext)


@dataclass(frozen=True, slots=True)
class GoogleOAuthCredentials:
    access_token: str
    refresh_token: str
    token_expiry: datetime
    scopes: str


def _parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CalendarSecurityError() from exc
    else:
        raise CalendarSecurityError()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class EncryptedGoogleTokenStore:
    """Principal-scoped persistence for encrypted Google OAuth credentials."""

    def __init__(self, db: CalendarDatabase, cipher: VersionedTokenCipher) -> None:
        self.db = db
        self.cipher = cipher

    def save(
        self,
        principal: AuthenticatedPrincipal,
        *,
        access_token: str,
        refresh_token: str | None,
        token_expiry: datetime,
        scopes: str = "",
    ) -> GoogleOAuthCredentials:
        user_id = _require_principal(principal)
        if not access_token:
            raise ValidationError("Google access token is required.")
        expiry = _parse_utc_datetime(token_expiry)
        stored_expiry = _utc_naive(expiry)
        with self.db.transaction():
            if self.db.fetch_one("SELECT user_id FROM users WHERE user_id=?", (user_id,)) is None:
                raise ResourceNotFound("The authenticated account was not found.")
            existing = self.db.fetch_one(
                "SELECT refresh_token FROM google_oauth_tokens WHERE user_id=?", (user_id,)
            )
            if refresh_token:
                encrypted_refresh = self.cipher.encrypt(refresh_token)
                resolved_refresh = refresh_token
            elif existing is not None:
                encrypted_refresh = str(existing["refresh_token"])
                resolved_refresh = self.cipher.decrypt(encrypted_refresh)
            else:
                raise ValidationError("Google refresh token is required for initial connection.")
            encrypted_access = self.cipher.encrypt(access_token)
            self.db.execute(
                """INSERT INTO google_oauth_tokens
                       (user_id,access_token,refresh_token,token_expiry,scopes)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       access_token=excluded.access_token,
                       refresh_token=excluded.refresh_token,
                       token_expiry=excluded.token_expiry,
                       scopes=excluded.scopes,
                       updated_at=CURRENT_TIMESTAMP""",
                (
                    user_id, encrypted_access, encrypted_refresh,
                    stored_expiry, str(scopes or ""),
                ),
            )
        return GoogleOAuthCredentials(
            access_token=access_token,
            refresh_token=resolved_refresh,
            token_expiry=expiry,
            scopes=str(scopes or ""),
        )

    def get(
        self, principal: AuthenticatedPrincipal
    ) -> GoogleOAuthCredentials | None:
        user_id = _require_principal(principal)
        with self.db.transaction():
            row = self.db.fetch_one(
                """SELECT access_token,refresh_token,token_expiry,scopes
                   FROM google_oauth_tokens WHERE user_id=?""",
                (user_id,),
            )
            if row is None:
                return None
            encrypted_access = str(row["access_token"])
            encrypted_refresh = str(row["refresh_token"])
            # Non-destructive adoption of pre-encryption CoachAI rows. The
            # first authenticated read rewrites both values before returning;
            # subsequent reads always require a valid versioned envelope.
            legacy_access = not encrypted_access.startswith(
                f"{VersionedTokenCipher.PREFIX}:{VersionedTokenCipher.VERSION}:"
            )
            legacy_refresh = not encrypted_refresh.startswith(
                f"{VersionedTokenCipher.PREFIX}:{VersionedTokenCipher.VERSION}:"
            )
            access_token = (
                encrypted_access if legacy_access
                else self.cipher.decrypt(encrypted_access)
            )
            refresh_token = (
                encrypted_refresh if legacy_refresh
                else self.cipher.decrypt(encrypted_refresh)
            )
            # Opportunistic re-encryption makes rotation incremental and does
            # not expose plaintext to callers or logs.
            if (
                legacy_access
                or legacy_refresh
                or self.cipher.needs_rotation(encrypted_access)
                or self.cipher.needs_rotation(encrypted_refresh)
            ):
                self.db.execute(
                    """UPDATE google_oauth_tokens
                       SET access_token=?,refresh_token=?,updated_at=CURRENT_TIMESTAMP
                       WHERE user_id=?""",
                    (
                        self.cipher.encrypt(access_token),
                        self.cipher.encrypt(refresh_token),
                        user_id,
                    ),
                )
        return GoogleOAuthCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=_parse_utc_datetime(row["token_expiry"]),
            scopes=str(row.get("scopes") or ""),
        )

    def delete(self, principal: AuthenticatedPrincipal) -> bool:
        user_id = _require_principal(principal)
        with self.db.transaction():
            result = self.db.execute(
                "DELETE FROM google_oauth_tokens WHERE user_id=?", (user_id,)
            )
        return result.rowcount == 1


@dataclass(frozen=True, slots=True)
class CalendarOperation:
    operation_id: str
    user_id: str
    operation_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    status: str
    attempts: int
    available_at: Any
    locked_at: Any = None
    locked_by: str | None = None
    last_error: str | None = None
    created_at: Any = None
    completed_at: Any = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "CalendarOperation":
        try:
            payload = json.loads(str(row["payload"]))
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            return cls(
                operation_id=str(row["operation_id"]),
                user_id=str(row["user_id"]),
                operation_type=str(row["operation_type"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=payload,
                status=str(row["status"]),
                attempts=int(row["attempts"]),
                available_at=row.get("available_at"),
                locked_at=row.get("locked_at"),
                locked_by=row.get("locked_by"),
                last_error=row.get("last_error"),
                created_at=row.get("created_at"),
                completed_at=row.get("completed_at"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CalendarOperationError() from exc


class CalendarOperationQueue:
    """Durable, idempotent Calendar mutation queue with worker leases."""

    def __init__(self, db: CalendarDatabase) -> None:
        self.db = db

    @staticmethod
    def _validate_key(value: str) -> str:
        value = str(value or "").strip()
        if not value or len(value) > 200:
            raise ValidationError("Calendar idempotency key is invalid.")
        return value

    def enqueue(
        self,
        principal: AuthenticatedPrincipal,
        *,
        operation_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        available_at: datetime | None = None,
    ) -> tuple[CalendarOperation, bool]:
        user_id = _require_principal(principal)
        operation_type = _validate_identifier(
            operation_type, label="Calendar operation type", pattern=_OPERATION_RE
        )
        idempotency_key = self._validate_key(idempotency_key)
        encoded = _canonical_payload(payload)
        ready_at = _utc_naive(available_at)

        with self.db.transaction():
            if self.db.fetch_one("SELECT user_id FROM users WHERE user_id=?", (user_id,)) is None:
                raise ResourceNotFound("The authenticated account was not found.")
            existing = self.db.fetch_one(
                """SELECT * FROM calendar_operations
                   WHERE user_id=? AND operation_type=? AND idempotency_key=?""",
                (user_id, operation_type, idempotency_key),
            )
            if existing is not None:
                if str(existing["payload"]) != encoded:
                    raise IdempotencyConflict()
                return CalendarOperation.from_row(existing), False

            operation_id = str(uuid4())
            result = self.db.execute(
                """INSERT INTO calendar_operations
                       (operation_id,user_id,operation_type,idempotency_key,payload,status,
                        attempts,available_at,locked_at,locked_by,last_error,completed_at)
                   VALUES (?,?,?,?,?,'queued',0,?,NULL,NULL,NULL,NULL)
                   ON CONFLICT(user_id,operation_type,idempotency_key) DO NOTHING""",
                (operation_id, user_id, operation_type, idempotency_key, encoded, ready_at),
            )
            created = result.rowcount == 1
            row = self.db.fetch_one(
                """SELECT * FROM calendar_operations
                   WHERE user_id=? AND operation_type=? AND idempotency_key=?""",
                (user_id, operation_type, idempotency_key),
            )
            if row is None:
                raise CalendarOperationError()
            if str(row["payload"]) != encoded:
                raise IdempotencyConflict()
            return CalendarOperation.from_row(row), created

    def claim_ready(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
        limit: int = 25,
        stale_after_seconds: int = 300,
        max_attempts: int = 5,
    ) -> list[CalendarOperation]:
        worker_id = str(worker_id or "").strip()
        if not worker_id or len(worker_id) > 100:
            raise ValidationError("Calendar worker ID is invalid.")
        if not isinstance(limit, int) or not (1 <= limit <= 100):
            raise ValidationError("Calendar claim limit must be between 1 and 100.")
        if stale_after_seconds < 1 or max_attempts < 1:
            raise ValidationError("Calendar retry settings are invalid.")
        claimed_at = _utc_naive(now)
        stale_before = claimed_at - timedelta(seconds=stale_after_seconds)
        claimed: list[CalendarOperation] = []

        with self.db.transaction():
            candidates = self.db.fetch_all(
                """SELECT operation_id FROM calendar_operations
                   WHERE attempts<? AND (
                       (status IN ('queued','failed') AND available_at<=?) OR
                       (status='processing' AND locked_at IS NOT NULL AND locked_at<=?)
                   )
                   ORDER BY available_at,created_at,operation_id LIMIT ?""",
                (max_attempts, claimed_at, stale_before, limit),
            )
            for candidate in candidates:
                operation_id = str(candidate["operation_id"])
                result = self.db.execute(
                    """UPDATE calendar_operations
                       SET status='processing',attempts=attempts+1,locked_at=?,locked_by=?,
                           last_error=NULL
                       WHERE operation_id=? AND attempts<? AND (
                           (status IN ('queued','failed') AND available_at<=?) OR
                           (status='processing' AND locked_at IS NOT NULL AND locked_at<=?)
                       )""",
                    (
                        claimed_at, worker_id, operation_id, max_attempts,
                        claimed_at, stale_before,
                    ),
                )
                if result.rowcount != 1:
                    continue
                row = self.db.fetch_one(
                    "SELECT * FROM calendar_operations WHERE operation_id=?",
                    (operation_id,),
                )
                if row is not None:
                    claimed.append(CalendarOperation.from_row(row))
        return claimed

    def mark_completed(
        self,
        operation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> CalendarOperation:
        completed_at = _utc_naive(now)
        with self.db.transaction():
            result = self.db.execute(
                """UPDATE calendar_operations
                   SET status='completed',completed_at=?,locked_at=NULL,locked_by=NULL,last_error=NULL
                   WHERE operation_id=? AND status='processing' AND locked_by=?""",
                (completed_at, operation_id, worker_id),
            )
            if result.rowcount != 1:
                raise VersionConflict("The Calendar operation lease is no longer held.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_operations WHERE operation_id=?", (operation_id,)
            )
        if row is None:
            raise CalendarOperationError()
        return CalendarOperation.from_row(row)

    def mark_failed(
        self,
        operation_id: str,
        *,
        worker_id: str,
        error: BaseException | str,
        now: datetime | None = None,
        retryable: bool = True,
        max_attempts: int = 5,
        base_delay_seconds: int = 30,
        max_delay_seconds: int = 3600,
    ) -> CalendarOperation:
        failed_at = _utc_naive(now)
        with self.db.transaction():
            current = self.db.fetch_one(
                """SELECT attempts FROM calendar_operations
                   WHERE operation_id=? AND status='processing' AND locked_by=?""",
                (operation_id, worker_id),
            )
            if current is None:
                raise VersionConflict("The Calendar operation lease is no longer held.")
            attempts = int(current["attempts"])
            exhausted = (not retryable) or attempts >= max_attempts
            stored_attempts = max(attempts, max_attempts) if exhausted else attempts
            exponent = max(0, attempts - 1)
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** exponent))
            available_at = failed_at + timedelta(seconds=delay)
            result = self.db.execute(
                """UPDATE calendar_operations
                   SET status='failed',attempts=?,available_at=?,locked_at=NULL,locked_by=NULL,
                       last_error=?,completed_at=NULL
                   WHERE operation_id=? AND status='processing' AND locked_by=?""",
                (
                    stored_attempts, available_at, _safe_failure_message(error),
                    operation_id, worker_id,
                ),
            )
            if result.rowcount != 1:
                raise VersionConflict("The Calendar operation lease is no longer held.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_operations WHERE operation_id=?", (operation_id,)
            )
        if row is None:
            raise CalendarOperationError()
        return CalendarOperation.from_row(row)

    def get(
        self, principal: AuthenticatedPrincipal, operation_id: str
    ) -> CalendarOperation:
        user_id = _require_principal(principal)
        row = self.db.fetch_one(
            "SELECT * FROM calendar_operations WHERE operation_id=? AND user_id=?",
            (operation_id, user_id),
        )
        if row is None:
            raise ResourceNotFound("The Calendar operation was not found.")
        return CalendarOperation.from_row(row)

    def list_for_user(
        self,
        principal: AuthenticatedPrincipal,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[CalendarOperation]:
        user_id = _require_principal(principal)
        if not isinstance(limit, int) or not (1 <= limit <= 500):
            raise ValidationError("Calendar operation list limit is invalid.")
        allowed = {"queued", "processing", "completed", "failed", "cancelled"}
        normalized = tuple(dict.fromkeys(str(item) for item in (statuses or ())))
        if any(item not in allowed for item in normalized):
            raise ValidationError("Calendar operation status is invalid.")
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            rows = self.db.fetch_all(
                f"""SELECT * FROM calendar_operations
                    WHERE user_id=? AND status IN ({placeholders})
                    ORDER BY created_at DESC,operation_id DESC LIMIT ?""",
                (user_id, *normalized, limit),
            )
        else:
            rows = self.db.fetch_all(
                """SELECT * FROM calendar_operations WHERE user_id=?
                   ORDER BY created_at DESC,operation_id DESC LIMIT ?""",
                (user_id, limit),
            )
        return [CalendarOperation.from_row(row) for row in rows]

    def cancel(
        self, principal: AuthenticatedPrincipal, operation_id: str
    ) -> CalendarOperation:
        user_id = _require_principal(principal)
        with self.db.transaction():
            result = self.db.execute(
                """UPDATE calendar_operations
                   SET status='cancelled',locked_at=NULL,locked_by=NULL
                   WHERE operation_id=? AND user_id=? AND status IN ('queued','failed')""",
                (operation_id, user_id),
            )
            if result.rowcount != 1:
                exists = self.db.fetch_one(
                    "SELECT status FROM calendar_operations WHERE operation_id=? AND user_id=?",
                    (operation_id, user_id),
                )
                if exists is None:
                    raise ResourceNotFound("The Calendar operation was not found.")
                raise VersionConflict("The Calendar operation can no longer be cancelled.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_operations WHERE operation_id=? AND user_id=?",
                (operation_id, user_id),
            )
        if row is None:
            raise CalendarOperationError()
        return CalendarOperation.from_row(row)


class CalendarSyncStateStore:
    """Principal-scoped freshness state for bounded Calendar date ranges."""

    def __init__(self, db: CalendarDatabase, *, max_range_days: int = 84) -> None:
        self.db = db
        self.max_range_days = max_range_days

    def _validate(
        self, calendar_id: str, range_start: date, range_end: date
    ) -> tuple[str, date, date]:
        calendar_id = str(calendar_id or "").strip()
        if not calendar_id or len(calendar_id) > 1024:
            raise ValidationError("Calendar ID is invalid.")
        if not isinstance(range_start, date) or not isinstance(range_end, date):
            raise ValidationError("Calendar sync range is invalid.")
        if range_end < range_start or (range_end - range_start).days + 1 > self.max_range_days:
            raise ValidationError("Calendar sync range is invalid.")
        return calendar_id, range_start, range_end

    def mark_pending(
        self,
        principal: AuthenticatedPrincipal,
        *,
        calendar_id: str,
        range_start: date,
        range_end: date,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        user_id = _require_principal(principal)
        calendar_id, range_start, range_end = self._validate(
            calendar_id, range_start, range_end
        )
        updated_at = _utc_naive(now)
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO calendar_sync_state
                       (user_id,calendar_id,range_start,range_end,sync_token,status,
                        last_error,last_success_at,updated_at)
                   VALUES (?,?,?,?,NULL,'pending',NULL,NULL,?)
                   ON CONFLICT(user_id,calendar_id) DO UPDATE SET
                       range_start=excluded.range_start,range_end=excluded.range_end,
                       sync_token=NULL,status='pending',last_error=NULL,updated_at=excluded.updated_at""",
                (user_id, calendar_id, range_start, range_end, updated_at),
            )
            row = self.db.fetch_one(
                "SELECT * FROM calendar_sync_state WHERE user_id=? AND calendar_id=?",
                (user_id, calendar_id),
            )
        if row is None:
            raise CalendarOperationError("Calendar sync state could not be saved.")
        return row

    def mark_running(
        self,
        principal: AuthenticatedPrincipal,
        *,
        calendar_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        user_id = _require_principal(principal)
        updated_at = _utc_naive(now)
        with self.db.transaction():
            result = self.db.execute(
                """UPDATE calendar_sync_state SET status='running',last_error=NULL,updated_at=?
                   WHERE user_id=? AND calendar_id=? AND status IN ('pending','error','fresh')""",
                (updated_at, user_id, calendar_id),
            )
            if result.rowcount != 1:
                raise VersionConflict("Calendar sync state is not ready to start.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_sync_state WHERE user_id=? AND calendar_id=?",
                (user_id, calendar_id),
            )
        return row or {}

    def mark_fresh(
        self,
        principal: AuthenticatedPrincipal,
        *,
        calendar_id: str,
        sync_token: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        user_id = _require_principal(principal)
        if sync_token is not None and len(sync_token) > 8192:
            raise ValidationError("Calendar sync token is invalid.")
        completed_at = _utc_naive(now)
        with self.db.transaction():
            result = self.db.execute(
                """UPDATE calendar_sync_state
                   SET sync_token=?,status='fresh',last_error=NULL,last_success_at=?,updated_at=?
                   WHERE user_id=? AND calendar_id=? AND status IN ('running','fresh')""",
                (sync_token, completed_at, completed_at, user_id, calendar_id),
            )
            if result.rowcount != 1:
                raise VersionConflict("Calendar sync state is not active.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_sync_state WHERE user_id=? AND calendar_id=?",
                (user_id, calendar_id),
            )
        return row or {}

    def mark_error(
        self,
        principal: AuthenticatedPrincipal,
        *,
        calendar_id: str,
        error: BaseException | str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        user_id = _require_principal(principal)
        updated_at = _utc_naive(now)
        with self.db.transaction():
            result = self.db.execute(
                """UPDATE calendar_sync_state
                   SET status='error',last_error=?,updated_at=?
                   WHERE user_id=? AND calendar_id=?""",
                (_safe_failure_message(error), updated_at, user_id, calendar_id),
            )
            if result.rowcount != 1:
                raise ResourceNotFound("Calendar sync state was not found.")
            row = self.db.fetch_one(
                "SELECT * FROM calendar_sync_state WHERE user_id=? AND calendar_id=?",
                (user_id, calendar_id),
            )
        return row or {}

    def get(
        self, principal: AuthenticatedPrincipal, *, calendar_id: str
    ) -> dict[str, Any] | None:
        user_id = _require_principal(principal)
        return self.db.fetch_one(
            "SELECT * FROM calendar_sync_state WHERE user_id=? AND calendar_id=?",
            (user_id, calendar_id),
        )
