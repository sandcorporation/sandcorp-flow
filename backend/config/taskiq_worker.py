"""taskiq 워커 부트스트랩 (ADR-0024) — `taskiq worker config.taskiq_worker:broker`로 기동.

ORM·settings를 쓰기 전에 Django를 셋업하고, chat 태스크를 임포트해 broker에 등록한다.
웹(uvicorn/asgi)·테스트(pytest)는 이 모듈을 임포트하지 않는다 — 각자 Django를 따로 셋업하므로
여기서의 django.setup()은 워커 프로세스 전용이다.

장수 워커라 요청 경계가 없으므로 Django 커넥션을 직접 정리해야 한다(Celery의 Django fixup 대응).
DB가 커넥션을 끊으면(재시작·타임아웃) 다음 태스크가 "connection already closed"로 깨지는 것을
close_old_connections로 막는다. ORM은 sync_to_async(thread_sensitive)의 전용 스레드에서 도므로
정리도 그 스레드에서 호출해야 효과가 있다(이벤트루프 스레드의 connections는 별개).
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
django.setup()

import asyncio  # noqa: E402

from taskiq import TaskiqEvents, TaskiqMessage, TaskiqMiddleware, TaskiqResult, TaskiqState  # noqa: E402

from apps.core.loop_pool import install_loop_thread_pool  # noqa: E402

from config.taskiq_broker import broker  # noqa: E402
import apps.chat.chat_task  # noqa: E402,F401 — @broker.task 등록(import 부작용)


class DjangoORMConnCleanupMiddleware(TaskiqMiddleware):
    """태스크 실행 전후로 ORM 스레드의 낡은/끊긴 Django 커넥션을 정리한다."""

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        await self._close()
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult) -> None:
        await self._close()

    @staticmethod
    async def _close() -> None:
        from asgiref.sync import sync_to_async
        from django.db import close_old_connections

        # thread_sensitive=True: ORM이 쓰는 그 전용 스레드에서 닫아야 실제 커넥션이 정리된다.
        await sync_to_async(close_old_connections, thread_sensitive=True)()


# InMemoryBroker(테스트)에는 붙이지 않는다 — django_db 테스트의 커넥션을 중간에 닫아
# 격리를 깨뜨릴 수 있다. prod redis 워커에서만 커넥션 위생을 강제한다.
if os.environ.get("TASKIQ_INMEMORY") != "1":
    broker.add_middlewares(DjangoORMConnCleanupMiddleware())


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _loop_thread_pool(state: TaskiqState) -> None:
    """턴의 임베딩·http_tool이 도는 루프 기본 풀을 --max-async-tasks 이상으로(#322)."""
    install_loop_thread_pool(asyncio.get_running_loop())
