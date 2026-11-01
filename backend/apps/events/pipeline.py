"""이벤트 파이프라인 — relay와 소비자 그룹 4개를 한 프로세스의 스레드로 돌린다 (issue 290).

운영에서 relay·소비자를 컨테이너 5개(각자 Django 한 벌, 약 100MiB씩)로 돌리던 것을 한 프로세스로
합쳤다. 루프 코드는 그대로(`run_relay`·`run_consumer`)이고 이 모듈은 감독만 한다.

- 격리: 루프마다 스레드 하나. 루프가 예외로 빠져나오면(또는 멈추라는 말 없이 돌아오면) 그 루프만
  로그를 남기고 백오프 후 다시 시작한다. 다른 루프는 계속 돈다.
- 포기 기준: 한 루프가 `window`초 안에 `max_crashes`번 죽으면 전체를 멈추고 exit_code=1 —
  명령이 1로 끝나 docker `restart: unless-stopped`가 프로세스를 새로 띄운다.
- 종료: stop()(SIGTERM)이면 모든 루프가 다음 반복 경계에서 멈춘다. relay는 wake 대기(idle_drain)
  중일 수 있어 멈출 때 wake를 쏴 바로 깨운다.
- DB: Django 연결은 스레드마다 따로다. 루프가 반복마다 정리하고(`tidy_db_connections`), 스레드가
  끝날 때 그 스레드의 연결을 닫는다.
- relay는 outbox 순서 때문에 단일 인스턴스여야 한다 — 이 프로세스(컨테이너)는 replica 1개로 고정.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

StopFn = Callable[[], bool]
LoopFn = Callable[[StopFn], None]

LOOP_NAMES = ("relay", "webhook", "visitor-bridge", "console-bridge", "presence-bridge")


@dataclass(frozen=True)
class Loop:
    name: str
    run: LoopFn  # stop()이 True가 될 때까지 돈다


def relay_loop(idle_drain: float = 5.0) -> LoopFn:
    def run(stop: StopFn) -> None:
        from apps.events.relay import run_relay

        run_relay(idle_drain=idle_drain, stop=stop)
    return run


def consumer_loop(group: str, topic: str | None = None, block_ms: int = 1000) -> LoopFn:
    def run(stop: StopFn) -> None:
        import apps.events.handlers  # noqa: F401 — group→handler 등록
        from apps.events.consumer import run_consumer

        # 소비자 이름은 따로 돌던 컨테이너와 같다(f"{group}-1") — 옛 pending을 그대로 이어받는다.
        run_consumer(group, topic=topic, consumer=f"{group}-1", stop=stop, block_ms=block_ms)
    return run


def default_loops(exclude: tuple[str, ...] = ()) -> list[Loop]:
    from apps.events.types import (
        GROUP_CONSOLE_BRIDGE, GROUP_PRESENCE_BRIDGE, GROUP_VISITOR_BRIDGE, GROUP_WEBHOOK, PRESENCE_TOPIC,
    )

    loops = [
        Loop("relay", relay_loop()),
        Loop(GROUP_WEBHOOK, consumer_loop(GROUP_WEBHOOK)),
        Loop(GROUP_VISITOR_BRIDGE, consumer_loop(GROUP_VISITOR_BRIDGE)),
        Loop(GROUP_CONSOLE_BRIDGE, consumer_loop(GROUP_CONSOLE_BRIDGE)),
        Loop(GROUP_PRESENCE_BRIDGE, consumer_loop(GROUP_PRESENCE_BRIDGE, topic=PRESENCE_TOPIC)),
    ]
    return [lp for lp in loops if lp.name not in exclude]


def wake_relay() -> None:
    from apps.events.wake import notify_outbox

    notify_outbox()


class Supervisor:
    """루프들을 스레드로 띄우고 죽으면 다시 띄운다. run()은 모두 멈출 때까지 막는다."""

    def __init__(
        self,
        loops: list[Loop],
        *,
        backoff: float = 1.0,
        max_backoff: float = 30.0,
        max_crashes: int = 5,
        window: float = 300.0,
        join_timeout: float = 20.0,
        on_stop: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self.loops = loops
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.max_crashes = max_crashes
        self.window = window
        self.join_timeout = join_timeout
        self.on_stop = on_stop
        self.exit_code = 0
        self._stop = threading.Event()

    def stop(self) -> None:
        """멈추라고만 알린다(시그널 처리기에서 부르므로 I/O를 하지 않는다)."""
        self._stop.set()

    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> int:
        threads = [
            threading.Thread(target=self._supervise, args=(lp,), name=f"events-{lp.name}", daemon=True)
            for lp in self.loops
        ]
        for t in threads:
            t.start()
        logger.info("[pipeline] started loops=%s", ",".join(lp.name for lp in self.loops))
        self._stop.wait()
        logger.info("[pipeline] stopping")
        for hook in self.on_stop:
            try:
                hook()
            except Exception:  # noqa: BLE001 — 깨우기는 best-effort(못 깨우면 idle 대기 뒤 멈춘다)
                logger.warning("[pipeline] on_stop hook failed", exc_info=True)
        deadline = time.monotonic() + self.join_timeout
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        stuck = [t.name for t in threads if t.is_alive()]
        if stuck:
            logger.error("[pipeline] loops did not stop within %.0fs: %s", self.join_timeout, ",".join(stuck))
            self.exit_code = 1
        logger.info("[pipeline] stopped exit_code=%d", self.exit_code)
        return self.exit_code

    def _supervise(self, loop: Loop) -> None:
        from django.db import connections

        from apps.events.consumer import tidy_db_connections

        crashes: deque[float] = deque()
        try:
            while not self._stop.is_set():
                try:
                    loop.run(self.stopping)
                    if self._stop.is_set():
                        break
                    logger.error("[pipeline] loop=%s returned without stop — restarting", loop.name)
                except Exception:  # noqa: BLE001 — 이 루프만 다시 시작한다(격리)
                    logger.exception("[pipeline] loop=%s crashed", loop.name)
                now = time.monotonic()
                crashes.append(now)
                while crashes and now - crashes[0] > self.window:
                    crashes.popleft()
                if len(crashes) >= self.max_crashes:
                    logger.critical(
                        "[pipeline] loop=%s crashed %d times in %.0fs — exiting for docker restart",
                        loop.name, len(crashes), self.window,
                    )
                    self.exit_code = 1
                    self._stop.set()
                    break
                delay = min(self.backoff * 2 ** (len(crashes) - 1), self.max_backoff)
                logger.warning("[pipeline] loop=%s restarting in %.2fs", loop.name, delay)
                self._stop.wait(delay)
                tidy_db_connections()
        finally:
            connections.close_all()  # 이 스레드의 연결만(연결은 스레드 로컬)
