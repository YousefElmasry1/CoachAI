from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi import Depends
from fastapi.testclient import TestClient

from database import Database
from fastapi_app import create_app
from identity import AuthenticatedPrincipal
from unit_of_work import DatabaseFactory


class _Verifier:
    def verify(self, token: str) -> AuthenticatedPrincipal:
        user_id = "firebase-u2" if token == "token-u2" else "firebase-u1"
        return AuthenticatedPrincipal(
            user_id=user_id,
            email=f"{user_id}@example.test",
            display_name=user_id,
            claims={"sub": user_id},
        )


class FastAPIUnitOfWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "api-uow.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        self.app = create_app(
            readiness_check=lambda: {"status": "healthy", "checks": {}},
            token_verifier=_Verifier(),
            database_factory=self.factory,
        )

        @self.app.get("/_test/context")
        async def context(application=Depends(self.app.state.application)):
            return {"user_id": application.principal.user_id}

        @self.app.post("/_test/uncommitted")
        async def uncommitted(application=Depends(self.app.state.application)):
            application.db.execute(
                "INSERT INTO categories(user_id,name,color) VALUES(?,?,?)",
                (application.principal.user_id, "Rollback", "#111111"),
            )
            return {"written": True}

        @self.app.post("/_test/failure")
        async def failure(application=Depends(self.app.state.application)):
            application.db.execute(
                "INSERT INTO categories(user_id,name,color) VALUES(?,?,?)",
                (application.principal.user_id, "Failure", "#222222"),
            )
            raise RuntimeError("route failure")

    def tearDown(self) -> None:
        self.factory.close()
        self.tempdir.cleanup()

    @staticmethod
    def headers(token: str = "token-u1") -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def count_categories(self) -> int:
        db = self.factory()
        try:
            return int(db.fetch_one("SELECT COUNT(*) AS n FROM categories")["n"])
        finally:
            db.close()

    def test_principal_is_provisioned_and_requests_are_isolated(self) -> None:
        with TestClient(self.app) as client:
            first = client.get("/_test/context", headers=self.headers())
            second = client.get(
                "/_test/context", headers=self.headers("token-u2")
            )

        self.assertEqual("firebase-u1", first.json()["user_id"])
        self.assertEqual("firebase-u2", second.json()["user_id"])
        db = self.factory()
        try:
            users = db.fetch_all("SELECT user_id FROM users ORDER BY user_id")
        finally:
            db.close()
        self.assertEqual(["firebase-u1", "firebase-u2"], [row["user_id"] for row in users])
        self.assertEqual(0, self.factory.pool_metrics()["checked_out"])

    def test_uncommitted_work_rolls_back_and_connection_is_released(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/_test/uncommitted", headers=self.headers())

        self.assertEqual(200, response.status_code)
        self.assertEqual(0, self.count_categories())
        self.assertEqual(0, self.factory.pool_metrics()["checked_out"])

    def test_exception_rolls_back_and_connection_is_released(self) -> None:
        with TestClient(self.app, raise_server_exceptions=False) as client:
            response = client.post("/_test/failure", headers=self.headers())

        self.assertEqual(500, response.status_code)
        self.assertEqual("INTERNAL_ERROR", response.json()["code"])
        self.assertEqual(0, self.count_categories())
        self.assertEqual(0, self.factory.pool_metrics()["checked_out"])

    def test_disabled_local_account_is_rejected_before_application_service(self) -> None:
        with TestClient(self.app) as client:
            self.assertEqual(
                200, client.get("/_test/context", headers=self.headers()).status_code
            )
        db = self.factory()
        try:
            db.execute(
                "UPDATE users SET is_disabled=1 WHERE user_id=?",
                ("firebase-u1",),
            )
            db.commit()
        finally:
            db.close()

        with TestClient(self.app) as client:
            response = client.get("/_test/context", headers=self.headers())

        self.assertEqual(401, response.status_code)
        self.assertEqual("AUTHENTICATION_REQUIRED", response.json()["code"])
        self.assertEqual(0, self.factory.pool_metrics()["checked_out"])


if __name__ == "__main__":
    unittest.main()
