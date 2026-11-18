"""worker·events 생존 신호(#362). 프로세스 안 스레드가 주기적으로 redis 키를 갱신하고 파일을 만진다.

- redis 키 `flow:heartbeat:<역할>`(TTL 90초): `/api/health` 가 `HEALTH_REQUIRED_WORKERS` 에 적힌 역할의 키가 있는지 본다.
  없으면 health 가 503 이라 외부 감시가 알림으로 잡는다. api 가 여러 대여도(롤링 교체) 같은 키를 쓴다.
- 파일 `/tmp/flow-heartbeat`: 컨테이너 healthcheck 가 redis 없이 mtime 만 본다(unhealthy 표시).

스레드가 살아 있다는 신호지 태스크 소비를 증명하지는 않는다 — 프로세스가 죽거나 멈추거나 redis 에 못 닿으면 신호가 끊긴다.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "flow:heartbeat:"
INTERVAL_SECONDS = 15.0
TTL_SECONDS = 90
HEARTBEAT_FILE = Path("/tmp/flow-heartbeat")  # noqa: S108 — 컨테이너 안 healthcheck 전용
SOCKET_TIMEOUT = 1.0

_started: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, socket_connect_timeout=SOCKET_TIMEOUT, socket_timeout=SOCKET_TIMEOUT)


def beat(role: str, *, client: redis.Redis | None = None, path: Path | None = None) -> None:
    """신호 한 번. 파일은 redis 가 끊겨도 만진다(redis 장애는 health 의 redis 점검이 따로 잡는다)."""

    (path or HEARTBEAT_FILE).touch()
    (client or redis_client()).set(KEY_PREFIX + role, "1", ex=TTL_SECONDS)


def _run(role: str, interval: float) -> None:
    stop = threading.Event()
    while not stop.wait(interval):
        try:
            beat(role)
        except Exception:  # noqa: BLE001 — redis 일시 장애로 스레드가 죽으면 안 된다. 끊김은 TTL 이 드러낸다
            logger.warning("[heartbeat] %s beat failed", role, exc_info=True)


def start_heartbeat(role: str, *, interval: float = INTERVAL_SECONDS) -> None:
    """역할당 한 번만 띄운다(중복 호출 무시). 즉시 한 번 보내고 스레드로 주기 갱신한다."""

    with _lock:
        if role in _started:
            return
        try:
            beat(role)
        except Exception:  # noqa: BLE001 — 시작 시 redis 가 없어도 워커는 떠야 한다
            logger.warning("[heartbeat] %s initial beat failed", role, exc_info=True)
        t = threading.Thread(target=_run, args=(role, interval), name=f"heartbeat-{role}", daemon=True)
        _started[role] = t
        t.start()


def missing_roles(required: list[str], *, client: redis.Redis | None = None) -> list[str]:
    r = client or redis_client()
    return [role for role in required if not r.exists(KEY_PREFIX + role)]
