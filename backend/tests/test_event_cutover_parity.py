"""escalation 라이프사이클 빅뱅 컷오버 parity (issue 151).

전이가 정확한 도메인 이벤트를 원자적으로 기록하고, 전 파이프라인(전이→outbox→relay→소비자)이
기존 부수효과(webhook·방문자 SSE·콘솔 델타)를 동일하게 재현함을 검증한다. LLM은 결정적 Fake.
"""
import pytest


@pytest.mark.django_db(transaction=True)
async def test_ai_escalation_records_one_session_escalated_event(tenant_with_key, fake_chat_llm):
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession
    from apps.events.models import EventStore, Outbox
    from apps.events.types import SESSION_ESCALATED
    adb = sync_to_async

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-esc")

    await run_chat_turn_async(session, "상담원 연결해 주세요")

    evs = await adb(lambda: list(EventStore.objects.filter(aggregate_id=str(session.id), type=SESSION_ESCALATED)))()
    assert len(evs) == 1                                                  # 정확히 1건
    assert "escalation_id" in evs[0].payload
    assert await adb(Outbox.objects.filter(event_id=evs[0].event_id).count)() == 1   # outbox에도 1건


@pytest.mark.django_db(transaction=True)
async def test_full_pipeline_fires_webhook_once_on_ai_escalation(
    tenant_with_key, fake_chat_llm, webhook_server, drain_events
):
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.tenants.models import TenantConfig
    from apps.chat.models import ChatSession
    adb = sync_to_async

    tenant, _ = tenant_with_key

    def _setup():
        config = TenantConfig.objects.get(tenant=tenant)
        config.webhook_url, config.webhook_type = webhook_server["url"], "generic"
        config.save()
    await adb(_setup)()
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-wh")

    await run_chat_turn_async(session, "상담원 연결해 주세요")
    await adb(drain_events)()  # 전이 이벤트 → relay → webhook 소비자

    assert len(webhook_server["received"]) == 1  # webhook 1회(at-least-once+멱등)


@pytest.mark.django_db
def test_takeover_records_event_and_console_delta(
    client, tenant_with_key, tenant_admin_token, redis_subscribe, drain_events
):
    from apps.chat.models import ChatSession
    from apps.events.models import EventStore
    from apps.events.types import SESSION_TAKEN_OVER

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-take2")
    pubsub = redis_subscribe(f"hitl:{tenant.id}")

    client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")

    assert EventStore.objects.filter(aggregate_id=str(session.id), type=SESSION_TAKEN_OVER).count() == 1
    drain_events()
    seen = False
    for _ in range(20):
        m = pubsub.get_message(timeout=0.5)
        if m and m["type"] == "message" and b"hitl_new" in m["data"]:
            seen = True
            break
    assert seen  # console-bridge가 콘솔 델타 발행


@pytest.mark.django_db
def test_resolve_records_resolved_event(client, tenant_with_key, tenant_admin_token):
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation
    from apps.events.models import EventStore
    from apps.events.types import ESCALATION_RESOLVED

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-res", is_hitl=True)
    esc = Escalation.objects.create(session=session, trigger_type="ai", status="claimed", reason="r")

    client.post(f"/api/tenant/escalations/{esc.id}/resolve", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")

    assert EventStore.objects.filter(aggregate_id=str(session.id), type=ESCALATION_RESOLVED).count() == 1
