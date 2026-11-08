from __future__ import annotations

import json
import unittest

from request_limits import RequestBodyLimitMiddleware


def _run_immediate(coroutine) -> None:
    """Drive ASGI coroutines whose test doubles never block on I/O."""
    while True:
        try:
            coroutine.send(None)
        except StopIteration:
            return


class RequestBodyLimitMiddlewareTests(unittest.TestCase):
    @staticmethod
    def scope(*, content_length: int | None = None) -> dict:
        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode("ascii")))
        return {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": "/v1/future-planning/preview",
            "raw_path": b"/v1/future-planning/preview",
            "query_string": b"",
            "headers": headers,
            "client": ("test", 1),
            "server": ("test", 80),
            "scheme": "http",
        }

    def test_declared_oversized_json_is_rejected_without_calling_app(self) -> None:
        called = False

        async def app(scope, receive, send):
            nonlocal called
            called = True

        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        middleware = RequestBodyLimitMiddleware(app, json_limit=8)
        _run_immediate(middleware(self.scope(content_length=9), receive, send))

        self.assertFalse(called)
        self.assertEqual(413, messages[0]["status"])
        payload = json.loads(messages[1]["body"])
        self.assertEqual("PAYLOAD_TOO_LARGE", payload["code"])

    def test_streamed_oversized_json_is_rejected_without_content_length(self) -> None:
        chunks = iter((
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ))
        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        async def receive():
            return next(chunks)

        async def app(scope, receive, send):
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = RequestBodyLimitMiddleware(app, json_limit=8)
        _run_immediate(middleware(self.scope(), receive, send))

        self.assertEqual(413, messages[0]["status"])
        self.assertEqual("PAYLOAD_TOO_LARGE", json.loads(messages[1]["body"])["code"])


if __name__ == "__main__":
    unittest.main()
