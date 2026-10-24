import pytest


@pytest.mark.django_db
def test_upsert_and_get_visitor_memory(tenant_with_key):
    from apps.memory.manager import upsert_memory, get_visitor_memories

    tenant, _ = tenant_with_key
    upsert_memory(str(tenant.id), "v-001", "preference", "email")
    memories = get_visitor_memories(str(tenant.id), "v-001")
    assert any("preference" in m for m in memories)


@pytest.mark.django_db
def test_delete_visitor_memory(tenant_with_key):
    from apps.memory.manager import upsert_memory, delete_memory, get_visitor_memories

    tenant, _ = tenant_with_key
    mem = upsert_memory(str(tenant.id), "v-002", "name", "Alice")
    delete_memory(str(tenant.id), "v-002", str(mem.id))
    memories = get_visitor_memories(str(tenant.id), "v-002")
    assert not any("name" in m for m in memories)


@pytest.mark.django_db
def test_memory_tenant_isolation(tenant_with_key):
    import secrets
    from apps.tenants.models import Tenant
    from apps.memory.manager import upsert_memory, get_visitor_memories

    tenant1, _ = tenant_with_key
    raw_key2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Corp 2", raw_key=raw_key2)

    upsert_memory(str(tenant1.id), "v-same", "secret", "tenant1 data")
    memories = get_visitor_memories(str(tenant2.id), "v-same")
    assert len(memories) == 0


@pytest.mark.django_db
def test_list_memories_api(client, tenant_admin_token, tenant_with_key):
    from apps.memory.manager import upsert_memory

    tenant, _ = tenant_with_key
    upsert_memory(str(tenant.id), "v-api", "key1", "value1")

    response = client.get(
        "/api/tenant/visitors/v-api/memory/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.django_db
def test_delete_memory_api(client, tenant_admin_token, tenant_with_key):
    from apps.memory.manager import upsert_memory

    tenant, _ = tenant_with_key
    mem = upsert_memory(str(tenant.id), "v-del", "k", "v")

    response = client.delete(
        f"/api/tenant/visitors/v-del/memory/{mem.id}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 204


@pytest.mark.django_db
def test_tenant_config_update(client, tenant_admin_token):
    response = client.patch(
        "/api/tenant/config/",
        {"model_id": "openai/gpt-4o", "system_prompt": "You are a sales assistant."},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["model_id"] == "openai/gpt-4o"
    assert data["system_prompt"] == "You are a sales assistant."


@pytest.mark.django_db
def test_update_memory_api(client, tenant_admin_token, tenant_with_key):
    """PATCH /api/tenant/visitors/{visitor_id}/memory/{memory_id} → key/value 업데이트."""
    from apps.memory.manager import upsert_memory
    from apps.memory.models import VisitorMemory

    tenant, _ = tenant_with_key
    mem = upsert_memory(str(tenant.id), "v-update", "color", "blue")

    response = client.patch(
        f"/api/tenant/visitors/v-update/memory/{mem.id}",
        {"key": "color", "value": "red"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["key"] == "color"
    assert data["value"] == "red"

    mem.refresh_from_db()
    assert mem.value == "red"


@pytest.mark.django_db
def test_memory_api_visitor_isolation(client, tenant_admin_token, tenant_with_key):
    """다른 visitor_id의 메모리는 수정·삭제할 수 없다."""
    from apps.memory.manager import upsert_memory

    tenant, _ = tenant_with_key
    mem = upsert_memory(str(tenant.id), "v-alice", "name", "Alice")

    # bob의 URL로 alice의 메모리에 접근 시도
    response = client.patch(
        f"/api/tenant/visitors/v-bob/memory/{mem.id}",
        {"key": "name", "value": "Hacked"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_delete_memory_api_returns_404_for_unknown_id(client, tenant_admin_token):
    """존재하지 않는 memory_id로 DELETE 요청 시 404를 반환한다."""
    import uuid
    fake_id = str(uuid.uuid4())
    response = client.delete(
        f"/api/tenant/visitors/v-nobody/memory/{fake_id}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_memory_extraction_upserts_facts_from_llm(tenant_with_key, fake_text_llm):
    """LLM이 추출한 facts가 Visitor Memory에 결정적으로 upsert된다."""
    from apps.chat.models import ChatSession, ChatMessage
    from apps.memory.tasks import schedule_memory_extraction
    from apps.memory.models import VisitorMemory

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id="v-mem-task",    )
    ChatMessage.objects.create(session=session, role="user", content="제 이름은 홍길동입니다")
    ChatMessage.objects.create(session=session, role="assistant", content="안녕하세요 홍길동님!")

    fake_text_llm.facts = {"name": "홍길동"}
    schedule_memory_extraction(str(tenant.id), "v-mem-task", str(session.id))

    mem = VisitorMemory.objects.get(tenant_id=tenant.id, visitor_id="v-mem-task", key="name")
    assert mem.value == "홍길동"
