"""권한 비트 인가 (issue 208, ADR-0025 C·D → #40 Membership).

인가는 역할이 아니라 Permission 비트로 검사한다. Admin=전체, Member=전체−{조직 단위 3종},
Tenant(=TENANT_KEY 인증 주체)=Admin 등가. 앞 넷은 순수 매핑이라 DB 불필요(실 객체 in-memory).
"""
import pytest

from apps.tenants import permissions as P
from apps.tenants.models import Membership, Tenant
from tests.utils import make_membership


def test_admin_has_all_permissions():
    admin = Membership(role=P.ROLE_ADMIN)
    assert P.has_permission(admin, P.AGENTS_MANAGE)
    assert P.has_permission(admin, P.TENANT_KEY_ROTATE)
    assert P.has_permission(admin, P.SLUG_CHANGE)


def test_member_lacks_the_three_admin_only_bits():
    member = Membership(role=P.ROLE_MEMBER)
    assert not P.has_permission(member, P.AGENTS_MANAGE)
    assert not P.has_permission(member, P.TENANT_KEY_ROTATE)
    assert not P.has_permission(member, P.SLUG_CHANGE)


def test_member_has_all_other_permissions():
    member = Membership(role=P.ROLE_MEMBER)
    # 일상 운영(문서·HITL·조회 등)은 Member도 전부
    assert P.has_permission(member, "documents.manage")
    assert P.has_permission(member, "hitl.operate")
    assert P.has_permission(member, "anything.else")


def test_tenant_key_subject_is_admin_equivalent():
    # TENANT_KEY 인증 주체(Tenant)는 전체 허용 — break-glass·프로그램 프로비저닝 유지
    tenant = Tenant(name="X")
    assert P.has_permission(tenant, P.AGENTS_MANAGE)
    assert P.has_permission(tenant, P.TENANT_KEY_ROTATE)
    assert P.has_permission(tenant, P.SLUG_CHANGE)


# ── 엔드포인트 가드 (Admin 전용 3종) ─────────────────────────────────────────

MEMBERS = "/api/tenant/members/"


def _hdr(tok):
    return {"HTTP_AUTHORIZATION": f"Bearer {tok}"}


def _only_admin(tenant):
    return Membership.objects.get(tenant=tenant, role="admin")  # 픽스처 = 유일 Admin


@pytest.mark.django_db
def test_list_members_includes_role_and_profile(client, tenant_admin_token):
    r = client.get(MEMBERS, **_hdr(tenant_admin_token))
    assert r.status_code == 200
    [me] = r.json()
    assert me["role"] == "admin"
    assert me["name"] == "상담원A"
    assert "username" not in me


@pytest.mark.django_db
def test_member_cannot_reset_key(client, tenant_member_token):
    r = client.post("/api/tenant/reset-key", **_hdr(tenant_member_token))
    assert r.status_code == 403


@pytest.mark.django_db
def test_admin_can_reset_key(client, tenant_admin_token):
    r = client.post("/api/tenant/reset-key", **_hdr(tenant_admin_token))
    assert r.status_code == 200


@pytest.mark.django_db
def test_member_cannot_change_slug(client, tenant_member_token):
    r = client.patch("/api/tenant/slug/", {"slug": "my-shop"}, content_type="application/json",
                     **_hdr(tenant_member_token))
    assert r.status_code == 403


# ── 역할 변경 + last-admin 가드 (issue 210, ADR-0025 D) ──────────────────────

@pytest.mark.django_db
def test_admin_promotes_member_to_admin(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    m = make_membership(tenant, "member")
    r = client.patch(f"{MEMBERS}{m.id}/role", {"role": "admin"}, content_type="application/json",
                     **_hdr(tenant_admin_token))
    assert r.status_code == 200
    m.refresh_from_db()
    assert m.role == "admin"


@pytest.mark.django_db
def test_member_cannot_change_roles(client, tenant_member_token, tenant_with_key):
    tenant, _ = tenant_with_key
    other = make_membership(tenant, "member")
    r = client.patch(f"{MEMBERS}{other.id}/role", {"role": "admin"}, content_type="application/json",
                     **_hdr(tenant_member_token))
    assert r.status_code == 403


@pytest.mark.django_db
def test_cannot_demote_last_admin(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    admin = _only_admin(tenant)
    r = client.patch(f"{MEMBERS}{admin.id}/role", {"role": "member"}, content_type="application/json",
                     **_hdr(tenant_admin_token))
    assert r.status_code == 409
    admin.refresh_from_db()
    assert admin.role == "admin"  # 그대로


@pytest.mark.django_db
def test_cannot_deactivate_last_admin(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    admin = _only_admin(tenant)
    r = client.patch(f"{MEMBERS}{admin.id}/deactivate", **_hdr(tenant_admin_token))
    assert r.status_code == 409


@pytest.mark.django_db
def test_tenant_key_cannot_deactivate_last_admin_either(client, tenant_admin_token, tenant_with_key):
    tenant, raw_key = tenant_with_key
    admin = _only_admin(tenant)
    r = client.patch(f"{MEMBERS}{admin.id}/deactivate", **_hdr(raw_key))
    assert r.status_code == 409


@pytest.mark.django_db
def test_can_demote_admin_when_another_admin_exists(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    admin1 = _only_admin(tenant)
    make_membership(tenant, "admin")
    r = client.patch(f"{MEMBERS}{admin1.id}/role", {"role": "member"}, content_type="application/json",
                     **_hdr(tenant_admin_token))
    assert r.status_code == 200


@pytest.mark.django_db
def test_deactivated_member_loses_access_immediately(client, tenant_admin_token, tenant_with_key):
    from tests.utils import member_token

    tenant, _ = tenant_with_key
    m = make_membership(tenant, "member")
    token = member_token(m)
    assert client.get("/api/tenant/members/me", **_hdr(token)).status_code == 200

    r = client.patch(f"{MEMBERS}{m.id}/deactivate", **_hdr(tenant_admin_token))

    assert r.status_code == 200
    assert client.get("/api/tenant/members/me", **_hdr(token)).status_code == 401


@pytest.mark.django_db
def test_admin_cannot_touch_other_tenants_member(client, tenant_admin_token):
    import secrets

    other = Tenant.objects.create_with_key(name="Other Co", raw_key=secrets.token_urlsafe(16))
    stranger = make_membership(other, "member")
    r = client.patch(f"{MEMBERS}{stranger.id}/role", {"role": "admin"}, content_type="application/json",
                     **_hdr(tenant_admin_token))
    assert r.status_code == 404
