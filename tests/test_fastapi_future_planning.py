from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend_errors import AuthenticationRequired
from fastapi_app import create_app
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
            display_name=f"User {user_id[-1]}",
            claims={"sub": user_id},
        )


class FastAPIFuturePlanningTests(unittest.TestCase):
    today = date(2026, 11, 8)
    end = date(2026, 11, 10)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "future-api.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        db = self.factory()
        try:
            db.create_user("u1", "u1@example.test", "User One", "UTC")
            db.create_user("u2", "u2@example.test", "User Two", "UTC")
            self.plan_u1 = db.create_plan("u1", self.today, "U1")
            self.plan_u2 = db.create_plan("u2", self.today, "U2")
            self.task_u1 = db.add_task(
                self.plan_u1, "u1", "U1 private future task",
                estimated_minutes=60, priority=2,
            )
            self.task_u2 = db.add_task(
                self.plan_u2, "u2", "U2 private future task",
                estimated_minutes=45, priority=3,
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
    def headers(token: str, key: str | None = None) -> dict[str, str]:
        result = {"Authorization": f"Bearer {token}"}
        if key:
            result["Idempotency-Key"] = key
        return result

    def request_payload(self, *, include_draft: bool = False) -> dict:
        tasks = [{
            "task_id": self.task_u1,
            "title": "U1 private future task",
            "remaining_minutes": 60,
            "priority": 2,
        }]
        if include_draft:
            tasks.append({
                "temporary_id": "draft:mobile-contract",
                "title": "Mobile draft task",
                "remaining_minutes": 30,
                "duration_source": "user_explicit",
                "priority": 3,
                "deadline": "2026-11-10T18:00:00",
            })
        return {
            "scope": "date_range",
            "input_method": "manual",
            "planning_start": self.today.isoformat(),
            "planning_end": self.end.isoformat(),
            "work_day_start": "09:00:00",
            "tasks": tasks,
        }

    def patches(self):
        return (
            patch("timezone_utils.user_today", return_value=self.today),
            patch("future_planner.user_today", return_value=self.today),
        )

    def test_candidates_are_authenticated_tenant_scoped_and_typed(self) -> None:
        first, second = self.patches()
        with first, second, TestClient(self.app) as client:
            missing = client.get(
                "/v1/future-planning/candidates",
                params={"planning_start": self.today, "planning_end": self.end},
            )
            u1 = client.get(
                "/v1/future-planning/candidates",
                params={"planning_start": self.today, "planning_end": self.end},
                headers=self.headers("token-u1"),
            )
            u2 = client.get(
                "/v1/future-planning/candidates",
                params={"planning_start": self.today, "planning_end": self.end},
                headers=self.headers("token-u2"),
            )

        self.assertEqual(401, missing.status_code)
        self.assertEqual(200, u1.status_code)
        self.assertEqual([self.task_u1], [item["task_id"] for item in u1.json()["tasks"]])
        self.assertEqual([self.task_u2], [item["task_id"] for item in u2.json()["tasks"]])
        self.assertNotIn("U2 private", u1.text)

    def test_preview_is_read_only_and_invalid_past_date_is_422(self) -> None:
        db = self.factory()
        try:
            before_tasks = db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"]
            before_segments = db.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"]
        finally:
            db.close()

        first, second = self.patches()
        with first, second, TestClient(self.app) as client:
            preview = client.post(
                "/v1/future-planning/preview",
                json=self.request_payload(include_draft=True),
                headers=self.headers("token-u1"),
            )
            invalid = client.get(
                "/v1/future-planning/candidates",
                params={"planning_start": "2026-11-07", "planning_end": "2026-11-08"},
                headers=self.headers("token-u1"),
            )

        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual(2, preview.json()["placed_count"])
        self.assertEqual(0, preview.json()["unplaced_count"])
        self.assertEqual(422, invalid.status_code)
        self.assertEqual("VALIDATION_ERROR", invalid.json()["code"])
        db = self.factory()
        try:
            self.assertEqual(before_tasks, db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"])
            self.assertEqual(before_segments, db.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"])
        finally:
            db.close()

    def test_apply_replays_without_duplicate_draft_and_undo_restores(self) -> None:
        payload = self.request_payload(include_draft=True)
        first, second = self.patches()
        with first, second, TestClient(self.app) as client:
            applied = client.post(
                "/v1/future-planning/apply",
                json=payload,
                headers=self.headers("token-u1", "future-mobile-once"),
            )
            replay = client.post(
                "/v1/future-planning/apply",
                json=payload,
                headers=self.headers("token-u1", "future-mobile-once"),
            )
            self.assertEqual(200, applied.status_code, applied.text)
            self.assertEqual(200, replay.status_code, replay.text)
            self.assertEqual(applied.json()["operation_id"], replay.json()["operation_id"])
            self.assertFalse(applied.json()["idempotent_replay"])
            self.assertTrue(replay.json()["idempotent_replay"])

            db = self.factory()
            try:
                draft_count = db.fetch_one(
                    "SELECT COUNT(*) AS n FROM tasks WHERE title=?", ("Mobile draft task",),
                )["n"]
            finally:
                db.close()
            self.assertEqual(1, draft_count)

            undone = client.post(
                f"/v1/scheduling/operations/{applied.json()['operation_id']}/undo",
                headers=self.headers("token-u1", "future-mobile-undo"),
            )
            self.assertEqual(200, undone.status_code, undone.text)

        db = self.factory()
        try:
            remaining = db.fetch_one(
                "SELECT COUNT(*) AS n FROM tasks WHERE title=?", ("Mobile draft task",),
            )["n"]
        finally:
            db.close()
        self.assertEqual(0, remaining)

    def test_oversized_json_is_rejected_before_route_parsing(self) -> None:
        oversized = b'{"raw_input":"' + (b"x" * (256 * 1024)) + b'"}'
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/planning/draft",
                content=oversized,
                headers={
                    **self.headers("token-u1"),
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(413, response.status_code, response.text)
        self.assertEqual("PAYLOAD_TOO_LARGE", response.json()["code"])


if __name__ == "__main__":
    unittest.main()
