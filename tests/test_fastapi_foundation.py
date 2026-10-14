from __future__ import annotations

import json
import inspect
import os
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from backend_errors import RateLimited, VersionConflict
from fastapi_app import ApiReadinessProbe, create_app


class FastAPIFoundationTests(unittest.TestCase):
    def test_health_is_database_independent(self) -> None:
        calls = []
        app = create_app(readiness_check=lambda: calls.append(True))

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "healthy"}, response.json())
        self.assertEqual([], calls)

    def test_ready_status_and_output_are_safe(self) -> None:
        secret = "postgresql://user:secret@example.test/coachai"

        def unhealthy():
            return {
                "status": "unhealthy",
                "checked_at": "2026-10-12T00:00:00Z",
                "checks": {
                    "database": {
                        "status": "unhealthy",
                        "summary": "Database connection failed.",
                        "details": {"exception": secret},
                    }
                },
            }

        with TestClient(create_app(readiness_check=unhealthy)) as client:
            response = client.get("/ready")

        self.assertEqual(503, response.status_code)
        self.assertEqual("unhealthy", response.json()["status"])
        self.assertNotIn(secret, response.text)
        self.assertNotIn("details", response.text)

        healthy = lambda: {"status": "healthy", "checks": {}}
        with TestClient(create_app(readiness_check=healthy)) as client:
            self.assertEqual(200, client.get("/ready").status_code)

    def test_ready_probe_is_offloaded_from_the_asgi_event_loop(self) -> None:
        app = create_app(readiness_check=lambda: {"status": "healthy"})
        ready_route = next(
            route for route in app.routes if getattr(route, "path", None) == "/ready"
        )

        self.assertFalse(inspect.iscoroutinefunction(ready_route.endpoint))

    def test_default_readiness_rejects_missing_or_non_postgresql_config(self) -> None:
        for environment in ({}, {"DATABASE_URL": "sqlite:///not-runtime.db"}):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    snapshot = ApiReadinessProbe()()
                self.assertEqual("unhealthy", snapshot["status"])
                self.assertEqual(
                    "unhealthy", snapshot["checks"]["configuration"]["status"]
                )

    def test_readiness_passes_credential_preserving_url_object_to_factory(self) -> None:
        secret = "readiness-password-secret"
        database_url = f"postgresql+psycopg2://coachai:{secret}@localhost/coachai"
        received = []
        sentinel = object()
        fake_unit_of_work = ModuleType("unit_of_work")

        def database_factory(url):
            received.append(url)
            return sentinel

        fake_unit_of_work.DatabaseFactory = database_factory
        environment = {
            "DATABASE_URL": database_url,
            "COACHAI_EXTERNAL_TIMEOUT_SECONDS": "7",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch.dict(sys.modules, {"unit_of_work": fake_unit_of_work}):
                factory = ApiReadinessProbe()._factory()

        self.assertIs(sentinel, factory)
        self.assertEqual(1, len(received))
        self.assertIsInstance(received[0], URL)
        self.assertEqual(secret, received[0].password)
        self.assertEqual("7", received[0].query["connect_timeout"])
        self.assertNotIn(secret, str(received[0]))
        self.assertNotIn(secret, repr(received[0]))

    def test_backend_errors_use_stable_safe_envelope(self) -> None:
        app = create_app(readiness_check=lambda: {"status": "healthy"})

        @app.get("/_test/version-conflict")
        async def version_conflict():
            raise VersionConflict()

        @app.get("/_test/rate-limited")
        async def rate_limited():
            raise RateLimited(retry_after_seconds=3.4)

        with TestClient(app) as client:
            conflict = client.get("/_test/version-conflict")
            limited = client.get("/_test/rate-limited")

        self.assertEqual(409, conflict.status_code)
        self.assertEqual("VERSION_CONFLICT", conflict.json()["code"])
        self.assertEqual({}, conflict.json()["details"])
        self.assertEqual(32, len(conflict.json()["correlation_id"]))
        self.assertEqual(conflict.headers["X-Correlation-ID"], conflict.json()["correlation_id"])
        self.assertEqual(429, limited.status_code)
        self.assertEqual("3", limited.headers["Retry-After"])

    def test_validation_not_found_and_unexpected_errors_are_safe(self) -> None:
        secret = "provider-password-secret"
        app = create_app(readiness_check=lambda: {"status": "healthy"})

        @app.get("/_test/value/{value}")
        async def typed_value(value: int):
            return {"value": value}

        @app.get("/_test/unexpected")
        async def unexpected():
            raise RuntimeError(secret)

        with TestClient(app, raise_server_exceptions=False) as client:
            invalid = client.get("/_test/value/not-an-integer")
            missing = client.get("/does-not-exist")
            failed = client.get("/_test/unexpected")

        self.assertEqual("VALIDATION_ERROR", invalid.json()["code"])
        self.assertEqual("RESOURCE_NOT_FOUND", missing.json()["code"])
        self.assertEqual(500, failed.status_code)
        self.assertEqual("INTERNAL_ERROR", failed.json()["code"])
        self.assertNotIn(secret, json.dumps(failed.json()))


if __name__ == "__main__":
    unittest.main()
