from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class TreeCache:
    def __init__(self, ttl_s: int, loader: Callable[[], Awaitable[dict[str, Any]]]):
        self.ttl_s = max(1, ttl_s)
        self.loader = loader
        self._value: dict[str, Any] | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, force: bool = False) -> dict[str, Any]:
        fresh = self._value is not None and time.monotonic() - self._loaded_at < self.ttl_s
        if fresh and not force:
            return self._value

        async with self._lock:
            fresh = self._value is not None and time.monotonic() - self._loaded_at < self.ttl_s
            if fresh and not force:
                return self._value
            self._value = await self.loader()
            self._loaded_at = time.monotonic()
            return self._value
