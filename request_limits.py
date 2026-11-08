"""ASGI request-body limits applied before framework body parsing."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        *,
        json_limit: int = 256 * 1024,
        audio_limit: int = 15 * 1024 * 1024,
        default_limit: int = 1024 * 1024,
    ) -> None:
        self.app = app
        self.json_limit = json_limit
        self.audio_limit = audio_limit
        self.default_limit = default_limit

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            limit = self.json_limit
        elif content_type.startswith("audio/"):
            limit = self.audio_limit
        else:
            limit = self.default_limit
        try:
            declared = int(headers.get("content-length", "0") or "0")
        except ValueError:
            declared = -1
        if declared < 0 or declared > limit:
            await self._reject(send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge()
            return message

        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._reject(send)
                return
            raise

    @staticmethod
    async def _reject(send) -> None:
        payload = json.dumps({
            "code": "PAYLOAD_TOO_LARGE",
            "message": "The request body is too large.",
            "details": {},
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": payload})
