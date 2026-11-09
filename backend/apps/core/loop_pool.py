"""이벤트 루프 기본 스레드 풀 크기(#322).

턴의 임베딩 HTTP(``sync_to_async(thread_sensitive=False)``)와 http_tool(``asyncio.to_thread``)은 루프의 **기본 풀**에서 돈다.
파이썬 기본 크기는 ``min(32, CPU+4)``라 운영 oracle(4코어 → 8)에서 동시 턴이 8개를 넘으면 느린 외부 호출이 줄을 서고,
그동안 kb 없는 턴의 단계도 풀 자리를 기다린다. 그래서 턴이 도는 모든 루프(taskiq 워커·api uvicorn·시험)에 같은 크기를 건다.

크기는 ``LOOP_THREAD_POOL_SIZE``(기본 32) — taskiq ``--max-async-tasks``(운영 25)보다 작게 두지 않는다.
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import Awaitable, Callable, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

DEFAULT_LOOP_THREAD_POOL_SIZE = 32

_installed: weakref.WeakSet[asyncio.AbstractEventLoop] = weakref.WeakSet()


def loop_thread_pool_size() -> int:
    """``LOOP_THREAD_POOL_SIZE``(1 이상), 없거나 잘못되면 32."""
    try:
        size = int(os.environ.get("LOOP_THREAD_POOL_SIZE", ""))
    except ValueError:
        return DEFAULT_LOOP_THREAD_POOL_SIZE
    return size if size >= 1 else DEFAULT_LOOP_THREAD_POOL_SIZE


def install_loop_thread_pool(loop: asyncio.AbstractEventLoop) -> None:
    """``loop``의 기본 풀을 ``loop_thread_pool_size()`` 크기로 바꾼다. 같은 루프에 두 번 걸면 아무것도 하지 않는다."""
    if loop in _installed:
        return
    loop.set_default_executor(ThreadPoolExecutor(max_workers=loop_thread_pool_size(), thread_name_prefix="loop-pool"))
    _installed.add(loop)


Scope = MutableMapping[str, Any]  # ASGI 경계 — 스펙이 dict 모양만 정한다
Message = MutableMapping[str, Any]  # ASGI 경계
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def with_loop_thread_pool(app: ASGIApp) -> ASGIApp:
    """uvicorn(``--lifespan off``라 시작 걸이가 없다)의 루프에 첫 요청 때 풀을 건다."""

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        install_loop_thread_pool(asyncio.get_running_loop())
        await app(scope, receive, send)

    return wrapped
