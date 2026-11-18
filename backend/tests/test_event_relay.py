"""relay 드레인 (issue 144). 순서·prune·실패 재발행·sweep을 검증.

happy-path는 실 Redis Streams로, 발행 실패 주입은 controllable fake bus로(실 Redis는 결정적
실패를 못 만들므로 — relay 로직 검증이 관심사, bus 자체는 142에서 검증됨).
"""
import uuid
import pytest


def _topic():
    return f"test.relay.{uuid.uuid4().hex}"


@pytest.mark.django_db
def test_drains_in_order_prunes_outbox_keeps_event_store():
    from apps.events.store import record_event
    from apps.events.relay import drain_once
    from apps.events.models import Outbox, EventStore
    from apps.events.bus import RedisStreamsBus

    topic = _topic()
    bus = RedisStreamsBus()
    bus.ensure_group(topic, "g")
    for i in range(3):
        record_event("E", f"s{i}", "t", {"i": i}, topic=topic)

    n = drain_once(bus)

    assert n == 3
    assert Outbox.objects.filter(published_at__isnull=True).count() == 0  # 발행분 prune
    assert EventStore.objects.count() == 3                                # 감사 불변
    msgs = bus.consume(topic, "g", "c", count=10, block_ms=300)
    assert [m.payload["payload"]["i"] for m in msgs] == [0, 1, 2]         # id 순서


class _OkBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, key, payload):
        self.published.append(payload["payload"]["i"])


class _FlakyBus(_OkBus):
    def __init__(self, fail_on):
        super().__init__()
        self.calls = 0
        self.fail_on = fail_on

    def publish(self, topic, key, payload):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("publish boom")
        super().publish(topic, key, payload)


@pytest.mark.django_db
def test_failed_publish_leaves_remaining_unpublished_and_retries():
    from apps.events.store import record_event
    from apps.events.relay import drain_once
    from apps.events.models import Outbox

    topic = _topic()
    for i in range(3):
        record_event("E", f"s{i}", "t", {"i": i}, topic=topic)

    flaky = _FlakyBus(fail_on=2)
    with pytest.raises(RuntimeError):
        drain_once(flaky)

    assert flaky.published == [0]                                          # 1건만 발행
    assert Outbox.objects.filter(published_at__isnull=True).count() == 2   # 나머지 미발행 유지

    ok = _OkBus()
    drain_once(ok)  # sweep/재시도
    assert ok.published == [1, 2]                                          # 남은 것 순서대로 재발행
    assert Outbox.objects.filter(published_at__isnull=True).count() == 0


@pytest.mark.django_db(transaction=True)
def test_record_event_publishes_wake_on_commit(redis_subscribe):
    """record_event는 커밋 후 Redis pub/sub로 relay wake를 쏜다(폴링 대신 저지연 wake)."""
    from django.db import transaction
    from apps.events.store import record_event
    from apps.events.wake import OUTBOX_WAKE_CHANNEL

    pubsub = redis_subscribe(OUTBOX_WAKE_CHANNEL)
    with transaction.atomic():
        record_event("E", "s", "t", {}, topic=_topic())
    # 커밋 시 on_commit → wake 발행
    got = False
    for _ in range(20):
        m = pubsub.get_message(timeout=0.5)
        if m and m["type"] == "message":
            got = True
            break
    assert got


@pytest.mark.django_db
def test_run_relay_boot_sweep_drains_and_respects_stop():
    """run_relay는 부팅 sweep으로 미발행 outbox를 드레인하고 stop을 따른다(pubsub 구독 정상)."""
    from apps.events.store import record_event
    from apps.events.relay import run_relay
    from apps.events.models import Outbox
    from apps.events.bus import RedisStreamsBus

    topic = _topic()
    bus = RedisStreamsBus()
    bus.ensure_group(topic, "g")
    record_event("E", "s", "t", {"i": 0}, topic=topic)

    run_relay(bus=bus, idle_drain=0.1, stop=lambda: True)  # 부팅 sweep 후 즉시 중단

    assert Outbox.objects.filter(published_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_run_relay_survives_transient_error(monkeypatch):
    """relay 루프(부팅 sweep 포함)는 일시 오류에 죽지 않고 로그+재시도로 계속 돈다.

    가드가 없으면 부팅 sweep의 drain_once 예외가 run_relay를 종료시켜 컨테이너가 크래시한다.
    """
    from apps.events import relay as rmod

    calls = {"n": 0}
    def flaky_drain(bus, batch=200):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient blip")  # 부팅 sweep에서 터진다
        return 0
    monkeypatch.setattr(rmod, "drain_once", flaky_drain)
    monkeypatch.setattr(rmod, "RELAY_BACKOFF_SECONDS", 0)  # 테스트 속도

    rmod.run_relay(bus=object(), idle_drain=0.01, stop=lambda: calls["n"] >= 3)

    assert calls["n"] >= 3  # 부팅 sweep 예외에도 죽지 않고 루프 지속


def _run_failing_relay(monkeypatch, caplog, errors, clock_times):
    """errors를 차례로 drain_once가 던지게 하고(끝나면 성공), relay 루프를 돌려 로그 레코드를 돌려준다."""
    import logging

    from apps.events import relay as rmod

    calls = {"n": 0}

    def drain(bus, batch=200):
        calls["n"] += 1
        if calls["n"] <= len(errors):
            raise errors[calls["n"] - 1]
        return 0

    times = iter(clock_times)
    last = [0.0]

    def clock():
        last[0] = next(times, last[0])
        return last[0]

    monkeypatch.setattr(rmod, "drain_once", drain)
    monkeypatch.setattr(rmod, "RELAY_BACKOFF_SECONDS", 0)
    with caplog.at_level(logging.DEBUG, logger="apps.events.relay"):
        rmod.run_relay(bus=object(), idle_drain=0.01, stop=lambda: calls["n"] > len(errors) + 1, clock=clock)
    return [r for r in caplog.records if r.name == "apps.events.relay" and "[relay]" in r.getMessage()
            and r.levelno >= logging.WARNING]


def _conn_errors(n):
    from django.db import OperationalError

    return [OperationalError("connection refused") for _ in range(n)]


@pytest.mark.django_db
def test_connection_blip_is_warning_not_exception(monkeypatch, caplog):
    """배포 순간의 짧은 연결 오류(5회 미만·30초 미만)는 warning — 오류 이벤트가 되지 않는다."""
    import logging

    recs = _run_failing_relay(monkeypatch, caplog, _conn_errors(3), [0.0] * 20)
    assert [r.levelno for r in recs] == [logging.WARNING] * 3
    assert all(r.exc_info is None for r in recs)


@pytest.mark.django_db
def test_connection_errors_escalate_after_five_consecutive(monkeypatch, caplog):
    """연속 5회째부터는 exception(logger.exception = ERROR)."""
    import logging

    recs = _run_failing_relay(monkeypatch, caplog, _conn_errors(6), [0.0] * 40)
    assert [r.levelno for r in recs] == [logging.WARNING] * 4 + [logging.ERROR] * 2
    assert recs[-1].exc_info is not None


@pytest.mark.django_db
def test_connection_errors_escalate_after_thirty_seconds(monkeypatch, caplog):
    """첫 실패로부터 30초가 지나면 횟수와 무관하게 exception."""
    import logging

    # 시계는 실패 기록 때만 읽힌다: 첫 실패 t=0, 두 번째 t=31
    recs = _run_failing_relay(monkeypatch, caplog, _conn_errors(2), [0.0, 31.0])
    assert [r.levelno for r in recs] == [logging.WARNING, logging.ERROR]


@pytest.mark.django_db
def test_non_connection_error_is_always_exception(monkeypatch, caplog):
    import logging

    recs = _run_failing_relay(monkeypatch, caplog, [RuntimeError("bug")], [0.0] * 10)
    assert [r.levelno for r in recs] == [logging.ERROR]


@pytest.mark.django_db
def test_redis_connection_error_is_warning(monkeypatch, caplog):
    import logging

    import redis

    recs = _run_failing_relay(monkeypatch, caplog, [redis.ConnectionError("down"), redis.TimeoutError("t")], [0.0] * 20)
    assert [r.levelno for r in recs] == [logging.WARNING] * 2
