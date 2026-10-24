"""Issue 98/99/100 — 어드민 access/refresh 엔드포인트 (ADR-0013).

실제 Ninja 엔드포인트·실제 DB. 쿠키(Set-Cookie)와 401 동작을 외부에서 검증한다.
"""
import pytest


def _operator_login(client):
    """sandcorp-auth 콜백(#21, test_operator_login.py)이 연 세션과 같은 상태를 만든다: 대표 Operator +
    Refresh Family + 쿠키. 돌려주는 값은 그 세션의 Access Token."""
    from apps.tenants.auth import create_operator_access_token
    from apps.tenants.operator_login import owner_operator
    from apps.tenants.refresh_tokens import issue_session

    op = owner_operator("https://auth.sandcorp.co.kr", "farrar142")
    client.cookies["op_refresh"] = issue_session(op)
    return create_operator_access_token(op)


@pytest.mark.django_db
def test_operator_refresh_rotates_cookie_and_returns_new_access(client):
    _operator_login(client)
    old = client.cookies["op_refresh"].value

    r = client.post("/api/operator/auth/refresh")

    assert r.status_code == 200
    assert "access_token" in r.json()
    assert client.cookies["op_refresh"].value != old  # 회전됨


@pytest.mark.django_db
def test_operator_refresh_without_cookie_rejected(client):
    r = client.post("/api/operator/auth/refresh")
    assert r.status_code == 401


@pytest.mark.django_db
def test_operator_refresh_reuse_is_rejected(client):
    _operator_login(client)
    old = client.cookies["op_refresh"].value
    client.post("/api/operator/auth/refresh")  # 회전 → old 소비됨

    client.cookies["op_refresh"] = old  # 옛 쿠키 재생
    r = client.post("/api/operator/auth/refresh")

    assert r.status_code == 401


# ── TenantAgent access/refresh (issue 99) ─────────────────────────────────────

def _make_agent(tenant, username="alice", password="secret123"):
    from apps.tenants.models import TenantAgent
    a = TenantAgent(tenant=tenant, username=username)
    a.set_password(password)
    a.save()
    return a


def _agent_login(client, tenant, username="alice", password="secret123"):
    return client.post(
        "/api/tenant/agents/auth/login",
        {"tenant_name": tenant.name, "username": username, "password": password},
        content_type="application/json",
    )


@pytest.mark.django_db
def test_agent_login_sets_httponly_refresh_cookie(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _make_agent(tenant)

    resp = _agent_login(client, tenant)

    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "agent_refresh" in resp.cookies
    cookie = resp.cookies["agent_refresh"]
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "strict"


@pytest.mark.django_db
def test_agent_refresh_rotates_cookie_and_returns_new_access(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _make_agent(tenant)
    _agent_login(client, tenant)
    old = client.cookies["agent_refresh"].value

    r = client.post("/api/tenant/agents/auth/refresh")

    assert r.status_code == 200
    assert "access_token" in r.json()
    assert client.cookies["agent_refresh"].value != old


@pytest.mark.django_db
def test_agent_refresh_without_cookie_rejected(client):
    r = client.post("/api/tenant/agents/auth/refresh")
    assert r.status_code == 401


# ── 로그아웃 3종 (issue 100) ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_operator_logout_revokes_current_session(client):
    _operator_login(client)
    raw = client.cookies["op_refresh"].value

    client.post("/api/operator/auth/logout")

    client.cookies["op_refresh"] = raw  # 폐기된 세션의 쿠키 재생
    r = client.post("/api/operator/auth/refresh")
    assert r.status_code == 401


@pytest.mark.django_db
def test_operator_logout_all_revokes_every_device(client):
    from django.test import Client

    # 기기 1·2에서 각각 로그인(별도 클라이언트 = 별도 Family)
    c1, c2 = Client(), Client()
    access1 = _operator_login(c1)
    _operator_login(c2)
    raw2 = c2.cookies["op_refresh"].value

    # 기기 1에서 전체 로그아웃(access 토큰으로 주체 식별)
    c1.post("/api/operator/auth/logout-all", HTTP_AUTHORIZATION=f"Bearer {access1}")

    # 기기 2도 죽는다
    c2.cookies["op_refresh"] = raw2
    r = c2.post("/api/operator/auth/refresh")
    assert r.status_code == 401


@pytest.mark.django_db
def test_change_password_revokes_all_agent_sessions(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _make_agent(tenant)
    login = _agent_login(client, tenant)
    access = login.json()["access_token"]
    raw = client.cookies["agent_refresh"].value

    client.post(
        "/api/tenant/agents/me/change-password",
        {"current_password": "secret123", "new_password": "newpass456!"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )

    # 비번 변경으로 기존 모든 세션의 refresh가 폐기됨
    client.cookies["agent_refresh"] = raw
    r = client.post("/api/tenant/agents/auth/refresh")
    assert r.status_code == 401
