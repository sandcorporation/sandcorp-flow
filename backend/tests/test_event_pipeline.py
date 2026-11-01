"""run_event_pipeline — relay와 소비자 그룹 4개를 한 프로세스에서 (issue 290).

실 Redis·DB로 검증한다. 루프는 스레드에서 돌아 테스트 트랜잭션을 볼 수 없으므로 transaction=True.
"""
import os
import signal
import threading
import time
import uuid

import pytest
from django.core.management import call_command


def _wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)  # wait-ok: 다른 스레드의 처리 완료를 폴링(사건 훅 없음)
    return False


def _crashing_loop(counter):
    def loop(stop):
        counter.append(1)
        raise RuntimeError("boom")
    return loop


@pytest.mark.django_db(transaction=True)
def test_crashing_group_is_restarted_while_relay_and_other_group_keep_working(tenant_with_key, webhook_server):
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation
    from apps.events.bus import RedisStreamsBus
    from apps.events.pipeline import Loop, Supervisor, consumer_loop, relay_loop
    from apps.events.store import record_event
    from apps.events.types import GROUP_WEBHOOK, SESSION_ESCALATED
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.webhook_url, config.webhook_type = webhook_server["url"], "generic"
    config.save()
    sess = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v")
    esc = Escalation.objects.create(session=sess, trigger_type="ai", status="pending", reason="r")

    topic = f"test.pipeline.{uuid.uuid4().hex}"
    RedisStreamsBus().ensure_group(topic, GROUP_WEBHOOK)
    crashes = []
    sup = Supervisor(
        [
            Loop("broken", _crashing_loop(crashes)),
            Loop("relay", relay_loop(idle_drain=0.2)),
            Loop(GROUP_WEBHOOK, consumer_loop(GROUP_WEBHOOK, topic=topic, block_ms=100)),
        ],
        backoff=0.01, max_backoff=0.05, max_crashes=10_000, window=60,
    )
    runner = threading.Thread(target=sup.run)
    runner.start()
    try:
        # relay가 outbox를 발행하고 webhook 그룹이 처리한다 — broken 그룹이 계속 죽는 동안에도.
        record_event(SESSION_ESCALATED, str(sess.id), str(tenant.id), {"escalation_id": str(esc.id)},
                     topic=topic)
        assert _wait_until(lambda: len(webhook_server["received"]) == 1)
        assert _wait_until(lambda: len(crashes) >= 3)  # 죽은 그룹만 백오프 후 다시 시작
    finally:
        sup.stop()
        runner.join(timeout=5)
    assert not runner.is_alive()
    assert sup.exit_code == 0


def test_group_that_keeps_crashing_stops_the_whole_pipeline_with_failure():
    from apps.events.pipeline import Loop, Supervisor

    crashes = []
    healthy_stopped = threading.Event()

    def healthy(stop):
        while not stop():
            time.sleep(0.01)  # wait-ok: 할 일 없는 가짜 루프
        healthy_stopped.set()

    sup = Supervisor([Loop("broken", _crashing_loop(crashes)), Loop("healthy", healthy)],
                     backoff=0.01, max_backoff=0.01, max_crashes=3, window=60)
    sup.run()  # 기준(3회/60초)을 넘으면 스스로 돌아온다

    assert len(crashes) == 3
    assert sup.exit_code == 1  # docker restart에 맡긴다
    assert healthy_stopped.is_set()  # 다른 루프도 깔끔하게 멈췄다


@pytest.mark.django_db(transaction=True)
def test_sigterm_stops_all_loops_and_command_returns():
    import apps.events.pipeline as pipeline

    def send_sigterm_when_ready():
        # 명령이 SIGTERM 처리기를 걸고 루프가 모두 뜬 뒤에만 보낸다(기본 처리기면 pytest가 죽는다).
        assert _wait_until(lambda: signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None)
                           and len([t for t in threading.enumerate() if t.name.startswith("events-")]) == 5)
        os.kill(os.getpid(), signal.SIGTERM)

    before = signal.getsignal(signal.SIGTERM)
    killer = threading.Thread(target=send_sigterm_when_ready, daemon=True)
    killer.start()
    started = time.monotonic()

    call_command("run_event_pipeline")

    assert time.monotonic() - started < 5  # relay의 idle 대기(5s)를 기다리지 않고 바로 깬다
    assert not [t for t in threading.enumerate() if t.name.startswith("events-")]
    assert signal.getsignal(signal.SIGTERM) is before  # 처리기를 되돌린다
    assert pipeline.LOOP_NAMES == ("relay", "webhook", "visitor-bridge", "console-bridge", "presence-bridge")
