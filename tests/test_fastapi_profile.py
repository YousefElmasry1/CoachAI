from __future__ import annotations

from pathlib import Path
import asyncio
import tempfile
import unittest

from httpx import ASGITransport, AsyncClient

from fastapi_app import create_app
from identity import AuthenticatedPrincipal
from unit_of_work import DatabaseFactory


class _Verifier:
    def verify(self, token: str) -> AuthenticatedPrincipal:
        user_id = {"token-u1": "u1", "token-u2": "u2"}.get(token)
        if user_id is None:
            raise AssertionError("Unexpected test token")
        return AuthenticatedPrincipal(
            user_id=user_id,
            email=f"{user_id}@example.test",
            display_name=user_id,
            claims={"sub": user_id},
        )


class FastAPIProfileRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "api-profile.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        db = self.factory()
        try:
            db.create_user("u1", "u1@example.test", "Ahmed Hassan", "UTC")
            db.create_user("u2", "u2@example.test", "Other User", "UTC")
        finally:
            db.close()
        self.app = create_app(
            readiness_check=lambda: {"status": "healthy", "checks": {}},
            token_verifier=_Verifier(),
            database_factory=self.factory,
        )

    def tearDown(self) -> None:
        self.factory.close()
        self.tempdir.cleanup()

    @staticmethod
    def headers(token: str, key: str | None = None) -> dict[str, str]:
        values = {"Authorization": f"Bearer {token}"}
        if key:
            values["Idempotency-Key"] = key
        return values

    def test_profile_is_principal_scoped(self) -> None:
        async def scenario():
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test",
            ) as client:
                return (
                    await client.get("/v1/profile", headers=self.headers("token-u1")),
                    await client.get("/v1/profile", headers=self.headers("token-u2")),
                )

        u1, u2 = asyncio.run(scenario())

        self.assertEqual(200, u1.status_code)
        self.assertEqual("Ahmed Hassan", u1.json()["display_name"])
        self.assertEqual("u1@example.test", u1.json()["email"])
        self.assertEqual("Other User", u2.json()["display_name"])
        self.assertEqual("no-store", u1.headers["cache-control"])
        self.assertEqual("Authorization", u1.headers["vary"])

    def test_name_and_timezone_updates_are_validated_and_idempotent(self) -> None:
        async def scenario():
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test",
            ) as client:
                name = await client.patch(
                "/v1/profile/display-name",
                json={"display_name": "  Ahmed H.  "},
                headers=self.headers("token-u1", "name-1"),
            )
                replay = await client.patch(
                "/v1/profile/display-name",
                json={"display_name": "Ahmed H."},
                headers=self.headers("token-u1", "name-1"),
            )
                timezone = await client.patch(
                "/v1/profile/timezone",
                json={"timezone": "Africa/Cairo"},
                headers=self.headers("token-u1", "timezone-1"),
            )
                invalid = await client.patch(
                "/v1/profile/timezone",
                json={"timezone": "Not/A_Real_Zone"},
                headers=self.headers("token-u1", "timezone-bad"),
            )
                return name, replay, timezone, invalid

        name, replay, timezone, invalid = asyncio.run(scenario())

        self.assertEqual(200, name.status_code)
        self.assertEqual("Ahmed H.", name.json()["display_name"])
        self.assertFalse(name.json()["idempotent_replay"])
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(200, timezone.status_code)
        self.assertEqual("Africa/Cairo", timezone.json()["timezone"])
        self.assertEqual("no-store", timezone.headers["cache-control"])
        self.assertEqual(422, invalid.status_code)
        self.assertEqual("VALIDATION_ERROR", invalid.json()["code"])

    def test_mutations_require_idempotency_key_and_authentication(self) -> None:
        async def scenario():
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test",
            ) as client:
                missing_key = await client.patch(
                "/v1/profile/display-name",
                json={"display_name": "Changed"},
                headers=self.headers("token-u1"),
            )
                unauthenticated = await client.get("/v1/profile")
                return missing_key, unauthenticated

        missing_key, unauthenticated = asyncio.run(scenario())

        self.assertEqual(422, missing_key.status_code)
        self.assertEqual(401, unauthenticated.status_code)


if __name__ == "__main__":
    unittest.main()
