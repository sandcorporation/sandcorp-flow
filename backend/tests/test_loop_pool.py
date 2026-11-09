"""루프 기본 스레드 풀 크기(#322) — 동시 턴의 임베딩·http_tool 이 CPU 수에 묶여 줄을 서지 않는다."""

import asyncio
import threading

import pytest

from apps.core.loop_pool import (
    DEFAULT_LOOP_THREAD_POOL_SIZE,
    install_loop_thread_pool,
    loop_thread_pool_size,
    with_loop_thread_pool,
)

CONCURRENT = 20  # 2~4 vCPU 실행기의 파이썬 기본(CPU+4)보다 크고 32보다 작다


@pytest.mark.parametrize(("raw", "size"), [(None, 32), ("48", 48), ("0", 32), ("x", 32)])
def test_size_comes_from_env_with_default_32(monkeypatch, raw, size):
    if raw is None:
        monkeypatch.delenv("LOOP_THREAD_POOL_SIZE", raising=False)
    else:
        monkeypatch.setenv("LOOP_THREAD_POOL_SIZE", raw)
    assert DEFAULT_LOOP_THREAD_POOL_SIZE == 32
    assert loop_thread_pool_size() == size


async def _all_at_once() -> None:
    """CONCURRENT 개의 블로킹 호출이 동시에 풀 스레드에 있어야만 장벽을 넘는다(줄을 서면 BrokenBarrierError)."""
    barrier = threading.Barrier(CONCURRENT, timeout=5)
    await asyncio.gather(*(asyncio.to_thread(barrier.wait) for _ in range(CONCURRENT)))


async def test_test_loops_get_the_same_pool_as_production():
    await _all_at_once()  # conftest 의 event_loop_policy 가 install_loop_thread_pool 을 부른다


def test_install_sizes_a_fresh_loop():
    loop = asyncio.new_event_loop()  # 정책을 거치지 않는 맨 루프
    try:
        install_loop_thread_pool(loop)
        install_loop_thread_pool(loop)  # 두 번 걸어도 같다
        loop.run_until_complete(_all_at_once())
    finally:
        loop.close()


def test_asgi_wrapper_installs_on_first_request():
    seen: list[str] = []

    async def app(scope, receive, send):
        await _all_at_once()
        seen.append(scope["type"])

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        return None

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(with_loop_thread_pool(app)({"type": "http"}, receive, send))
    finally:
        loop.close()
    assert seen == ["http"]
