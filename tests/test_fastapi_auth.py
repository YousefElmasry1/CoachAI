from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient

from backend_errors import AuthenticationRequired
from fastapi_app import create_app
from identity import AuthenticatedPrincipal


class _Verifier:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.tokens: list[str] = []

    def verify(self, token: str) -> AuthenticatedPrincipal:
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return AuthenticatedPrincipal(
            user_id="firebase-u1",
            email="u1@example.test",
            display_name="User One",
            claims={"sub": "firebase-u1"},
        )


def _authenticated_test_app(verifier=None):
    app = create_app(
        readiness_check=lambda: {"status": "healthy", "checks": {}},
        token_verifier=verifier,
    )

    @app.get("/_test/whoami")
    async def whoami(principal=Depends(app.state.authenticate)):
        return {"user_id": principal.user_id}

    return app


class FastAPIFirebaseAuthenticationTests(unittest.TestCase):
    def test_verified_bearer_token_produces_trusted_principal(self) -> None:
        verifier = _Verifier()
        with TestClient(_authenticated_test_app(verifier)) as client:
            response = client.get(
                "/_test/whoami",
                headers={"Authorization": "Bearer signed-firebase-token"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual({"user_id": "firebase-u1"}, response.json())
        self.assertEqual(["signed-firebase-token"], verifier.tokens)

    def test_missing_and_malformed_credentials_are_safe(self) -> None:
        verifier = _Verifier()
        with TestClient(_authenticated_test_app(verifier)) as client:
            missing = client.get("/_test/whoami")
            wrong_scheme = client.get(
                "/_test/whoami", headers={"Authorization": "Basic abc"}
            )

        for response in (missing, wrong_scheme):
            self.assertEqual(401, response.status_code)
            self.assertEqual("AUTHENTICATION_REQUIRED", response.json()["code"])
            self.assertNotIn("abc", response.text)
        self.assertEqual([], verifier.tokens)

    def test_rejected_expired_or_revoked_token_is_not_exposed(self) -> None:
        secret = "rejected-secret-bearer"
        verifier = _Verifier(
            error=AuthenticationRequired("The Firebase ID token is invalid.")
        )
        with TestClient(_authenticated_test_app(verifier)) as client:
            response = client.get(
                "/_test/whoami", headers={"Authorization": f"Bearer {secret}"}
            )

        self.assertEqual(401, response.status_code)
        self.assertEqual("AUTHENTICATION_REQUIRED", response.json()["code"])
        self.assertNotIn(secret, response.text)

    def test_default_verifier_fails_safely_without_firebase_project(self) -> None:
        environment = {"DATABASE_URL": "postgresql+psycopg2://local/test"}
        with patch.dict(os.environ, environment, clear=True):
            with TestClient(_authenticated_test_app()) as client:
                response = client.get(
                    "/_test/whoami", headers={"Authorization": "Bearer token"}
                )

        self.assertEqual(503, response.status_code)
        self.assertEqual("CONFIGURATION_ERROR", response.json()["code"])
        self.assertNotIn("token", response.text)


if __name__ == "__main__":
    unittest.main()
