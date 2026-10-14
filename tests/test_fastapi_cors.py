from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend_config import cors_allowed_origins
from backend_errors import ConfigurationError
from fastapi_app import create_app


class FastAPICorsTests(unittest.TestCase):
    def app(self):
        return create_app(
            readiness_check=lambda: {"status": "healthy", "checks": {}},
            cors_allowed_origins=("https://frontend.example",),
        )

    def test_allowed_origin_get_and_preflight(self) -> None:
        with TestClient(self.app()) as client:
            response = client.get(
                "/health", headers={"Origin": "https://frontend.example"}
            )
            preflight = client.options(
                "/v1/tasks",
                headers={
                    "Origin": "https://frontend.example",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )

        self.assertEqual("https://frontend.example", response.headers["Access-Control-Allow-Origin"])
        self.assertEqual(200, preflight.status_code)
        self.assertEqual("https://frontend.example", preflight.headers["Access-Control-Allow-Origin"])
        self.assertNotEqual("*", preflight.headers["Access-Control-Allow-Origin"])
        self.assertNotIn("Access-Control-Allow-Credentials", preflight.headers)

    def test_rejected_origin_receives_no_allow_origin_header(self) -> None:
        with TestClient(self.app()) as client:
            response = client.get(
                "/health", headers={"Origin": "https://attacker.example"}
            )
            preflight = client.options(
                "/v1/tasks",
                headers={
                    "Origin": "https://attacker.example",
                    "Access-Control-Request-Method": "GET",
                },
            )

        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertEqual(400, preflight.status_code)
        self.assertNotIn("Access-Control-Allow-Origin", preflight.headers)

    def test_wildcards_credentials_and_paths_are_rejected(self) -> None:
        invalid = (
            "*",
            "https://*.example",
            "https://user:secret@example.test",
            "https://frontend.example/path",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                cors_allowed_origins(value)


if __name__ == "__main__":
    unittest.main()
