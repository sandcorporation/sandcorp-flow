import pytest


def _make_session(tenant, visitor_id):
    from apps.chat.models import ChatSession
    return ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id=visitor_id,    )


@pytest.mark.django_db
def test_list_visitors_returns_distinct_visitor_ids(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/visitors/ → 세션이 있는 distinct visitor_id 목록을 반환한다."""
    tenant, _ = tenant_with_key
    _make_session(tenant, "v-alice")
    _make_session(tenant, "v-alice")  # 중복 세션
    _make_session(tenant, "v-bob")

    resp = client.get(
        "/api/tenant/visitors/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    visitor_ids = [v["visitor_id"] for v in data]
    assert "v-alice" in visitor_ids
    assert "v-bob" in visitor_ids
    assert visitor_ids.count("v-alice") == 1  # 중복 없어야 함


@pytest.mark.django_db
def test_list_visitors_search_by_visitor_id(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/visitors/?search=alice → visitor_id에 alice 포함된 것만 반환한다."""
    tenant, _ = tenant_with_key
    _make_session(tenant, "v-alice")
    _make_session(tenant, "v-bob")

    resp = client.get(
        "/api/tenant/visitors/?search=alice",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    visitor_ids = [v["visitor_id"] for v in data]
    assert "v-alice" in visitor_ids
    assert "v-bob" not in visitor_ids


@pytest.mark.django_db
def test_list_visitor_sessions(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/visitors/{visitor_id}/sessions/ → visitor의 세션 목록을 반환한다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    s1 = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-carol")
    s2 = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-carol")

    resp = client.get(
        "/api/tenant/visitors/v-carol/sessions/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    session_ids = [s["session_id"] for s in data]
    assert str(s1.id) in session_ids
    assert str(s2.id) in session_ids

    # 와이어 포맷 회귀(issue 109): VisitorSessionOut 키가 그대로
    assert set(data[0].keys()) == {"session_id", "visitor_id", "is_hitl", "created_at", "flow_id", "flow_name"}


@pytest.mark.django_db
def test_get_session_messages(client, tenant_with_key, tenant_admin_token):
    """GET /api/tenant/sessions/{session_id}/messages/ → 세션의 메시지 목록을 반환한다."""
    from apps.chat.models import ChatSession, ChatMessage

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-dave")
    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="안녕")
    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content="안녕하세요!")

    resp = client.get(
        f"/api/tenant/sessions/{session.id}/messages/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"


@pytest.mark.django_db
def test_get_session_messages_404_for_other_tenant(client, tenant_admin_token):
    """다른 테넌트의 session_id로 메시지 조회 시 404를 반환한다."""
    import secrets, uuid
    from apps.tenants.models import Tenant
    from apps.chat.models import ChatSession

    raw_key2 = secrets.token_urlsafe(32)
    other_tenant = Tenant.objects.create_with_key(name="OtherCo2", raw_key=raw_key2)
    other_session = ChatSession.objects.create(
        tenant_id=other_tenant.id, visitor_id="v-other2"    )

    resp = client.get(
        f"/api/tenant/sessions/{other_session.id}/messages/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_visitor_detail_shows_sessions_and_memories(client, tenant_with_key, tenant_admin_token):
    """visitor detail: 세션 목록 + 메모리 목록을 별도 API로 각각 조회할 수 있다."""
    from apps.memory.manager import upsert_memory

    tenant, _ = tenant_with_key
    _make_session(tenant, "v-eve")
    upsert_memory(str(tenant.id), "v-eve", "language", "Korean")

    sessions_resp = client.get(
        "/api/tenant/visitors/v-eve/sessions/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert sessions_resp.status_code == 200
    assert len(sessions_resp.json()) == 1

    memories_resp = client.get(
        "/api/tenant/visitors/v-eve/memory/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert memories_resp.status_code == 200
    assert any(m["key"] == "language" for m in memories_resp.json())


@pytest.mark.django_db
def test_list_visitors_tenant_isolation(client, tenant_admin_token, tenant_with_key):
    """다른 테넌트의 visitor는 조회되지 않는다."""
    import secrets
    from apps.tenants.models import Tenant

    tenant, _ = tenant_with_key
    _make_session(tenant, "v-own")

    raw_key2 = secrets.token_urlsafe(32)
    other_tenant = Tenant.objects.create_with_key(name="OtherCo", raw_key=raw_key2)
    _make_session(other_tenant, "v-other")

    resp = client.get(
        "/api/tenant/visitors/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    data = resp.json()
    visitor_ids = [v["visitor_id"] for v in data]
    assert "v-own" in visitor_ids
    assert "v-other" not in visitor_ids
