"""e2e 부하 재현용 ASGI 계측 래퍼(#177) — 시험 compose 겹침 파일에서만 쓴다. 앱 코드가 아니다.

uvicorn 이 `soak_timing:app` 을 띄우면 Django ASGI 앱을 감싸 요청마다 한 줄(JSON)을 stderr 에 남긴다.

    {"k":"req","t":<끝난 시각>,"pid":..,"m":"GET","p":"/api/..","s":200,
     "ttfb":<응답 머리까지 ms>,"ms":<전체 ms>,"inf":<시작 순간 이 프로세스의 처리 중 요청 수>,
     "infmax":<요청 동안 본 최대 처리 중 수>,"sse":<응답이 event-stream 이면 true>}

이벤트 루프가 막히는지도 본다 — 100ms 마다 깨어나 늦게 깨면(>200ms) {"k":"lag","ms":..} 를 남긴다.
막힌 동안 감시 스레드가 루프 스레드의 스택을 떠서 {"k":"stack","stack":[..]} 로 남긴다(무엇이 막았는지).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from config.asgi import application as django_app

Scope = MutableMapping[str, Any]  # ASGI scope·message 는 사전 — 키 집합이 메시지 종류마다 다르다
Message = MutableMapping[str, Any]  # 위와 같은 까닭
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_PID = os.getpid()
class _Slot:
    """처리 중 요청 하나. 값이 아니라 정체성으로 지운다(dict 는 내용이 같으면 엉뚱한 것을 지운다)."""

    __slots__ = ("max",)

    def __init__(self, n: int) -> None:
        self.max = n


_active: list[_Slot] = []
_lag_started = False
_LAG_TICK = 0.1
_LAG_REPORT_MS = 200


def _emit(rec: dict[str, object]) -> None:
    sys.stderr.write("SOAK " + json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stderr.flush()


_beat = time.perf_counter()


def _watchdog(loop_thread_id: int) -> None:
    """루프 박동이 300ms 넘게 멈추면 루프 스레드 스택을 한 번 뜬다(멈춘 동안 한 번)."""
    dumped_for = 0.0
    while True:
        time.sleep(0.05)
        beat = _beat
        if time.perf_counter() - beat > 0.3 and dumped_for != beat:
            dumped_for = beat
            frame = sys._current_frames().get(loop_thread_id)
            if frame is not None:
                stack = [f"{fs.filename}:{fs.lineno} {fs.name}" for fs in traceback.extract_stack(frame)][-40:]
                _emit({"k": "stack", "t": round(time.time(), 3), "pid": _PID, "stack": stack})


async def _lag_monitor() -> None:
    global _beat
    threading.Thread(target=_watchdog, args=(threading.get_ident(),), daemon=True).start()
    while True:
        before = time.perf_counter()
        _beat = before
        await asyncio.sleep(_LAG_TICK)
        late_ms = (time.perf_counter() - before - _LAG_TICK) * 1000
        if late_ms > _LAG_REPORT_MS:
            _emit({"k": "lag", "t": round(time.time(), 3), "pid": _PID, "ms": round(late_ms), "inf": len(_active)})


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    global _lag_started
    if scope["type"] != "http":
        await django_app(scope, receive, send)
        return
    if not _lag_started:
        _lag_started = True
        asyncio.get_running_loop().create_task(_lag_monitor())

    me = _Slot(len(_active) + 1)
    inf0 = len(_active)
    _active.append(me)
    for other in _active:
        other.max = max(other.max, len(_active))
    start = time.perf_counter()
    status = 0
    ttfb: float | None = None
    sse = False

    async def timed_send(message: Message) -> None:
        nonlocal status, ttfb, sse
        if message["type"] == "http.response.start":
            status = int(message["status"])
            ttfb = (time.perf_counter() - start) * 1000
            for name, value in message.get("headers", []):
                if name.lower() == b"content-type" and value.startswith(b"text/event-stream"):
                    sse = True
        await send(message)

    try:
        await django_app(scope, receive, timed_send)
    finally:
        _active.remove(me)
        _emit({
            "k": "req",
            "t": round(time.time(), 3),
            "pid": _PID,
            "m": scope.get("method"),
            "p": scope.get("path"),
            "s": status,
            "ttfb": round(ttfb) if ttfb is not None else None,
            "ms": round((time.perf_counter() - start) * 1000),
            "inf": inf0,
            "infmax": me.max,
            "sse": sse,
        })
