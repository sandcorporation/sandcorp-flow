"""이벤트 루프 지연 탐침(#256) — 시험이 「이 동안 루프가 막혔는가」를 잴 때 쓴다.

``interval``마다 깨어나는 코루틴이 예정보다 늦게 깬 만큼을 모은다. 루프에서 동기로 도는 일(블로킹 I/O·긴 계산)이
있으면 그만큼 깨어남이 밀려 ``max_lag``가 커진다. 스레드로 보낸 일(``sync_to_async``)은 루프를 막지 않으므로 늘지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from types import TracebackType


class LoopProbe:
    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.lags: list[float] = []
        self.at: list[float] = []
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            planned = time.monotonic() + self.interval
            await asyncio.sleep(self.interval)
            now = time.monotonic()
            self.lags.append(max(0.0, now - planned))
            self.at.append(now)

    @property
    def max_lag(self) -> float:
        return max(self.lags, default=0.0)

    async def __aenter__(self) -> LoopProbe:
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        assert self._task is not None
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
