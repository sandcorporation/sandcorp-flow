import pytest
from asgiref.sync import sync_to_async
from utils import aopen_stream, aread_first_chunk

adb = sync_to_async


@pytest.mark.django_db
def test_create_tenant_returns_key(client, operator_token):
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.post(
        "/api/operator/tenants/",
        {"name": "Acme Corp"},
        content_type="application/json",
    )
    assert response.status_code == 201
    data = response.json()
    assert "tenant_key" in data
    assert data["name"] == "Acme Corp"


@pytest.mark.django_db
def test_create_tenant_requires_operator_auth(client):
    response = client.post(
        "/api/operator/tenants/",
        {"name": "Acme Corp"},
        content_type="application/json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_tenants(client, operator_token):
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    client.post(
        "/api/operator/tenants/",
        {"name": "Corp A"},
        content_type="application/json",
    )
    client.post(
        "/api/operator/tenants/",
        {"name": "Corp B"},
        content_type="application/json",
    )
    response = client.get("/api/operator/tenants/")
    assert response.status_code == 200
    assert len(response.json()) >= 2


@pytest.mark.django_db
def test_suspend_tenant(client, operator_token, tenant_with_key):
    tenant, _ = tenant_with_key
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.patch(f"/api/operator/tenants/{tenant.id}/suspend")
    assert response.status_code == 200

    tenant.refresh_from_db()
    assert tenant.is_active is False


@pytest.mark.django_db
def test_delete_tenant(client, operator_token, tenant_with_key):
    from apps.tenants.models import Tenant

    tenant, _ = tenant_with_key
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.delete(f"/api/operator/tenants/{tenant.id}")
    assert response.status_code == 204
    assert not Tenant.objects.filter(id=tenant.id).exists()


@pytest.mark.django_db
def test_tenant_key_not_stored_in_plaintext(client, operator_token):
    from apps.tenants.models import Tenant

    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.post(
        "/api/operator/tenants/",
        {"name": "Secret Corp"},
        content_type="application/json",
    )
    raw_key = response.json()["tenant_key"]
    tenant = Tenant.objects.get(name="Secret Corp")
    assert tenant.tenant_key_hash != raw_key


# ── Issue 17: Operator Tenant 생성 시 초기 TenantAgent 자동 생성 ──────────


@pytest.mark.django_db
def test_create_tenant_returns_initial_agent_credentials(client, operator_token):
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.post(
        "/api/operator/tenants/",
        {"name": "New Corp"},
        content_type="application/json",
    )
    assert response.status_code == 201
    data = response.json()
    assert "agent_username" in data
    assert "agent_temp_password" in data
    assert data["agent_username"] == "admin"


@pytest.mark.django_db
def test_initial_agent_can_login_after_tenant_creation(client, operator_token):
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    resp = client.post(
        "/api/operator/tenants/",
        {"name": "LoginCorp"},
        content_type="application/json",
    )
    data = resp.json()
    tenant_name = data["name"]
    password = data["agent_temp_password"]

    login_resp = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant_name, "username": "admin", "password": password},
        content_type="application/json",
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


# ── Issue 18: TENANT_KEY가 /api/tenant/config/에서 거부됨 ─────────────────


@pytest.mark.django_db
def test_tenant_key_rejected_on_config_endpoint(client, tenant_with_key):
    _, raw_key = tenant_with_key
    response = client.get(
        "/api/tenant/config/",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert response.status_code == 401


# ── Issue 19: TenantAgent CRUD API ────────────────────────────────────────


@pytest.mark.django_db
def test_list_agents(client, tenant_agent_token):
    response = client.get(
        "/api/tenant/agents/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(a["username"] == "agent" for a in data)


@pytest.mark.django_db
def test_create_agent_returns_temp_password(client, tenant_agent_token):
    response = client.post(
        "/api/tenant/agents/",
        {"username": "newbie"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newbie"
    assert "temp_password" in data


@pytest.mark.django_db
def test_created_agent_can_login(client, tenant_agent_token, tenant_with_key):
    tenant, _ = tenant_with_key
    resp = client.post(
        "/api/tenant/agents/",
        {"username": "newbie2"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    temp_password = resp.json()["temp_password"]

    login_resp = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "newbie2", "password": temp_password},
        content_type="application/json",
    )
    assert login_resp.status_code == 200


@pytest.mark.django_db
def test_deactivate_agent_blocks_login(client, tenant_agent_token, tenant_with_key):
    from apps.tenants.models import TenantAgent

    tenant, _ = tenant_with_key
    resp = client.post(
        "/api/tenant/agents/",
        {"username": "todeactivate"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    agent_id = resp.json()["id"]
    temp_password = resp.json()["temp_password"]

    client.patch(
        f"/api/tenant/agents/{agent_id}/deactivate",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )

    login_resp = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "todeactivate", "password": temp_password},
        content_type="application/json",
    )
    assert login_resp.status_code == 401


@pytest.mark.django_db
def test_tenant_key_can_create_agent(client, tenant_with_key):
    tenant, raw_key = tenant_with_key
    response = client.post(
        "/api/tenant/agents/",
        {"username": "serverside"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert response.status_code == 201


# ── Issue 20: 비밀번호 변경 ────────────────────────────────────────────────


@pytest.mark.django_db
def test_change_password(client, tenant_agent_token, tenant_with_key):
    tenant, _ = tenant_with_key
    client.post(
        "/api/tenant/agents/me/change-password",
        {"current_password": "agentpass", "new_password": "newpass123"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )

    login_resp = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "agent", "password": "newpass123"},
        content_type="application/json",
    )
    assert login_resp.status_code == 200


@pytest.mark.django_db
def test_change_password_wrong_current(client, tenant_agent_token):
    response = client.post(
        "/api/tenant/agents/me/change-password",
        {"current_password": "wrongpass", "new_password": "newpass123"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 400


# ── Issue 16: TenantAgent 모델 + 로그인 API + TenantAgentAuth ──────────────


@pytest.mark.django_db
def test_tenant_agent_login_returns_token(client, tenant_with_key):
    from apps.tenants.models import TenantAgent

    tenant, _ = tenant_with_key
    agent = TenantAgent(tenant=tenant, username="alice")
    agent.set_password("secret123")
    agent.save()

    response = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "alice", "password": "secret123"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.django_db
def test_tenant_agent_login_wrong_password(client, tenant_with_key):
    from apps.tenants.models import TenantAgent

    tenant, _ = tenant_with_key
    agent = TenantAgent(tenant=tenant, username="bob")
    agent.set_password("correct")
    agent.save()

    response = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "bob", "password": "wrong"},
        content_type="application/json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_tenant_agent_login_inactive_account(client, tenant_with_key):
    from apps.tenants.models import TenantAgent

    tenant, _ = tenant_with_key
    agent = TenantAgent(tenant=tenant, username="carol", is_active=False)
    agent.set_password("secret123")
    agent.save()

    response = client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": "carol", "password": "secret123"},
        content_type="application/json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_tenant_agent_jwt_protects_config_endpoint(client, tenant_with_key):
    from apps.tenants.models import TenantAgent
    from apps.tenants.auth import create_tenant_agent_token

    tenant, _ = tenant_with_key
    agent = TenantAgent(tenant=tenant, username="dave")
    agent.set_password("pass")
    agent.save()
    token = create_tenant_agent_token(agent)

    response = client.get(
        "/api/tenant/config/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_get_config_returns_all_fields(client, tenant_agent_token):
    """GET /api/tenant/config/ → 모든 설정 필드가 반환된다."""
    response = client.get(
        "/api/tenant/config/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    data = response.json()
    for field in ("model_id", "system_prompt", "agent_display_name", "webhook_url", "webhook_type", "welcome_message"):
        assert field in data


@pytest.mark.django_db
def test_welcome_message_saved_and_retrieved_via_api(client, tenant_agent_token):
    """PATCH welcome_message 후 GET으로 동일 값이 반환된다."""
    client.patch(
        "/api/tenant/config/",
        {"welcome_message": "안녕하세요! 무엇을 도와드릴까요?"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    response = client.get(
        "/api/tenant/config/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    assert response.json()["welcome_message"] == "안녕하세요! 무엇을 도와드릴까요?"


@pytest.mark.django_db
def test_update_webhook_config(client, tenant_agent_token):
    """PATCH /api/tenant/config/ → webhook_url, webhook_type 업데이트."""
    response = client.patch(
        "/api/tenant/config/",
        {"webhook_url": "https://hooks.slack.com/test", "webhook_type": "slack"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["webhook_url"] == "https://hooks.slack.com/test"
    assert data["webhook_type"] == "slack"


@pytest.mark.django_db
def test_config_partial_update_does_not_reset_other_fields(client, tenant_agent_token):
    """PATCH /api/tenant/config/ → 지정하지 않은 필드는 유지된다."""
    client.patch(
        "/api/tenant/config/",
        {"system_prompt": "Custom prompt"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    response = client.patch(
        "/api/tenant/config/",
        {"agent_display_name": "고객센터"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["system_prompt"] == "Custom prompt"
    assert data["agent_display_name"] == "고객센터"


@pytest.mark.django_db
def test_unauthenticated_config_access_rejected(client):
    """인증 없이 /api/tenant/config/ 요청 시 401을 반환한다."""
    response = client.get("/api/tenant/config/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_business_hours_config_roundtrips(client, tenant_agent_token):
    """PATCH로 영업시간(타임존·요일별 시간창·휴일)을 저장하면 GET이 동일 값을 돌려준다(issue 137)."""
    schedule = {"mon": {"enabled": True, "start": "09:00", "end": "18:00"}}
    resp = client.patch(
        "/api/tenant/config/",
        {"hitl_timezone": "Asia/Seoul", "hitl_schedule": schedule, "hitl_holidays": ["2026-01-01"]},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert resp.status_code == 200
    data = client.get(
        "/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}"
    ).json()
    assert data["hitl_timezone"] == "Asia/Seoul"
    assert data["hitl_schedule"]["mon"]["start"] == "09:00"
    assert data["hitl_holidays"] == ["2026-01-01"]


@pytest.mark.django_db
def test_reset_tenant_key_returns_new_key(client, tenant_agent_token, tenant_with_key):
    """POST /api/tenant/reset-key → 새 키 반환, 기존 키로 인증 불가."""
    tenant, old_raw_key = tenant_with_key

    response = client.post(
        "/api/tenant/reset-key",
        HTTP_AUTHORIZATION=f"Bearer {tenant_agent_token}",
    )
    assert response.status_code == 200
    data = response.json()
    assert "new_tenant_key" in data
    new_key = data["new_tenant_key"]
    assert new_key != old_raw_key

    from apps.tenants.models import Tenant
    assert Tenant.verify_key(old_raw_key) is None
    assert Tenant.verify_key(new_key) is not None


@pytest.mark.django_db(transaction=True)
async def test_welcome_message_included_in_connected_event(tenant_with_key):
    """welcome_message가 설정된 경우 SSE connected 이벤트 payload에 포함된다."""
    import json
    from apps.tenants.models import TenantConfig

    tenant, raw_key = tenant_with_key

    def _seed():
        config = TenantConfig.objects.get(tenant=tenant)
        config.welcome_message = "안녕하세요! 무엇을 도와드릴까요?"
        config.save()
    await adb(_seed)()

    stream_resp = await aopen_stream(tenant, "v-welcome")

    first_chunk = await aread_first_chunk(stream_resp)
    assert "event: connected" in first_chunk
    payload = json.loads(first_chunk.split("data: ", 1)[1])
    assert payload["welcome_message"] == "안녕하세요! 무엇을 도와드릴까요?"


@pytest.mark.django_db(transaction=True)
async def test_no_welcome_message_when_empty(tenant_with_key):
    """welcome_message가 비어있으면 connected 이벤트 payload에 포함되지 않는다."""
    import json

    tenant, raw_key = tenant_with_key
    stream_resp = await aopen_stream(tenant, "v-no-welcome")

    first_chunk = await aread_first_chunk(stream_resp)
    payload = json.loads(first_chunk.split("data: ", 1)[1])
    assert "welcome_message" not in payload
