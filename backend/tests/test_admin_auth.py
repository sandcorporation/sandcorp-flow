"""Issue 98/99/100 — 어드민 access/refresh 엔드포인트 (ADR-0013).

실제 Ninja 엔드포인트·실제 DB. 쿠키(Set-Cookie)와 401 동작을 외부에서 검증한다.
"""
import pytest


def _operator_login(client):
    """sandcorp-auth 콜백(#21, test_operator_login.py)이 연 세션과 같은 상태를 만든다: 대표 Operator +
    Refresh Family + 쿠키. 돌려주는 값은 그 세션의 Access Token."""
    from apps.tenants.auth import create_operator_access_token
    from apps.tenants.members import owner_operator
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


# ── 멤버(테넌트) access/refresh (issue 99 → #40) ──────────────────────────────

def _member_login(client, membership):
    """콜백(#40, test_member_login.py)이 연 테넌트 세션과 같은 상태: Refresh Family + 쿠키."""
    from apps.tenants.refresh_tokens import issue_session
    from tests.utils import member_token

    client.cookies["member_refresh"] = issue_session(membership)
    return member_token(membership)


@pytest.mark.django_db
def test_member_refresh_rotates_cookie_and_returns_new_access(client, tenant_with_key):
    from tests.utils import make_membership

    tenant, _ = tenant_with_key
    _member_login(client, make_membership(tenant))
    old = client.cookies["member_refresh"].value

    r = client.post("/api/tenant/members/auth/refresh")

    assert r.status_code == 200
    assert "access_token" in r.json()
    cookie = r.cookies["member_refresh"]
    assert cookie.value != old
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/api/tenant/members/auth"


@pytest.mark.django_db
def test_member_refresh_without_cookie_rejected(client):
    r = client.post("/api/tenant/members/auth/refresh")
    assert r.status_code == 401


@pytest.mark.django_db
def test_deactivated_member_cannot_refresh(client, tenant_with_key):
    from tests.utils import make_membership

    tenant, _ = tenant_with_key
    m = make_membership(tenant)
    _member_login(client, m)
    m.is_active = False
    m.save()

    assert client.post("/api/tenant/members/auth/refresh").status_code == 401


@pytest.mark.django_db
def test_member_logout_all_revokes_every_device(tenant_with_key):
    from django.test import Client
    from tests.utils import make_membership

    tenant, _ = tenant_with_key
    m = make_membership(tenant)
    c1, c2 = Client(), Client()
    access1 = _member_login(c1, m)
    _member_login(c2, m)
    raw2 = c2.cookies["member_refresh"].value

    c1.post("/api/tenant/members/auth/logout-all", HTTP_AUTHORIZATION=f"Bearer {access1}")

    c2.cookies["member_refresh"] = raw2
    assert c2.post("/api/tenant/members/auth/refresh").status_code == 401


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
