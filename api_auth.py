"""FastAPI authentication adapter for verified Firebase ID tokens."""

from __future__ import annotations

from threading import Lock
import os
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend_config import BackendSettings
from backend_errors import AuthenticationRequired, ConfigurationError
from identity import AuthenticatedPrincipal, FirebaseTokenVerifier, TokenVerifier


_bearer = HTTPBearer(auto_error=False)


class FirebaseBearerAuthenticator:
    """Resolve a trusted principal from an HTTP Bearer credential.

    The real Firebase verifier is created lazily so liveness and readiness do
    not require Firebase credentials. Tests inject a deterministic verifier and
    never need a Firebase project or network access.
    """

    def __init__(self, verifier: TokenVerifier | None = None) -> None:
        self._verifier = verifier
        self._lock = Lock()

    def _get_verifier(self) -> TokenVerifier:
        if self._verifier is not None:
            return self._verifier
        with self._lock:
            if self._verifier is None:
                project_id = BackendSettings.from_env().firebase_project_id
                if not project_id:
                    raise ConfigurationError(
                        "FIREBASE_PROJECT_ID is required for authenticated API routes."
                    )
                raw_check_revoked = os.getenv("FIREBASE_CHECK_REVOKED", "true").strip().lower()
                if raw_check_revoked not in {"true", "false"}:
                    raise ConfigurationError(
                        "FIREBASE_CHECK_REVOKED must be true or false."
                    )
                environment = os.getenv("COACHAI_ENVIRONMENT", "production").strip().lower()
                if raw_check_revoked == "false" and environment not in {"development", "test"}:
                    raise ConfigurationError(
                        "FIREBASE_CHECK_REVOKED=false is allowed only in development or test."
                    )
                self._verifier = FirebaseTokenVerifier(
                    project_id=project_id,
                    check_revoked=raw_check_revoked == "true",
                )
            return self._verifier

    def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_bearer),
        ] = None,
    ) -> AuthenticatedPrincipal:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not credentials.credentials.strip()
        ):
            raise AuthenticationRequired("A Firebase Bearer token is required.")
        return self._get_verifier().verify(credentials.credentials)
