import pytest
from asgiref.sync import sync_to_async
from utils import get_redis_message

adb = sync_to_async  # async 테스트에서 sync ORM/픽스처를 단일 스레드로 await


def _make_session(tenant):
    from apps.chat.models import ChatSession
    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-hitl")


# ── Issue 55: LLM 경계 Fake가 판정을 통제한다 (tracer bullet) ──────────────────

@pytest.mark.django_db(transaction=True)
async def test_fake_llm_verdict_drives_escalation(tenant_with_key, fake_chat_llm):
    """Fake가 needs_hitl=True를 강제하면 메시지 내용과 무관하게 Escalation이 생성된다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)
    fake_chat_llm.override = lambda messages: HITLResponse(response="", needs_hitl=True, hitl_reason="forced")

    await run_chat_turn_async(session, "안녕하세요")

    await adb(session.refresh_from_db)()
    assert session.is_hitl is True
    assert await adb(Escalation.objects.filter(session=session, trigger_type="ai").exists)()


@pytest.mark.django_db(transaction=True)
async def test_hitl_with_response_does_not_stream_model_text_then_escalates(tenant_with_key, fake_chat_llm, redis_subscribe):
    """needs_hitl=True면 모델 답(전환 멘트 포함)은 스트리밍·저장되지 않고 escalation만 일어난다(#25: 「연결 불가」 모순 차단)."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatMessage
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)
    pubsub = redis_subscribe(f"session:{session.id}")
    fake_chat_llm.override = lambda m: HITLResponse(
        response="상담원에게 연결해 드리겠습니다.", needs_hitl=True, hitl_reason="불확실"
    )

    await run_chat_turn_async(session, "FCB1010 전원 사양 알려줘")

    seen = []
    while (msg := get_redis_message(pubsub)) is not None:
        seen.append(msg)
    assert not [m for m in seen if m["type"] == "token" and "연결해 드리겠습니다" in m["content"]]
    assert not await adb(ChatMessage.objects.filter(
        session=session, role=ChatMessage.ROLE_ASSISTANT, content="상담원에게 연결해 드리겠습니다.",
    ).exists)()
    await adb(session.refresh_from_db)()
    assert session.is_hitl is True
    assert await adb(Escalation.objects.filter(session=session).exists)()


# ── Issue 88: HITL 토글 (불리언으로 그래프 분기) ──────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_hitl_disabled_never_escalates_and_answers(tenant_with_key, fake_chat_llm):
    """hitl_enabled=False면 fake가 needs_hitl을 강제해도 escalation 없이 AI가 답한다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatMessage
    from apps.escalation.models import Escalation
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key

    def _disable():
        config = TenantConfig.objects.get(tenant=tenant)
        config.hitl_enabled = False
        config.save()

    await adb(_disable)()
    session = await adb(_make_session)(tenant)
    fake_chat_llm.override = lambda m: HITLResponse(
        response="제가 도와드리겠습니다", needs_hitl=True, hitl_reason="forced"
    )

    await run_chat_turn_async(session, "상담원 연결해 주세요")

    await adb(session.refresh_from_db)()
    assert session.is_hitl is False
    assert not await adb(Escalation.objects.filter(session=session).exists)()
    assert await adb(ChatMessage.objects.filter(
        session=session, role=ChatMessage.ROLE_ASSISTANT, content="제가 도와드리겠습니다"
    ).exists)()


@pytest.mark.django_db
def test_hitl_enabled_toggle_settable_via_config_api(client, tenant_admin_token):
    """Tenant가 어드민 config API로 hitl_enabled를 끄고 다시 조회할 수 있다(기본 True)."""
    g0 = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert g0.json()["hitl_enabled"] is True

    r = client.patch(
        "/api/tenant/config/",
        {"hitl_enabled": False},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert r.status_code == 200

    g1 = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert g1.json()["hitl_enabled"] is False


# ── Issue 21: Escalation 모델 + LangGraph Structured Output ───────────────

@pytest.mark.django_db(transaction=True)
async def test_needs_hitl_true_creates_escalation(tenant_with_key):
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)

    await run_chat_turn_async(session, "상담원 연결해 주세요")

    await adb(session.refresh_from_db)()
    assert session.is_hitl is True
    assert await adb(Escalation.objects.filter(session=session, trigger_type="ai").exists)()


@pytest.mark.django_db(transaction=True)
async def test_needs_hitl_false_saves_assistant_message(tenant_with_key):
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatMessage
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)

    await run_chat_turn_async(session, "안녕하세요")

    await adb(session.refresh_from_db)()
    assert session.is_hitl is False
    assert not await adb(Escalation.objects.filter(session=session).exists)()
    assert await adb(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).exists)()


@pytest.mark.django_db(transaction=True)
async def test_hitl_start_sse_published_on_escalation(tenant_with_key, redis_subscribe, drain_events):
    """AI escalation → SessionEscalated 이벤트 → visitor-bridge 소비자가 hitl_start를 발행한다."""
    from apps.flows.chat_turn import run_chat_turn_async

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)
    pubsub = redis_subscribe(f"session:{session.id}")

    await run_chat_turn_async(session, "상담원 연결해 주세요")
    await adb(drain_events)()  # 전이 이벤트 → relay → 소비자 인프로세스 플러시

    data = get_redis_message(pubsub)
    assert data is not None, "hitl_start 이벤트가 Redis에 발행되지 않았습니다"
    assert data["type"] == "hitl_start"


# ── Issue 22: Chat API HITL 모드 차단 ────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_ai_resumes_after_hitl_resolved(tenant_with_key, tenant_admin_token):
    """Escalation이 resolved되면 is_hitl=False로 전환되어 AI 응답이 재개된다."""
    from django.test import AsyncClient
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatMessage
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = await adb(_make_session)(tenant)

    await run_chat_turn_async(session, "상담원 연결해 주세요")
    await adb(session.refresh_from_db)()
    assert session.is_hitl is True

    esc = await adb(Escalation.objects.get)(session=session)
    aclient = AsyncClient()
    resp = await aclient.post(
        f"/api/tenant/escalations/{esc.id}/resolve",
        headers={"Authorization": f"Bearer {tenant_admin_token}"},
    )
    assert resp.status_code == 200

    await adb(session.refresh_from_db)()
    assert session.is_hitl is False

    await run_chat_turn_async(session, "다시 질문드려요")
    assert await adb(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).count)() >= 1


@pytest.mark.django_db
def test_hitl_mode_session_does_not_invoke_agent(client, tenant_with_key):
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(
        tenant_id=tenant.id, visitor_id="v-hitl-block", is_hitl=True,
    )

    response = client.post(
        "/api/chat/message",
        {"session_id": str(session.id), "content": "아직 거기 있나요?"},
        content_type="application/json",
    )

    assert response.status_code == 202
    assert ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_USER).exists()
    assert not ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).exists()
