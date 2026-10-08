"""Firebase Admin SDK adapter with deterministic error classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ErrorClassification, ProviderResult, PushMessage
from .config import FCMConfig


class FCMProvider:
    name = "fcm"

    def __init__(
        self, config: FCMConfig, *, firebase_module: Any = None,
        messaging_module: Any = None, credentials_module: Any = None,
    ) -> None:
        self.config = config
        self._firebase = firebase_module
        self._messaging = messaging_module
        self._credentials = credentials_module or getattr(firebase_module, "credentials", None)
        self._app = None

    def _initialize(self) -> ProviderResult | None:
        if not self.config.enabled:
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="fcm_disabled")
        try:
            if self._firebase is None or self._messaging is None:
                import firebase_admin
                from firebase_admin import credentials, messaging
                self._firebase, self._messaging = firebase_admin, messaging
                self._credentials = credentials
            options = {"httpTimeout": self.config.timeout_seconds}
            if self.config.project_id:
                options["projectId"] = self.config.project_id
            credential = None
            if self.config.credentials_path:
                path = Path(self.config.credentials_path)
                if not path.is_file():
                    return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="credentials_not_found")
                credential = self._credentials.Certificate(str(path))
            try:
                self._app = self._firebase.get_app("coachai-push")
            except ValueError:
                self._app = self._firebase.initialize_app(credential, options, name="coachai-push")
            return None
        except (ImportError, ValueError, OSError):
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="fcm_configuration")
        except Exception as exc:
            return ProviderResult(False, classification=self.classify_error(exc), error_code=type(exc).__name__)

    @staticmethod
    def classify_error(exc: Exception) -> ErrorClassification:
        name = type(exc).__name__
        code = str(getattr(exc, "code", "") or "").lower()
        if name in {"UnregisteredError"} or code in {"unregistered", "registration-token-not-registered"}:
            return ErrorClassification.PERMANENT_TOKEN
        if name in {"SenderIdMismatchError"} or code in {"sender-id-mismatch", "invalid-registration-token"}:
            return ErrorClassification.PERMANENT_TOKEN
        if name in {"QuotaExceededError", "ResourceExhaustedError"} or code in {"quota-exceeded", "resource-exhausted"}:
            return ErrorClassification.RATE_LIMITED
        if name in {"ThirdPartyAuthError", "UnauthenticatedError"} or code in {"unauthenticated", "authentication-error"}:
            return ErrorClassification.AUTHENTICATION
        if name in {"RefreshError", "InvalidCredentialsError"}:
            return ErrorClassification.AUTHENTICATION
        if name in {"DefaultCredentialsError"}:
            return ErrorClassification.CONFIGURATION
        if name in {"InvalidArgumentError", "ValueError"} or code == "invalid-argument":
            return ErrorClassification.PERMANENT_REQUEST
        if name in {
            "UnavailableError", "InternalError", "DeadlineExceededError",
            "TimeoutError", "ConnectionError", "TransportError",
        }:
            return ErrorClassification.TRANSIENT
        return ErrorClassification.UNKNOWN

    def send(self, message: PushMessage) -> ProviderResult:
        initialization = self._initialize() if self._app is None else None
        if initialization is not None:
            return initialization
        try:
            request = self._messaging.Message(
                notification=self._messaging.Notification(title=message.title, body=message.body),
                data=dict(message.data), token=message.token,
            )
            message_id = self._messaging.send(request, dry_run=self.config.dry_run, app=self._app)
            return ProviderResult(True, provider_message_id=str(message_id))
        except Exception as exc:
            return ProviderResult(
                False, classification=self.classify_error(exc), error_code=type(exc).__name__,
            )
