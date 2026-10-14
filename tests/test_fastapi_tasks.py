from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


class FastAPITaskRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "api-tasks.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")
        db = self.factory()
        try:
            for user_id in ("u1", "u2"):
                db.create_user(user_id, f"{user_id}@example.test", user_id, "UTC")
            self.plan_u1 = db.create_plan("u1", date(2099, 1, 1), "u1 plan")
            self.plan_u2 = db.create_plan("u2", date(2099, 1, 1), "u2 plan")
            self.task_u1 = db.add_task(
                self.plan_u1, "u1", "U1 task", estimated_minutes=30
            )
            self.task_u2 = db.add_task(
                self.plan_u2, "u2", "U2 task", estimated_minutes=45
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
        values = {"Authorization": f"Bearer {token}"}
        if key is not None:
            values["Idempotency-Key"] = key
        return values

    def task(self, task_id: int, user_id: str):
        db = self.factory()
        try:
            return db.get_task(task_id, user_id)
        finally:
            db.close()

    def test_listing_is_paginated_and_principal_scoped(self) -> None:
        with TestClient(self.app) as client:
            u1 = client.get("/v1/tasks?limit=1", headers=self.headers("token-u1"))
            u2 = client.get("/v1/tasks", headers=self.headers("token-u2"))

        self.assertEqual(200, u1.status_code)
        self.assertEqual([self.task_u1], [item["task_id"] for item in u1.json()["items"]])
        self.assertIsNone(u1.json()["next_offset"])
        self.assertEqual([self.task_u2], [item["task_id"] for item in u2.json()["items"]])

    def test_update_is_versioned_idempotent_and_retry_safe(self) -> None:
        payload = {"expected_version": 0, "title": "Updated"}
        headers = self.headers("token-u1", "update-key")
        with TestClient(self.app) as client:
            first = client.patch(f"/v1/tasks/{self.task_u1}", json=payload, headers=headers)
            replay = client.patch(f"/v1/tasks/{self.task_u1}", json=payload, headers=headers)
            mismatch = client.patch(
                f"/v1/tasks/{self.task_u1}",
                json={"expected_version": 0, "title": "Different"},
                headers=headers,
            )
            stale = client.patch(
                f"/v1/tasks/{self.task_u1}",
                json={"expected_version": 0, "description": "stale"},
                headers=self.headers("token-u1", "stale-key"),
            )

        self.assertEqual(200, first.status_code)
        self.assertEqual(1, first.json()["version"])
        self.assertFalse(first.json()["idempotent_replay"])
        self.assertEqual(200, replay.status_code)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(409, mismatch.status_code)
        self.assertEqual("IDEMPOTENCY_CONFLICT", mismatch.json()["code"])
        self.assertEqual(409, stale.status_code)
        self.assertEqual("VERSION_CONFLICT", stale.json()["code"])
        self.assertEqual("Updated", self.task(self.task_u1, "u1")["title"])

    def test_cross_tenant_and_invalid_requests_are_safe(self) -> None:
        with TestClient(self.app) as client:
            foreign = client.patch(
                f"/v1/tasks/{self.task_u2}",
                json={"expected_version": 0, "title": "steal"},
                headers=self.headers("token-u1", "foreign-key"),
            )
            invalid = client.patch(
                f"/v1/tasks/{self.task_u1}",
                json={"expected_version": 0, "title": "   ", "unknown": 1},
                headers=self.headers("token-u1", "invalid-key"),
            )

        self.assertEqual(404, foreign.status_code)
        self.assertEqual("RESOURCE_NOT_FOUND", foreign.json()["code"])
        self.assertEqual(422, invalid.status_code)
        self.assertEqual("VALIDATION_ERROR", invalid.json()["code"])
        self.assertNotIn("unknown", invalid.text)

    def test_failure_after_mutation_rolls_back_route_transaction(self) -> None:
        with patch(
            "mobile_repository.MobileRepository.record_change",
            side_effect=RuntimeError("after mutation"),
        ):
            with TestClient(self.app, raise_server_exceptions=False) as client:
                response = client.patch(
                    f"/v1/tasks/{self.task_u1}",
                    json={"expected_version": 0, "title": "Must roll back"},
                    headers=self.headers("token-u1", "rollback-key"),
                )

        self.assertEqual(500, response.status_code)
        self.assertEqual("INTERNAL_ERROR", response.json()["code"])
        task = self.task(self.task_u1, "u1")
        self.assertEqual("U1 task", task["title"])
        self.assertEqual(0, task["version"])
        self.assertEqual(0, self.factory.pool_metrics()["checked_out"])

    def test_openapi_matches_the_implemented_slice(self) -> None:
        with TestClient(self.app) as client:
            spec = client.get("/openapi.json").json()

        self.assertEqual(
            {"/v1/tasks", "/v1/tasks/{task_id}"},
            set(spec["paths"]),
        )
        operation = spec["paths"]["/v1/tasks/{task_id}"]["patch"]
        self.assertIn("Idempotency-Key", [p["name"] for p in operation["parameters"]])
        self.assertIn("422", operation["responses"])

    def test_database_integer_and_pagination_bounds_fail_before_access(self) -> None:
        enormous = 10**100
        with TestClient(self.app) as client:
            offset = client.get(
                f"/v1/tasks?offset={enormous}", headers=self.headers("token-u1")
            )
            task_id = client.patch(
                f"/v1/tasks/{enormous}",
                json={"expected_version": 0, "title": "bounded"},
                headers=self.headers("token-u1", "bounded-id"),
            )
            version = client.patch(
                f"/v1/tasks/{self.task_u1}",
                json={"expected_version": enormous, "title": "bounded"},
                headers=self.headers("token-u1", "bounded-version"),
            )

        for response in (offset, task_id, version):
            self.assertEqual(422, response.status_code)
            self.assertEqual("VALIDATION_ERROR", response.json()["code"])
            self.assertNotIn(str(enormous), response.text)
        self.assertEqual("U1 task", self.task(self.task_u1, "u1")["title"])


if __name__ == "__main__":
    unittest.main()
