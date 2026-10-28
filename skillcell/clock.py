from __future__ import annotations

import time
from typing import Protocol


def now_ms() -> int:
    return int(time.time() * 1000)


class Clock(Protocol):
    def now_ms(self) -> int: ...


class SystemClock:
    def now_ms(self) -> int:
        return now_ms()
