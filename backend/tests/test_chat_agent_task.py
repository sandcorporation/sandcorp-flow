# pyright: reportOperatorIssue=false
"""chat 1턴 실행의 사용자 관측 동작(실패→error, 락 해제)을 HTTP 경계로 검증한다.

배선·직렬화의 단위 검증은 test_chat_task(taskiq chat_task 직접)·test_chat_message_async가,
큐 라우팅 같은 Celery 내부는 async 전환(ADR-0024)으로 폐기됐다. 여기선 POST /message →
dispatch_chat(InMemoryBroker await_inplace) → run_chat_turn_async의 종단 동작만 본다.
"""
import pytest
from utils import get_redis_message, open_stream


def _open_session(client, tenant, visitor_id):
    """slug 기반 stream으로 세션을 만들고 session_id를 반환한다 (issue 85)."""
    return open_stream(client, tenant, visitor_id)["X-Session-Id"]


@pytest.mark.django_db
def test_agent_failure_publishes_error_and_saves_no_assistant(
    client, tenant_with_key, fake_chat_llm, redis_subscribe
):
    """에이전트 실행이 실패하면 사용자에게 error 이벤트가 발행되고, 부분 저장은 없다.

    at-most-once: 실패는 조용한 무응답 대신 error로 알려 재전송을 유도하며,
    절반 실행된 assistant 메시지를 남기지 않는다.
    """
    from apps.chat.models import ChatSession, ChatMessage

    tenant, raw_key = tenant_with_key
    session_id = _open_session(client, tenant, "v-task-error")
    pubsub = redis_subscribe(f"session:{session_id}")

    def _boom(messages):
        raise RuntimeError("LLM exploded")

    fake_chat_llm.override = _boom

    client.post(
        "/api/chat/message",
        {"session_id": session_id, "content": "안녕하세요"},
        content_type="application/json",
    )

    msg = get_redis_message(pubsub)
    assert msg is not None and msg["type"] == "error", f"실패 시 error 이벤트가 와야 한다: {msg}"

    session = ChatSession.objects.get(id=session_id)
    assert not ChatMessage.objects.filter(
        session=session, role=ChatMessage.ROLE_ASSISTANT
    ).exists(), "실패 시 assistant 메시지를 남기면 안 된다"


@pytest.mark.django_db
def test_lock_released_on_failure_so_session_not_deadlocked(
    client, tenant_with_key, fake_chat_llm
):
    """에이전트가 실패해도 세션 락은 finally로 해제되어 다음 메시지가 막히지 않는다."""
    from apps.chat import session_lock

    tenant, raw_key = tenant_with_key
    session_id = _open_session(client, tenant, "v-fail-unlock")

    fake_chat_llm.override = lambda m: (_ for _ in ()).throw(RuntimeError("boom"))

    client.post(
        "/api/chat/message",
        {"session_id": session_id, "content": "안녕하세요"},
        content_type="application/json",
    )

    # 실패 후 락이 풀려 있어야 한다 → 외부에서 즉시 획득 가능
    assert session_lock.acquire(session_id) is True, "실패 시 락이 해제되어야 한다(데드락 금지)"
    session_lock.release(session_id)
