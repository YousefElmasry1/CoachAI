from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from backend_errors import AuthenticationRequired
from database import Database
from identity import (
    AuthenticatedPrincipal,
    FirebaseTokenVerifier,
    get_or_create_user_from_principal,
)


class FirebaseAuthenticationTests(unittest.TestCase):
    project_id = "coachai-test"

    def claims(self, **changes):
        values = {
            "sub": "firebase-u1",
            "email": "u1@example.com",
            "name": "User One",
            "aud": self.project_id,
            "iss": f"https://securetoken.google.com/{self.project_id}",
            "exp": int(datetime.now(timezone.utc).timestamp()) + 3600,
        }
        values.update(changes)
        return values

    def verifier(self, claims=None, *, raises=None):
        def verify(_token, **_kwargs):
            if raises is not None:
                raise raises
            return self.claims() if claims is None else claims

        return FirebaseTokenVerifier(
            project_id=self.project_id,
            verify_callable=verify,
        )

    def test_valid_token_returns_immutable_trusted_principal(self):
        principal = self.verifier().verify("signed-token")
        self.assertEqual("firebase-u1", principal.user_id)
        self.assertEqual("u1@example.com", principal.email)
        with self.assertRaises(TypeError):
            principal.claims["sub"] = "spoofed"

    def test_missing_or_provider_rejected_token_is_rejected(self):
        with self.assertRaises(AuthenticationRequired):
            self.verifier().verify("")
        with self.assertRaises(AuthenticationRequired):
            self.verifier(raises=ValueError("provider detail")).verify("bad")

    def test_expired_wrong_audience_wrong_issuer_and_disabled_are_rejected(self):
        invalid = (
            self.claims(exp=1),
            self.claims(aud="another-project"),
            self.claims(iss="https://attacker.invalid"),
            self.claims(disabled=True),
        )
        for claims in invalid:
            with self.subTest(claims=claims):
                with self.assertRaises(AuthenticationRequired):
                    self.verifier(claims).verify("token")


class PrincipalProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "auth.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    @staticmethod
    def principal(user_id="firebase-u1"):
        return AuthenticatedPrincipal(
            user_id=user_id,
            email=f"{user_id}@example.com",
            display_name="Firebase User",
            claims={"sub": user_id},
        )

    def test_provisions_idempotently_from_verified_principal(self):
        first = get_or_create_user_from_principal(
            self.db, self.principal(), timezone_name="Africa/Cairo"
        )
        second = get_or_create_user_from_principal(self.db, self.principal())
        self.assertEqual("firebase-u1", first["user_id"])
        self.assertEqual(first["user_id"], second["user_id"])
        self.assertEqual(1, self.db.fetch_one("SELECT COUNT(*) AS n FROM users")["n"])

    def test_disabled_local_account_cannot_be_provisioned(self):
        get_or_create_user_from_principal(self.db, self.principal())
        self.db.execute("UPDATE users SET is_disabled=1 WHERE user_id=?", ("firebase-u1",))
        self.db.commit()
        with self.assertRaises(AuthenticationRequired):
            get_or_create_user_from_principal(self.db, self.principal())


if __name__ == "__main__":
    unittest.main()
