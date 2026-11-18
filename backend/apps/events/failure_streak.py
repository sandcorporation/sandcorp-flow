"""루프 오류 로그 수준 판정 — relay·소비자 supervisor 루프가 함께 쓴다(#352·#355).

배포 순간(db·redis 재시작)의 연결 오류는 warning, 문턱을 넘으면 exception(오류 이벤트).
"""
import logging
from collections.abc import Callable

CONNECTION_WARN_MAX_CONSECUTIVE = 5  # 연속 이 횟수 미만이면 warning
CONNECTION_WARN_MAX_SECONDS = 30.0  # 첫 실패로부터 이 시간 미만이면 warning


def is_connection_error(exc: BaseException) -> bool:
    from django.db import InterfaceError, OperationalError
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return isinstance(exc, (OperationalError, InterfaceError, RedisConnectionError, RedisTimeoutError))


class FailureStreak:
    """연속 연결 오류를 세어 warning/exception을 가른다. 성공하면 reset()."""

    def __init__(self, clock: Callable[[], float], logger: logging.Logger):
        self._clock = clock
        self._logger = logger
        self._count = 0
        self._first_at = 0.0

    def reset(self) -> None:
        self._count = 0

    def log(self, exc: BaseException, message: str, *args: object) -> None:
        if not is_connection_error(exc):
            self._logger.exception(message, *args)
            return
        now = self._clock()
        if self._count == 0:
            self._first_at = now
        self._count += 1
        if self._count < CONNECTION_WARN_MAX_CONSECUTIVE and now - self._first_at < CONNECTION_WARN_MAX_SECONDS:
            self._logger.warning("%s (%s: %s)", message % args if args else message, type(exc).__name__, exc)
        else:
            self._logger.exception(message, *args)
