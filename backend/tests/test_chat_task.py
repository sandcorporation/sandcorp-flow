"""taskiq chat_task (issue 194) — kiq가 InMemoryBroker로 1턴 실행하고 응답을 저장한다."""
import pytest
from asgiref.sync import sync_to_async


def _make_session(tenant):
    from apps.chat.models import ChatSession
    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-task")


@pytest.mark.django_db(transaction=True)
async def test_chat_task_runs_agent_and_saves(tenant_with_key, fake_chat_llm):
    """chat_task.kiq → run_chat_turn_async → assistant 메시지 저장."""
    from apps.chat.chat_task import chat_task
    from apps.chat.models import ChatMessage
    from apps.agent.nodes import HITLResponse

    tenant, _ = tenant_with_key
    session = await sync_to_async(_make_session)(tenant)
    fake_chat_llm.override = lambda m: HITLResponse(response="taskiq 답변입니다", needs_hitl=False)

    sent = await chat_task.kiq(str(session.id), "안녕")
    await sent.wait_result(timeout=10)

    contents = await sync_to_async(
        lambda: [m.content for m in ChatMessage.objects.filter(session=session, role="assistant")]
    )()
    assert any("taskiq 답변" in c for c in contents)


@pytest.mark.django_db(transaction=True)
async def test_chat_task_dropped_when_session_locked(tenant_with_key, fake_chat_llm):
    """세션 락이 이미 잡혀 있으면 chat_task는 실행을 건너뛴다(직렬화)."""
    from apps.chat.chat_task import chat_task
    from apps.chat.session_lock import aacquire, arelease
    from apps.chat.models import ChatMessage
    from apps.agent.nodes import HITLResponse

    tenant, _ = tenant_with_key
    session = await sync_to_async(_make_session)(tenant)
    fake_chat_llm.override = lambda m: HITLResponse(response="should-not-run", needs_hitl=False)

    assert await aacquire(str(session.id)) is True   # 외부가 먼저 락 보유
    try:
        sent = await chat_task.kiq(str(session.id), "안녕")
        await sent.wait_result(timeout=10)
        count = await sync_to_async(ChatMessage.objects.filter(session=session, role="assistant").count)()
        assert count == 0   # 락 때문에 에이전트 미실행
    finally:
        await arelease(str(session.id))
