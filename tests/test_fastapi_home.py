from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from fastapi_app import create_app
from api_models import TaskResponse
from backend_errors import AuthenticationRequired
from identity import AuthenticatedPrincipal
from unit_of_work import DatabaseFactory


class _Verifier:
    def verify(self, token: str) -> AuthenticatedPrincipal:
        user_id = {"token-u1": "u1", "token-u2": "u2"}.get(token)
        if user_id is None:
            raise AuthenticationRequired("Invalid test token")
        return AuthenticatedPrincipal(
            user_id=user_id,
            email=f"{user_id}@example.test",
            display_name=user_id,
            claims={"sub": user_id},
        )


class FastAPIHomeRouteTests(unittest.TestCase):
    day = date(2026, 11, 8)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "api-home.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        db = self.factory()
        try:
            db.create_user("u1", "u1@example.test", "User One", "Africa/Cairo")
            db.create_user("u2", "u2@example.test", "User Two", "UTC")
            self.plan_u1 = db.create_plan("u1", self.day, "private u1 plan")
            self.plan_u2 = db.create_plan("u2", self.day, "private u2 plan")
            self.task_u1 = db.add_task(
                self.plan_u1, "u1", "U1 private task", estimated_minutes=30
            )
            self.task_u2 = db.add_task(
                self.plan_u2, "u2", "U2 private task", estimated_minutes=45
            )
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

    def get_home(self, token: str, query: str = ""):
        with patch("task_application.user_today", return_value=self.day):
            with TestClient(self.app) as client:
                return client.get(
                    f"/v1/home{query}", headers=self.headers(token)
                )

    def test_home_is_principal_scoped_and_not_cacheable(self) -> None:
        u1 = self.get_home("token-u1")
        u2 = self.get_home("token-u2")

        self.assertEqual(200, u1.status_code)
        self.assertEqual("no-store", u1.headers["cache-control"])
        self.assertEqual("Authorization", u1.headers["vary"])
        self.assertEqual("User One", u1.json()["display_name"])
        self.assertEqual(
            [self.task_u1],
            [item["task_id"] for item in u1.json()["plan"]["tasks"]],
        )
        self.assertEqual(
            [self.task_u2],
            [item["task_id"] for item in u2.json()["plan"]["tasks"]],
        )
        self.assertNotIn("private u2 plan", u1.text)
        self.assertNotIn("U2 private task", u1.text)

    def test_home_rejects_missing_and_invalid_bearer_tokens(self) -> None:
        with TestClient(self.app) as client:
            missing = client.get("/v1/home")
            invalid = client.get(
                "/v1/home",
                headers={"Authorization": "Bearer invalid-token"},
            )

        for response in (missing, invalid):
            self.assertEqual(401, response.status_code)
            self.assertEqual("AUTHENTICATION_REQUIRED", response.json()["code"])
            self.assertNotIn("invalid-token", response.text)

    def test_user_and_date_query_injection_cannot_change_scope(self) -> None:
        response = self.get_home(
            "token-u1", "?user_id=u2&date=2099-01-01"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("2026-11-08", response.json()["date"])
        self.assertEqual(
            [self.task_u1],
            [item["task_id"] for item in response.json()["plan"]["tasks"]],
        )

    def test_missing_plan_is_explicit_and_does_not_fall_back_to_other_days(self) -> None:
        with patch("task_application.user_today", return_value=date(2026, 11, 9)):
            with TestClient(self.app) as client:
                response = client.get(
                    "/v1/home", headers=self.headers("token-u1")
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual("2026-11-09", response.json()["date"])
        self.assertIsNone(response.json()["plan"])
        self.assertNotIn("U1 private task", response.text)

    def test_home_preview_is_bounded_but_progress_counts_every_task(self) -> None:
        db = self.factory()
        try:
            for index in range(100):
                db.add_task(
                    self.plan_u1,
                    "u1",
                    f"Task {index}",
                    estimated_minutes=5,
                    order_index=index + 1,
                )
        finally:
            db.close()

        response = self.get_home("token-u1")
        plan = response.json()["plan"]

        self.assertEqual(200, response.status_code)
        self.assertEqual(100, len(plan["tasks"]))
        self.assertTrue(plan["tasks_truncated"])
        self.assertEqual(101, plan["progress"]["total"])
        self.assertEqual(101, plan["progress"]["pending"])

    def test_scheduled_database_times_serialize_in_task_response(self) -> None:
        now = datetime.now(timezone.utc)
        task = TaskResponse.model_validate({
            "task_id": 1,
            "plan_id": 1,
            "title": "Scheduled task",
            "priority": 3,
            "estimated_minutes": 60,
            "scheduled_start": time(9, 0),
            "scheduled_end": time(10, 0),
            "is_fixed_time": False,
            "is_break": False,
            "order_index": 0,
            "status": "pending",
            "timer_accumulated_seconds": 0,
            "pause_count": 0,
            "timer_total_paused_seconds": 0,
            "timer_version": 0,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        })

        payload = task.model_dump(mode="json")
        self.assertEqual("09:00:00", payload["scheduled_start"])
        self.assertEqual("10:00:00", payload["scheduled_end"])


if __name__ == "__main__":
    unittest.main()
