"""소비자 런타임 — 멱등·제한재시도·DLQ (issue 145). 실 Redis Streams + Postgres."""
import uuid
import pytest


def _bus():
    from apps.events.bus import RedisStreamsBus
    return RedisStreamsBus()


def _topic():
    return f"test.consumer.{uuid.uuid4().hex}"


def _publish(bus, topic, **payload):
    payload.setdefault("event_id", str(uuid.uuid4()))
    bus.publish(topic, key="s", payload=payload)
    return payload["event_id"]


@pytest.mark.django_db
def test_success_acks_and_does_not_redeliver():
    from apps.events.consumer import EventConsumer
    bus, topic, group = _bus(), _topic(), "g"
    bus.ensure_group(topic, group)
    _publish(bus, topic, type="X")

    seen = []
    EventConsumer(bus, topic, group, "c", lambda env: seen.append(env["event_id"])).process_once(block_ms=200)

    assert len(seen) == 1
    assert bus.claim_stale(topic, group, "c2", min_idle_ms=0) == []  # acked → pending 없음


@pytest.mark.django_db
def test_duplicate_delivery_handled_once():
    from apps.events.consumer import EventConsumer
    bus, topic, group = _bus(), _topic(), "g"
    bus.ensure_group(topic, group)
    ev_id = _publish(bus, topic, type="X")

    calls = []
    ec = EventConsumer(bus, topic, group, "c", lambda env: calls.append(env["event_id"]))
    ec.process_once(block_ms=200)
    # 같은 event_id 재전달(중복) → 핸들러 재호출 없이 dedup
    bus.publish(topic, key="s", payload={"event_id": ev_id, "type": "X"})
    ec.process_once(block_ms=200)

    assert calls.count(ev_id) == 1


@pytest.mark.django_db
def test_consumer_logs_each_handled_event(caplog):
    """소비자가 처리한 이벤트를 INFO로 남긴다 — bridge 컨테이너의 docker logs로 흐름을 본다."""
    import logging
    from apps.events.consumer import EventConsumer
    bus, topic, group = _bus(), _topic(), "webhook"
    bus.ensure_group(topic, group)
    ev_id = _publish(bus, topic, type="SessionEscalated", aggregate_id="sess-42")

    ec = EventConsumer(bus, topic, group, "c", lambda env: None)
    with caplog.at_level(logging.INFO, logger="apps.events.consumer"):
        ec.process_once(block_ms=200)

    handled = [r.getMessage() for r in caplog.records if "handled" in r.getMessage()]
    assert any("SessionEscalated" in m and group in m and ev_id in m for m in handled)


@pytest.mark.django_db
def test_run_consumer_survives_transient_loop_error(monkeypatch):
    """소비자 루프는 일시 인프라 오류(Redis/DB 블립)에 죽지 않고 로그+재시도로 계속 돈다.

    가드가 없으면 process_once의 예외가 run_consumer를 종료시켜 컨테이너가 크래시루프에 빠진다.
    """
    from apps.events import consumer as cmod

    calls = {"n": 0}
    def flaky_process_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient redis blip")
        return 0
    monkeypatch.setattr(cmod.EventConsumer, "process_once", flaky_process_once)
    monkeypatch.setattr(cmod, "CONSUMER_BACKOFF_SECONDS", 0)  # 테스트 속도(실sleep 없음)
    cmod.register_handler("resilient-g", lambda env: None)

    # 1회째 예외 후에도 루프가 살아 process_once를 다시 부른다 → 3회 이후 stop.
    cmod.run_consumer("resilient-g", topic=f"test.resilient.{uuid.uuid4().hex}",
                      stop=lambda: calls["n"] >= 3)

    assert calls["n"] >= 3  # 예외 한 번에 죽지 않고 계속 처리


@pytest.mark.django_db
def test_poison_message_dead_lettered_after_max_attempts():
    from apps.events.consumer import EventConsumer
    bus, topic, group = _bus(), _topic(), "g"
    bus.ensure_group(topic, group)
    ev_id = _publish(bus, topic, type="X")

    calls = {"n": 0}
    def boom(env):
        calls["n"] += 1
        raise RuntimeError("handler boom")

    ec = EventConsumer(bus, topic, group, "c", boom, max_attempts=3)
    ec.process_once(block_ms=200)  # 예외 전파 없이 처리

    assert calls["n"] == 3                                    # 제한 횟수만 시도
    items = bus.dead_letter_items(topic)
    assert any(m.payload["event_id"] == ev_id for m in items)  # DLQ로 이동
    assert bus.claim_stale(topic, group, "c2", min_idle_ms=0) == []  # 원 PEL ack(멈추지 않음)


def _run_failing_consumer(monkeypatch, caplog, errors, clock_times):
    """process_once가 errors를 차례로 던지게(끝나면 성공) 하고 run_consumer를 돌려 [consumer] 로그를 돌려준다."""
    import logging

    from apps.events import consumer as cmod

    calls = {"n": 0}

    def process_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] <= len(errors):
            raise errors[calls["n"] - 1]
        return 0

    times = iter(clock_times)
    last = [0.0]

    def clock():
        last[0] = next(times, last[0])
        return last[0]

    monkeypatch.setattr(cmod.EventConsumer, "process_once", process_once)
    monkeypatch.setattr(cmod, "CONSUMER_BACKOFF_SECONDS", 0)
    cmod.register_handler("streak-g", lambda env: None)
    with caplog.at_level(logging.DEBUG, logger="apps.events.consumer"):
        cmod.run_consumer("streak-g", topic=f"test.streak.{uuid.uuid4().hex}",
                          stop=lambda: calls["n"] > len(errors) + 1, clock=clock)
    return [r for r in caplog.records if r.name == "apps.events.consumer" and "[consumer]" in r.getMessage()
            and r.levelno >= logging.WARNING]


def _conn_errors(n):
    from django.db import OperationalError

    return [OperationalError("connection refused") for _ in range(n)]


@pytest.mark.django_db
def test_consumer_connection_blip_is_warning(monkeypatch, caplog):
    import logging

    recs = _run_failing_consumer(monkeypatch, caplog, _conn_errors(3), [0.0] * 20)
    assert [r.levelno for r in recs] == [logging.WARNING] * 3
    assert all(r.exc_info is None for r in recs)


@pytest.mark.django_db
def test_consumer_connection_errors_escalate_after_five(monkeypatch, caplog):
    import logging

    recs = _run_failing_consumer(monkeypatch, caplog, _conn_errors(6), [0.0] * 40)
    assert [r.levelno for r in recs] == [logging.WARNING] * 4 + [logging.ERROR] * 2


@pytest.mark.django_db
def test_consumer_connection_errors_escalate_after_thirty_seconds(monkeypatch, caplog):
    import logging

    recs = _run_failing_consumer(monkeypatch, caplog, _conn_errors(2), [0.0, 31.0])
    assert [r.levelno for r in recs] == [logging.WARNING, logging.ERROR]


@pytest.mark.django_db
def test_consumer_non_connection_error_is_always_exception(monkeypatch, caplog):
    import logging

    recs = _run_failing_consumer(monkeypatch, caplog, [RuntimeError("bug")], [0.0] * 10)
    assert [r.levelno for r in recs] == [logging.ERROR]


@pytest.mark.django_db
def test_consumer_redis_connection_error_is_warning(monkeypatch, caplog):
    import logging

    import redis

    recs = _run_failing_consumer(monkeypatch, caplog, [redis.ConnectionError("down"), redis.TimeoutError("t")], [0.0] * 20)
    assert [r.levelno for r in recs] == [logging.WARNING] * 2
