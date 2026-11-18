"""relay — outbox를 EventBus로 드레인하는 단일(싱글톤) 프로세스 (issue 144).

정상 경로는 LISTEN/NOTIFY로 즉시 드레인하고, 부팅/재연결 시 미발행 outbox 행을 한 번
쓸어담는다(catch-up sweep — NOTIFY 유실 안전망). 단일 인스턴스라 outbox id 순서가 보존된다.
발행 성공한 행만 prune하고 event_store(감사)는 건드리지 않는다.
"""
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

RELAY_BACKOFF_SECONDS = 2.0  # 루프 오류(인프라 블립·발행 실패 등) 후 재시도 간격


# 배포 순간(db·redis 재시작)의 연결 오류는 warning, 이 문턱을 넘으면 exception(오류 이벤트).
CONNECTION_WARN_MAX_CONSECUTIVE = 5  # 연속 이 횟수 미만이면 warning
CONNECTION_WARN_MAX_SECONDS = 30.0  # 첫 실패로부터 이 시간 미만이면 warning


def _is_connection_error(exc: BaseException) -> bool:
    from django.db import InterfaceError, OperationalError
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return isinstance(exc, (OperationalError, InterfaceError, RedisConnectionError, RedisTimeoutError))


class _FailureStreak:
    """연속 연결 오류를 세어 warning/exception을 가른다. 성공하면 reset()."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._count = 0
        self._first_at = 0.0

    def reset(self) -> None:
        self._count = 0

    def log(self, exc: BaseException, message: str, *args: object) -> None:
        if not _is_connection_error(exc):
            logger.exception(message, *args)
            return
        now = self._clock()
        if self._count == 0:
            self._first_at = now
        self._count += 1
        if self._count < CONNECTION_WARN_MAX_CONSECUTIVE and now - self._first_at < CONNECTION_WARN_MAX_SECONDS:
            logger.warning("%s (%s: %s)", message % args if args else message, type(exc).__name__, exc)
        else:
            logger.exception(message, *args)


def drain_once(bus, batch=200) -> int:
    """미발행 outbox 행을 id 순서로 발행하고 prune한다. 발행 실패 시 그 행부터 남긴다(재발행)."""
    from apps.events.models import Outbox

    rows = list(Outbox.objects.filter(published_at__isnull=True).order_by("id")[:batch])
    published = 0
    for row in rows:
        bus.publish(row.topic, row.key, row.envelope)  # 실패하면 예외 전파 → 이 행은 prune 안 됨
        logger.info(
            "[relay] published topic=%s key=%s type=%s event_id=%s",
            row.topic, row.key, (row.envelope or {}).get("type"), (row.envelope or {}).get("event_id"),
        )
        row.delete()
        published += 1
    return published


def _close_pubsub(pubsub):
    """pubsub를 조용히 닫는다(재구독 전 정리). 실패해도 무시."""
    if pubsub is not None:
        try:
            from apps.events.wake import OUTBOX_WAKE_CHANNEL
            pubsub.unsubscribe(OUTBOX_WAKE_CHANNEL)
            pubsub.close()
        except Exception:
            pass
    return None


def run_relay(bus=None, idle_drain=5.0, stop=None, clock: Callable[[], float] = time.monotonic) -> None:
    """Redis pub/sub wake로 깨어 outbox를 드레인한다(부팅 sweep + 주기 backstop 포함).

    record_event가 커밋 후 쏘는 wake를 구독해 저지연으로 드레인하고, wake가 없어도 idle_drain
    마다 한 번 드레인해 유실된 wake를 회수한다(정합성 backstop). pg LISTEN/NOTIFY 대신 이미 쓰는
    Redis pub/sub라 psycopg3 비호환·플랫폼 문제가 없다.

    supervisor 루프: 부팅 sweep·구독·드레인 어디서 인프라 오류가 나도 프로세스를 죽이지 않고
    로그 + backoff 후 재구독/재시도한다(컨테이너 생존, 의존성 복구 시 자가 회복). 재연결 직후엔
    catch-up sweep로 끊긴 사이 쌓인 outbox를 회수한다.
    """
    from apps.events.bus import RedisStreamsBus
    from apps.events.consumer import tidy_db_connections
    from apps.events.wake import OUTBOX_WAKE_CHANNEL, _redis

    bus = bus or RedisStreamsBus()
    pubsub = None
    streak = _FailureStreak(clock)
    try:
        # 부팅 catch-up sweep — stop과 무관하게 1회 시도. 실패해도 루프가 재시도한다.
        try:
            pubsub = _redis().pubsub()
            pubsub.subscribe(OUTBOX_WAKE_CHANNEL)
            drain_once(bus)
            streak.reset()
        except Exception as exc:
            streak.log(exc, "[relay] 부팅 sweep/구독 실패 — 루프에서 재시도")
            pubsub = _close_pubsub(pubsub)

        while stop is None or not stop():
            tidy_db_connections()
            try:
                if pubsub is None:  # (재)연결 — 직후 catch-up sweep로 끊긴 사이 누락분 회수
                    pubsub = _redis().pubsub()
                    pubsub.subscribe(OUTBOX_WAKE_CHANNEL)
                    drain_once(bus)
                pubsub.get_message(timeout=idle_drain)  # wake 시 즉시, 없으면 idle_drain backstop
                drain_once(bus)
                streak.reset()
            except Exception as exc:  # noqa: BLE001 — 죽지 말고 재구독/재시도(컨테이너 생존)
                streak.log(exc, "[relay] 루프 오류 — %.1fs 후 재시도", RELAY_BACKOFF_SECONDS)
                pubsub = _close_pubsub(pubsub)
                time.sleep(RELAY_BACKOFF_SECONDS)
    finally:
        _close_pubsub(pubsub)
