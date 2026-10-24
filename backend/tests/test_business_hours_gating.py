"""영업시간으로 그래프를 가르는 게이팅 (issue 136).

is_open의 결과에 따라 그래프 선택(escalation 가능/불가)과 시간 외 안내 주입을 검증한다.
"""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


@pytest.mark.django_db(transaction=True)
async def test_off_hours_uses_plain_graph_no_escalation(tenant_with_key, fake_chat_llm, monkeypatch):
    """시간 외(is_open=False)면 plain 그래프 → '상담원' 요청에도 escalation이 생기지 않는다."""
    from apps.tenants import business_hours
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import PlainResponse
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    monkeypatch.setattr(business_hours, "is_open", lambda config, now: False)

    captured = {}
    def fake(messages):
        captured["joined"] = " ".join(getattr(m, "content", "") for m in messages)
        return PlainResponse(response="현재는 AI가 답변드립니다.", context_sufficient=True)
    fake_chat_llm.override = fake

    tenant, _ = tenant_with_key  # hitl_enabled 기본 True
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-off")
    answer = await run_chat_turn_async(session, "상담원 연결해줘")

    assert not await adb(Escalation.objects.filter(session=session).exists)()
    assert answer
    assert "운영 안내" in captured["joined"]


@pytest.mark.django_db(transaction=True)
async def test_open_hours_allows_escalation(tenant_with_key, fake_chat_llm, monkeypatch):
    """상담시간 내(is_open=True)면 structured 그래프 → '상담원' 요청이 escalation을 만든다."""
    from apps.tenants import business_hours
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    monkeypatch.setattr(business_hours, "is_open", lambda config, now: True)

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-on")
    await run_chat_turn_async(session, "상담원 연결해줘")  # 기본 Fake: '상담원' → needs_hitl=True

    assert await adb(Escalation.objects.filter(session=session).exists)()


@pytest.mark.django_db(transaction=True)
async def test_off_hours_no_notice_when_hitl_disabled(tenant_with_key, fake_chat_llm, monkeypatch):
    """HITL 자체가 꺼진 테넌트는 시간 외 안내를 주입하지 않는다."""
    from apps.tenants import business_hours
    from apps.tenants.models import TenantConfig
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import PlainResponse
    from apps.chat.models import ChatSession

    monkeypatch.setattr(business_hours, "is_open", lambda config, now: False)
    tenant, _ = tenant_with_key

    def _disable():
        config = TenantConfig.objects.get(tenant=tenant)
        config.hitl_enabled = False
        config.save()
    await adb(_disable)()

    captured = {}
    def fake(messages):
        captured["joined"] = " ".join(getattr(m, "content", "") for m in messages)
        return PlainResponse(response="안녕하세요", context_sufficient=True)
    fake_chat_llm.override = fake

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-disabled")
    await run_chat_turn_async(session, "상담원 연결해줘")
    assert "운영 안내" not in captured["joined"]
