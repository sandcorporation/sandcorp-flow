import pytest
from utils import get_redis_message


def _make_session(tenant, is_hitl=False):
    from apps.chat.models import ChatSession
    return ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id="v-esc",        is_hitl=is_hitl,
    )


def _make_escalation(session):
    from apps.escalation.models import Escalation
    return Escalation.objects.create(
        session=session,
        trigger_type=Escalation.TRIGGER_AI,
        reason="복잡한 문의",
        status=Escalation.STATUS_PENDING,
    )


def _agent_client(client, token):
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client


# ── Issue 23: Escalation 관리 API ────────────────────────────────────────


@pytest.mark.django_db
def test_list_escalations_returns_pending_and_claimed(client, tenant_with_key, tenant_admin_token):
    from apps.escalation.models import Escalation, EscalationClaim

    tenant, _ = tenant_with_key
    session1 = _make_session(tenant, is_hitl=True)
    session2 = _make_session(tenant, is_hitl=True)
    esc1 = _make_escalation(session1)
    esc2 = _make_escalation(session2)
    esc2.status = Escalation.STATUS_CLAIMED
    esc2.save()

    _agent_client(client, tenant_admin_token)
    resp = client.get("/api/tenant/escalations/")

    assert resp.status_code == 200
    data = resp.json()
    ids = [e["id"] for e in data]
    assert str(esc1.id) in ids

    # 와이어 포맷 회귀(issue 108): Schema 정비 후에도 EscalationOut 키가 그대로
    row = next(e for e in data if e["id"] == str(esc1.id))
    assert set(row.keys()) == {"id", "session_id", "trigger_type", "reason", "status", "created_at", "flow_id", "flow_name"}
    assert str(esc2.id) in ids


@pytest.mark.django_db
def test_list_escalations_excludes_resolved(client, tenant_with_key, tenant_admin_token):
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)
    esc.status = Escalation.STATUS_RESOLVED
    esc.save()

    _agent_client(client, tenant_admin_token)
    resp = client.get("/api/tenant/escalations/")

    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert str(esc.id) not in ids


@pytest.mark.django_db
def test_claim_escalation_creates_claim(client, tenant_with_key, tenant_admin_token):
    from apps.escalation.models import Escalation, EscalationClaim

    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    _agent_client(client, tenant_admin_token)
    resp = client.post(f"/api/tenant/escalations/{esc.id}/claim")

    assert resp.status_code == 200
    esc.refresh_from_db()
    assert esc.status == Escalation.STATUS_CLAIMED
    assert EscalationClaim.objects.filter(escalation=esc).exists()


@pytest.mark.django_db
def test_double_claim_returns_409(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    _agent_client(client, tenant_admin_token)
    client.post(f"/api/tenant/escalations/{esc.id}/claim")
    resp = client.post(f"/api/tenant/escalations/{esc.id}/claim")

    assert resp.status_code == 409


@pytest.mark.django_db
def test_send_message_saves_human_agent_role(client, tenant_with_key, tenant_admin_token):
    from apps.chat.models import ChatMessage

    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    _agent_client(client, tenant_admin_token)
    resp = client.post(
        f"/api/tenant/escalations/{esc.id}/message",
        {"content": "안녕하세요, 상담원입니다."},
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert ChatMessage.objects.filter(
        session=session, role=ChatMessage.ROLE_HUMAN_AGENT
    ).exists()


@pytest.mark.django_db
def test_send_message_publishes_hitl_message_sse(client, tenant_with_key, tenant_admin_token, redis_subscribe):
    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    # Subscribe before the API call to catch the event
    pubsub = redis_subscribe(f"session:{session.id}")

    _agent_client(client, tenant_admin_token)
    client.post(
        f"/api/tenant/escalations/{esc.id}/message",
        {"content": "도움이 필요하신가요?"},
        content_type="application/json",
    )

    data = get_redis_message(pubsub)
    assert data is not None, "hitl_message 이벤트가 Redis에 발행되지 않았습니다"
    assert data["type"] == "hitl_message"
    assert data["content"] == "도움이 필요하신가요?"


@pytest.mark.django_db
def test_escalation_sse_stream_returns_event_stream(client, tenant_with_key):
    """GET /api/tenant/escalations/stream?token=<agent_jwt> → SSE 커넥션 확립."""
    from tests.utils import make_membership, member_token

    tenant, _ = tenant_with_key
    agent = make_membership(tenant, "member")
    token = member_token(agent)

    resp = client.get(f"/api/tenant/escalations/stream?token={token}")
    assert resp.status_code == 200
    assert "text/event-stream" in resp["Content-Type"]


@pytest.mark.django_db
def test_escalation_sse_stream_rejects_invalid_token(client):
    """유효하지 않은 토큰으로 escalation stream 요청 시 401을 반환한다."""
    resp = client.get("/api/tenant/escalations/stream?token=invalid-token")
    assert resp.status_code == 401


@pytest.mark.django_db(transaction=True)
async def test_escalation_stream_delivers_visitor_message_live(tenant_with_key):
    """hitl 채널에 발행된 visitor_message가 연결된 콘솔 스트림으로 라이브 전달된다(issue 212).

    sync 블로킹 스트림이 uvicorn ASGI에서 라이브 전달을 못 하던 버그의 regression — async 전환 후 통과.
    """
    import asyncio
    import json
    from django.test import AsyncClient
    from asgiref.sync import sync_to_async
    from tests.utils import make_membership, member_token
    from apps.chat.sse import publish_visitor_message

    tenant, _ = tenant_with_key

    def _agent():
        a = make_membership(tenant, "admin")
        return a
    agent = await sync_to_async(_agent)()
    token = member_token(agent)

    resp = await AsyncClient().get(f"/api/tenant/escalations/stream?token={token}")
    assert resp.status_code == 200

    async def read_until_visitor():
        async for chunk in resp.streaming_content:
            text = chunk.decode()
            if "visitor_message" in text:  # keepalive(`: keepalive`)는 건너뛴다
                data_line = next(l for l in text.splitlines() if l.startswith("data:"))
                return json.loads(data_line[len("data:"):].strip())
        return None

    task = asyncio.ensure_future(read_until_visitor())
    # pub/sub은 구독 전 발행이 유실되므로, 구독 확립을 보장하려 짧게 반복 발행한다.
    for _ in range(6):
        await asyncio.sleep(0.3)
        await sync_to_async(publish_visitor_message)(str(tenant.id), "sess-live", "상담원님 안녕하세요")
        if task.done():
            break
    data = await asyncio.wait_for(task, timeout=5)

    assert data["type"] == "visitor_message"
    assert data["session_id"] == "sess-live"
    assert data["content"] == "상담원님 안녕하세요"


@pytest.mark.django_db
def test_typing_indicator_publishes_sse_event(client, tenant_with_key, tenant_admin_token, redis_subscribe):
    """POST /api/tenant/escalations/{id}/typing → Redis에 typing 이벤트가 발행된다."""
    from utils import get_redis_message
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id="v-typing",        is_hitl=True,
    )
    esc = Escalation.objects.create(
        session=session,
        trigger_type=Escalation.TRIGGER_AI,
        reason="test",
        status=Escalation.STATUS_CLAIMED,
    )

    pubsub = redis_subscribe(f"session:{session.id}")

    resp = client.post(
        f"/api/tenant/escalations/{esc.id}/typing",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200

    data = get_redis_message(pubsub)
    assert data is not None
    assert data["type"] == "typing"
    assert data["actor"] == "human_agent"


@pytest.mark.django_db
def test_typing_indicator_404_for_unknown_escalation(client, tenant_admin_token):
    """존재하지 않는 escalation_id로 /typing 요청 시 404를 반환한다."""
    import uuid
    fake_id = str(uuid.uuid4())
    resp = client.post(
        f"/api/tenant/escalations/{fake_id}/typing",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_escalation_messages_returns_conversation(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/escalations/{id}/messages → 세션의 대화 내역을 반환한다."""
    from apps.chat.models import ChatMessage

    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="도움이 필요해요")
    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content="무엇을 도와드릴까요?")

    _agent_client(client, tenant_admin_token)
    resp = client.get(f"/api/tenant/escalations/{esc.id}/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "도움이 필요해요"
    assert data[1]["role"] == "assistant"

    # 와이어 포맷 회귀(issue 108): EscalationMessageOut 키가 그대로
    assert set(data[0].keys()) == {"id", "role", "content", "created_at"}


@pytest.mark.django_db
def test_get_escalation_messages_404_for_unknown(client, tenant_admin_token):
    """존재하지 않는 escalation_id로 /messages 요청 시 404를 반환한다."""
    import uuid
    _agent_client(client, tenant_admin_token)
    resp = client.get(f"/api/tenant/escalations/{uuid.uuid4()}/messages")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_visitor_message_in_hitl_session_publishes_to_hitl_channel(client, tenant_with_key, redis_subscribe):
    """HITL 세션에 visitor가 메시지를 보내면 hitl:{tenant_id} 채널에 visitor_message 이벤트가 발행된다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id="v-hitl-visitor-msg",        is_hitl=True,
    )

    pubsub = redis_subscribe(f"hitl:{tenant.id}")

    resp = client.post(
        "/api/chat/message",
        {"session_id": str(session.id), "content": "아직 거기 있나요?"},
        content_type="application/json",
    )
    assert resp.status_code == 202

    data = get_redis_message(pubsub)
    assert data is not None, "visitor_message 이벤트가 Redis에 발행되지 않았습니다"
    assert data["type"] == "visitor_message"
    assert data["content"] == "아직 거기 있나요?"
    assert data["session_id"] == str(session.id)


@pytest.mark.django_db
def test_resolve_escalation(client, tenant_with_key, tenant_admin_token, redis_subscribe, drain_events):
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = _make_session(tenant, is_hitl=True)
    esc = _make_escalation(session)

    pubsub = redis_subscribe(f"session:{session.id}")

    _agent_client(client, tenant_admin_token)
    resp = client.post(f"/api/tenant/escalations/{esc.id}/resolve")

    assert resp.status_code == 200
    esc.refresh_from_db()
    session.refresh_from_db()
    assert esc.status == Escalation.STATUS_RESOLVED
    assert esc.resolved_at is not None
    assert session.is_hitl is False

    # 컷오버(151): hitl_end는 EscalationResolved 이벤트의 visitor-bridge 소비자가 발행한다.
    drain_events()
    data = get_redis_message(pubsub)
    assert data is not None, "hitl_end 이벤트가 Redis에 발행되지 않았습니다"
    assert data["type"] == "hitl_end"
