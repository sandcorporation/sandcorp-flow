# pyright: reportOptionalSubscript=false
import pytest
from asgiref.sync import sync_to_async
from utils import open_stream, aopen_stream, aread_first_chunk

adb = sync_to_async


@pytest.mark.django_db
def test_langgraph_checkpoint_table_exists(db):
    """PostgresSaver.setup()이 checkpoint 테이블을 DB에 생성한다."""
    from apps.agent.graph import _create_checkpointer
    saver, conn = _create_checkpointer()
    conn.close()

    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='checkpoints'"
        )
        count = cursor.fetchone()[0]
    assert count == 1


@pytest.mark.django_db
def test_visitor_connects_via_slug_without_token(client, tenant_with_key):
    """slug + visitor_id로 토큰 없이 연결되어 ChatSession이 생성된다 (issue 85)."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    tenant.slug = "abc-shop"
    tenant.save(update_fields=["slug"])

    resp = client.get("/api/chat/stream?slug=abc-shop&visitor_id=v-77")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"

    session = ChatSession.objects.get(id=resp["X-Session-Id"])
    assert str(session.tenant_id) == str(tenant.id)
    assert session.visitor_id == "v-77"


@pytest.mark.django_db
def test_stream_unknown_slug_rejected(client):
    """존재하지 않는 slug로의 연결은 거부된다 (issue 85)."""
    resp = client.get("/api/chat/stream?slug=no-such-tenant&visitor_id=v-1")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_resolve_404_for_unknown_slug(client):
    """위젯 진입 가드 — 존재하지 않는 slug는 404 (유효하지 않은 챗봇 페이지를 띄우게)."""
    resp = client.get("/api/chat/resolve?slug=no-such-shop")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_resolve_200_for_valid_slug(client, tenant_with_key):
    """활성 Tenant의 slug는 200 + 공개 브랜드."""
    tenant, _ = tenant_with_key
    tenant.slug = "myshop"
    tenant.save(update_fields=["slug"])
    resp = client.get("/api/chat/resolve?slug=myshop")
    assert resp.status_code == 200
    assert "brand_name" in resp.json()


@pytest.mark.django_db
def test_stream_missing_visitor_id_rejected(client, tenant_with_key):
    """visitor_id 없이 연결하면 거부된다 (위젯이 Anonymous Visitor ID를 보장)."""
    tenant, _ = tenant_with_key
    tenant.slug = "novisitor"
    tenant.save(update_fields=["slug"])
    resp = client.get("/api/chat/stream?slug=novisitor&visitor_id=")
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
async def test_stream_sends_connected_event_first(tenant_with_key):
    import json

    tenant, _ = tenant_with_key
    stream_resp = await aopen_stream(tenant, "v-connected")

    # 첫 번째 청크가 'connected' 이벤트여야 한다
    first_chunk = await aread_first_chunk(stream_resp)
    assert "event: connected" in first_chunk
    payload = json.loads(first_chunk.split("data: ", 1)[1])
    assert "session_id" in payload


@pytest.mark.django_db
def test_chatsession_created_on_stream(client, tenant_with_key):
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    open_stream(client, tenant, "v-session-test")

    assert ChatSession.objects.filter(visitor_id="v-session-test").exists()


@pytest.mark.django_db
def test_send_message_via_api_saves_user_message(client, tenant_with_key):
    """POST /api/chat/message → 202 반환 + user 메시지 DB 저장."""
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key
    session_id = open_stream(client, tenant, "v-post-msg")["X-Session-Id"]

    resp = client.post(
        "/api/chat/message",
        {"session_id": session_id, "content": "안녕하세요"},
        content_type="application/json",
    )
    assert resp.status_code == 202

    session = ChatSession.objects.get(id=session_id)
    assert ChatMessage.objects.filter(
        session=session, role=ChatMessage.ROLE_USER, content="안녕하세요"
    ).exists()


@pytest.mark.django_db
def test_send_message_unknown_session_returns_404(client):
    import uuid
    resp = client.post(
        "/api/chat/message",
        {"session_id": str(uuid.uuid4()), "content": "hello"},
        content_type="application/json",
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_get_session_checkpoint_returns_channel_values(client, tenant_with_key, tenant_admin_token):
    """run_chat_agent 실행 후 GET /api/tenant/sessions/{id}/checkpoint → channel_values JSON."""
    from asgiref.sync import sync_to_async
    from apps.agent.graph import run_chat_agent_async
    from apps.chat.models import ChatSession
    adb = sync_to_async

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-checkpoint-api")
    await run_chat_agent_async(session, "안녕하세요")

    resp = await adb(client.get)(
        f"/api/tenant/sessions/{session.id}/checkpoint",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert "rag_chunks" in data


@pytest.mark.django_db
def test_get_session_checkpoint_404_when_no_llm_call(client, tenant_with_key, tenant_admin_token):
    """LLM 호출이 없는 세션에 checkpoint 조회 시 404를 반환한다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-no-checkpoint")

    resp = client.get(
        f"/api/tenant/sessions/{session.id}/checkpoint",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_session_checkpoint_404_for_other_tenant(client, tenant_admin_token):
    """다른 Tenant의 session_id로 checkpoint 조회 시 404를 반환한다."""
    import secrets
    from apps.tenants.models import Tenant
    from apps.chat.models import ChatSession

    raw_key2 = secrets.token_urlsafe(32)
    other_tenant = Tenant.objects.create_with_key(name="OtherCo CP", raw_key=raw_key2)
    other_session = ChatSession.objects.create(tenant_id=other_tenant.id, visitor_id="v-other-cp")

    resp = client.get(
        f"/api/tenant/sessions/{other_session.id}/checkpoint",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_get_session_retrievals_returns_turns(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/sessions/{id}/retrievals → 턴별 GraphRAG 검색 결과(히스토리에서 복원, issue 207)."""
    from asgiref.sync import sync_to_async
    from apps.agent.graph import run_chat_agent_async
    from apps.chat.models import ChatSession
    adb = sync_to_async

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-retr-api")
    await run_chat_agent_async(session, "안녕하세요")

    resp = await adb(client.get)(
        f"/api/tenant/sessions/{session.id}/retrievals",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "turns" in data and isinstance(data["turns"], list)
    assert any(t.get("user_message") == "안녕하세요" for t in data["turns"])
    # 턴마다 실행 노드 흐름이 함께 온다
    assert all(isinstance(t.get("nodes"), list) for t in data["turns"])
    assert any("local_search" in t.get("nodes", []) for t in data["turns"])


@pytest.mark.django_db
def test_get_session_retrievals_404_for_other_tenant(client, tenant_admin_token):
    """다른 Tenant의 session_id로 retrievals 조회 시 404를 반환한다."""
    import secrets
    from apps.tenants.models import Tenant
    from apps.chat.models import ChatSession

    raw_key2 = secrets.token_urlsafe(32)
    other_tenant = Tenant.objects.create_with_key(name="OtherCo RT", raw_key=raw_key2)
    other_session = ChatSession.objects.create(tenant_id=other_tenant.id, visitor_id="v-other-rt")

    resp = client.get(
        f"/api/tenant/sessions/{other_session.id}/retrievals",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_checkpoint_has_messages_but_not_lc_messages(tenant_with_key):
    """Checkpoint channel_values에 대화는 messages로만 남고 lc_messages(프롬프트 조립물)는 없다."""
    from asgiref.sync import sync_to_async
    from apps.agent.graph import run_chat_agent_async, _create_checkpointer
    from apps.chat.models import ChatSession
    adb = sync_to_async

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-lc-messages")
    await run_chat_agent_async(session, "안녕하세요")
    await run_chat_agent_async(session, "또 질문이요")

    def _read():
        saver, conn = _create_checkpointer()
        try:
            return saver.get({"configurable": {"thread_id": str(session.id)}})
        finally:
            conn.close()
    cp = await adb(_read)()

    cv = cp["channel_values"]
    assert "messages" in cv
    assert "lc_messages" not in cv, f"lc_messages가 checkpoint에 남아있음: {sorted(cv.keys())}"

    # 시간순 + 중복 없음
    roles = [m["role"] for m in cv["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"], roles


@pytest.mark.django_db(transaction=True)
async def test_multi_turn_creates_multiple_assistant_replies(tenant_with_key):
    """agent를 두 번 실행하면 각각 assistant 메시지가 저장되고 히스토리가 누적된다."""
    from asgiref.sync import sync_to_async
    from apps.agent.graph import run_chat_agent_async
    from apps.chat.models import ChatSession, ChatMessage
    adb = sync_to_async

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-multiturn")

    await run_chat_agent_async(session, "안녕하세요")
    assert await adb(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).count)() == 1

    await run_chat_agent_async(session, "감사합니다")
    assert await adb(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).count)() == 2


@pytest.mark.django_db
def test_hitl_session_blocks_message_post(client, tenant_with_key):
    """is_hitl=True인 세션에 메시지를 보내면 202를 반환하되 agent를 실행하지 않는다."""
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(
        tenant_id=tenant.id, visitor_id="v-hitl-block-post", is_hitl=True
    )

    resp = client.post(
        "/api/chat/message",
        {"session_id": str(session.id), "content": "아직 거기 있나요?"},
        content_type="application/json",
    )
    assert resp.status_code == 202
    assert ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_USER).exists()
    assert not ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).exists()


@pytest.mark.django_db
def test_stream_reconnect_reuses_same_session(client, tenant_with_key):
    """동일 slug+visitor_id로 두 번 stream 요청해도 같은 ChatSession을 재사용한다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session_id_1 = open_stream(client, tenant, "v-reconnect")["X-Session-Id"]
    session_id_2 = open_stream(client, tenant, "v-reconnect")["X-Session-Id"]

    assert session_id_1 == session_id_2
    assert ChatSession.objects.filter(visitor_id="v-reconnect").count() == 1


# ── Issue 42: session restoration in connected event ─────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_stream_reconnect_includes_history_in_connected_event(tenant_with_key):
    """기존 메시지가 있는 세션에 재연결하면 connected 이벤트 payload에 history가 포함된다."""
    import json
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key

    def _seed():
        session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-history-restore")
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="안녕하세요")
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content="무엇을 도와드릴까요?")
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-history-restore")
    first_chunk = await aread_first_chunk(stream_resp)

    assert "event: connected" in first_chunk
    payload = json.loads(first_chunk.split("data: ", 1)[1])
    assert "history" in payload
    assert len(payload["history"]) == 2
    assert payload["history"][0] == {"role": "user", "content": "안녕하세요"}
    assert payload["history"][1] == {"role": "assistant", "content": "무엇을 도와드릴까요?"}


@pytest.mark.django_db(transaction=True)
async def test_stream_reconnect_is_hitl_true_included_in_connected_event(tenant_with_key):
    """is_hitl=True인 기존 세션에 재연결하면 connected 이벤트에 is_hitl: true가 포함된다."""
    import json
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key

    def _seed():
        session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-hitl-restore", is_hitl=True)
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="상담원 연결해 주세요")
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-hitl-restore")
    first_chunk = await aread_first_chunk(stream_resp)
    payload = json.loads(first_chunk.split("data: ", 1)[1])

    assert payload.get("is_hitl") is True


@pytest.mark.django_db(transaction=True)
async def test_stream_reconnect_no_welcome_message_for_existing_session(tenant_with_key):
    """기존 메시지가 있는 세션에 재연결하면 connected 이벤트에 welcome_message가 없다."""
    import json
    from apps.chat.models import ChatSession, ChatMessage
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key

    def _seed():
        config = TenantConfig.objects.get(tenant=tenant)
        config.welcome_message = "안녕하세요! 무엇을 도와드릴까요?"
        config.save()

        session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-no-welcome-repeat")
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="첫 메시지")
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-no-welcome-repeat")
    first_chunk = await aread_first_chunk(stream_resp)
    payload = json.loads(first_chunk.split("data: ", 1)[1])

    assert "welcome_message" not in payload


@pytest.mark.django_db(transaction=True)
async def test_stream_new_session_no_history_in_connected_event(tenant_with_key):
    """신규 세션(ChatMessage 없음)에서 connected 이벤트에 history가 없고 welcome_message가 있다."""
    import json
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key

    def _seed():
        config = TenantConfig.objects.get(tenant=tenant)
        config.welcome_message = "신규 방문자 환영합니다!"
        config.save()
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-new-no-history")
    first_chunk = await aread_first_chunk(stream_resp)
    payload = json.loads(first_chunk.split("data: ", 1)[1])

    assert "history" not in payload
    assert payload.get("welcome_message") == "신규 방문자 환영합니다!"


# ── Issue 89: 위젯 브랜드 텍스트 ──────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_brand_name_in_connected_event(tenant_with_key):
    """brand_name이 설정되면 connected 이벤트 payload에 포함된다(헤더 타이틀용)."""
    import json
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key

    def _seed():
        config = TenantConfig.objects.get(tenant=tenant)
        config.brand_name = "ABC쇼핑 고객센터"
        config.save()
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-brand")
    first_chunk = await aread_first_chunk(stream_resp)
    payload = json.loads(first_chunk.split("data: ", 1)[1])
    assert payload.get("brand_name") == "ABC쇼핑 고객센터"


@pytest.mark.django_db
def test_brand_name_settable_via_config_api(client, tenant_admin_token):
    """Tenant가 어드민 config API로 brand_name을 설정·조회할 수 있다."""
    client.patch(
        "/api/tenant/config/",
        {"brand_name": "내 브랜드"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert g.json()["brand_name"] == "내 브랜드"
