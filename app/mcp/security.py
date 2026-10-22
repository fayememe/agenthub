from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections import deque
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

logger = logging.getLogger("app.mcp.access")


class AccessMiddleware:
    """Bearer auth, rate limiting and request audit for the MCP endpoint."""

    def __init__(
        self,
        app: Any,
        token: str | None,
        rate_limit_per_min: int,
        health_path: str = "/healthz",
    ):
        self.app = app
        self.token = token or None
        self.rate_limit_per_min = max(1, rate_limit_per_min)
        self.health_path = health_path
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    def _client_key(self, scope: dict[str, Any]) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])
        return "unknown"

    def _authorized(self, headers: Headers) -> bool:
        if self.token is None:
            return True
        value = headers.get("authorization", "")
        prefix = "bearer "
        if not value.lower().startswith(prefix):
            return False
        return hmac.compare_digest(value[len(prefix) :].strip(), self.token)

    async def _rate_ok(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path == self.health_path:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        client = self._client_key(scope)
        authorized = self._authorized(headers)
        bucket = "auth" if authorized else "unauth"
        limit = (
            self.rate_limit_per_min
            if authorized
            else max(5, self.rate_limit_per_min // 3)
        )
        key = f"{bucket}:{client}"
        if not await self._rate_ok(key, limit):
            response = JSONResponse(
                {"error": "rate_limited", "error_description": "too many requests"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        if not authorized:
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "error_description": "a valid bearer token is required",
                },
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="sah-mcp"'},
            )
            await response(scope, receive, send)
            return

        started = time.monotonic()
        status = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "mcp request method=%s path=%s status=%s duration_ms=%s client=%s",
                scope.get("method"),
                path,
                status,
                duration_ms,
                key,
            )
