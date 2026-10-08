"""HTTP/2 APNs token-authentication adapter."""

from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .base import ErrorClassification, ProviderResult, PushMessage
from .config import APNsConfig


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class APNsProvider:
    name = "apns"
    _PERMANENT_TOKEN = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
    _AUTH = {"ExpiredProviderToken", "InvalidProviderToken", "MissingProviderToken"}
    _PERMANENT_REQUEST = {
        "BadCollapseId", "BadMessageId", "BadPriority", "BadTopic", "DuplicateHeaders",
        "PayloadEmpty", "TopicDisallowed", "BadPath", "MethodNotAllowed",
    }

    def __init__(self, config: APNsConfig, *, client: Any = None, clock=time.time) -> None:
        self.config = config
        self._client = client
        self._clock = clock
        self._jwt: str | None = None
        self._jwt_created_at = 0.0

    @property
    def endpoint(self) -> str:
        return "https://api.push.apple.com" if self.config.environment == "production" else "https://api.sandbox.push.apple.com"

    def _configuration_error(self) -> ProviderResult | None:
        if not self.config.enabled:
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="apns_disabled")
        if not all((self.config.key_id, self.config.team_id, self.config.bundle_id, self.config.private_key_path)):
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="apns_configuration")
        if not Path(str(self.config.private_key_path)).is_file():
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="private_key_not_found")
        return None

    def _token(self) -> str:
        now = float(self._clock())
        if self._jwt is not None and now - self._jwt_created_at < 20 * 60:
            return self._jwt
        header = _b64(json.dumps({"alg": "ES256", "kid": self.config.key_id}, separators=(",", ":")).encode())
        payload = _b64(json.dumps({"iss": self.config.team_id, "iat": int(now)}, separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        key = serialization.load_pem_private_key(Path(str(self.config.private_key_path)).read_bytes(), password=None)
        der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        self._jwt = f"{header}.{payload}.{signature}"
        self._jwt_created_at = now
        return self._jwt

    @classmethod
    def classify_response(cls, status: int, reason: str | None) -> ErrorClassification:
        if reason in cls._PERMANENT_TOKEN:
            return ErrorClassification.PERMANENT_TOKEN
        if status == 410:
            return ErrorClassification.PERMANENT_TOKEN
        if reason in cls._AUTH or status == 403:
            return ErrorClassification.AUTHENTICATION
        if status == 429:
            return ErrorClassification.RATE_LIMITED
        if status >= 500:
            return ErrorClassification.TRANSIENT
        if reason in cls._PERMANENT_REQUEST or status in {400, 404, 405, 413}:
            return ErrorClassification.PERMANENT_REQUEST
        return ErrorClassification.UNKNOWN

    def _retry_after_seconds(self, value: str | None) -> int | None:
        if not value:
            return None
        if value.isdigit():
            return max(1, int(value))
        try:
            retry_at = parsedate_to_datetime(value)
            return max(1, math.ceil(retry_at.timestamp() - float(self._clock())))
        except (TypeError, ValueError, OverflowError):
            return None

    def send(self, message: PushMessage) -> ProviderResult:
        invalid = self._configuration_error()
        if invalid is not None:
            return invalid
        try:
            token = self._token()
            if self._client is None:
                self._client = httpx.Client(http2=True, timeout=self.config.timeout_seconds)
            response = self._client.post(
                f"{self.endpoint}/3/device/{message.token}",
                headers={
                    "authorization": f"bearer {token}",
                    "apns-topic": message.app_identifier or str(self.config.bundle_id),
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                json={"aps": {"alert": {"title": message.title, "body": message.body}}, "coachai": dict(message.data)},
            )
            if response.status_code == 200:
                return ProviderResult(True, provider_message_id=response.headers.get("apns-id"))
            try:
                reason = response.json().get("reason")
            except Exception:
                reason = None
            retry_after = response.headers.get("retry-after")
            return ProviderResult(
                False, classification=self.classify_response(response.status_code, reason),
                error_code=reason or f"http_{response.status_code}",
                retry_after_seconds=self._retry_after_seconds(retry_after),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return ProviderResult(False, classification=ErrorClassification.TRANSIENT, error_code="network_error")
        except (ValueError, OSError):
            return ProviderResult(False, classification=ErrorClassification.CONFIGURATION, error_code="apns_configuration")
        except Exception as exc:
            return ProviderResult(False, classification=ErrorClassification.UNKNOWN, error_code=type(exc).__name__)
