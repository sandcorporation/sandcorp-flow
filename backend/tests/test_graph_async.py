"""run_chat_turn_async (issue 192) — AsyncPostgresSaver + graph.ainvoke로 1턴 async 실행.

노드는 아직 sync(LangGraph가 async 그래프에서 실행). 진짜 async 토큰 경로는 후속 슬라이스.
"""
import pytest
from asgiref.sync import sync_to_async


def _make_session(tenant):
    from apps.chat.models import ChatSession
    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-async")


@pytest.mark.django_db(transaction=True)
async def test_run_chat_turn_async_answers(tenant_with_key, fake_chat_llm):
    """run_chat_turn_async가 async로 1턴 실행하고 응답 문자열을 반환한다(HITL-OFF)."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse

    tenant, _ = tenant_with_key
    session = await sync_to_async(_make_session)(tenant)
    fake_chat_llm.override = lambda m: HITLResponse(response="async 답변입니다", needs_hitl=False)

    result = await run_chat_turn_async(session, "안녕")
    assert "async 답변" in result
