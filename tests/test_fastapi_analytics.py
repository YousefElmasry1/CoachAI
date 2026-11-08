from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

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


class FastAPIAnalyticsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "api-analytics.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        db = self.factory()
        try:
            for user_id in ("u1", "u2"):
                db.create_user(user_id, f"{user_id}@example.test", user_id, "UTC")
            plan = db.create_plan("u1", date.today(), "u1 analytics")
            db.add_task(plan, "u1", "Observed task", estimated_minutes=30)
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
    def headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_returns_complete_flat_profile_scoped_to_principal(self) -> None:
        with TestClient(self.app) as client:
            u1 = client.get(
                "/v1/analytics?window_days=30&language=en",
                headers=self.headers("token-u1"),
            )
            u2 = client.get("/v1/analytics", headers=self.headers("token-u2"))

        self.assertEqual(200, u1.status_code)
        self.assertEqual(200, u2.status_code)
        payload = u1.json()
        self.assertEqual(1, payload["sample_size"])
        self.assertEqual(0, u2.json()["sample_size"])
        self.assertEqual("no-store", u1.headers["cache-control"])
        self.assertEqual("Authorization", u1.headers["vary"])
        expected_sections = {
            "productivity", "priority", "planning", "categories",
            "failure_analysis", "consistency", "trend", "patterns",
            "insights", "correlations", "heatmaps", "duration_analysis",
            "weekday_weekend", "habits", "burnout", "best_hour", "capacity",
        }
        self.assertTrue(expected_sections.issubset(payload))

    def test_query_contract_rejects_unsupported_values(self) -> None:
        with TestClient(self.app) as client:
            bad_window = client.get(
                "/v1/analytics?window_days=0",
                headers=self.headers("token-u1"),
            )
            bad_language = client.get(
                "/v1/analytics?language=fr",
                headers=self.headers("token-u1"),
            )

        for response in (bad_window, bad_language):
            self.assertEqual(422, response.status_code)
            self.assertEqual("VALIDATION_ERROR", response.json()["code"])


if __name__ == "__main__":
    unittest.main()
