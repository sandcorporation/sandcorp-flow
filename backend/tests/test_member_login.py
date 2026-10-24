"""#40 — 고객(일반 사용자) 로그인·멤버십·초대 링크·break-glass.

sandcorp-auth는 외부 경계라 토큰 엔드포인트만 대역(`FakeAuthorizationServer`, 실제 Ed25519 서명)으로
바꾸고, 콜백·검증기·DB·쿠키·Ninja 엔드포인트는 실제로 돈다. 브라우저가 겪는 순서대로 시험한다:
초대 링크(`/api/auth/authorize?invite=`) → sandcorp-auth → `/api/auth/callback` → 테넌트 세션.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.utils import timezone

from operator_login_fakes import FakeAuthorizationServer
from resource_token_fakes import MEMBER_SUB
from test_operator_login import clock, flow_client, server, signer  # noqa: F401 — 픽스처 재사용
from tests.utils import make_membership, member_token

MEMBER_HOME = "/admin-ui/tenant"
OTHER_SUB = "m_01JB2Z3Y4X5W6V7T8S9R0Q1P2M"


@pytest.fixture(autouse=True)
def member_home(settings):
    settings.AUTH_MEMBER_REDIRECT = MEMBER_HOME


def login(
    client: Client,
    server: FakeAuthorizationServer,
    *,
    sub: str = MEMBER_SUB,
    start_url: str = "/api/auth/authorize",
    profile: dict[str, object] | None = None,
):
    """브라우저 한 바퀴: authorize → (sandcorp-auth 동의) → callback. 콜백 응답을 돌려준다."""
    started = client.get(start_url)
    assert started.status_code == 302
    query = server.approve(
        started["Location"], sub=sub, acct="member", flow_scope="login",
        id_token_overrides=profile or {"name": "김고객", "email": None, "email_verified": None},
    )
    return client.get("/api/auth/callback", query)


def landing(response) -> tuple[str, dict[str, str]]:
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    return location.path, {k: v[0] for k, v in parse_qs(location.query).items()}


def _hdr(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def open_session(client: Client) -> str:
    """콜백이 심은 Refresh 쿠키로 Access Token을 받는다(화면이 하는 일)."""
    r = client.post("/api/tenant/members/auth/refresh")
    assert r.status_code == 200
    return r.json()["access_token"]


def new_tenant(name: str):
    from apps.tenants.models import Tenant

    return Tenant.objects.create_with_key(name=name, raw_key=secrets.token_urlsafe(16))


def member_user(sub: str = MEMBER_SUB):
    from apps.tenants.members import member_user as upsert

    return upsert("https://auth.sandcorp.co.kr", sub)


def invite_url(client: Client, token: str, role: str = "member") -> str:
    r = client.post("/api/tenant/invites/", {"role": role}, content_type="application/json", **_hdr(token))
    assert r.status_code == 201, r.content
    url = urlsplit(r.json()["url"])
    return f"{url.path}?{url.query}"


# ── 콜백: 멤버십 없음 / 하나 / 여럿 ──────────────────────────────────────────


@pytest.mark.django_db
def test_member_without_membership_is_sent_to_onboarding(server) -> None:
    from apps.tenants.models import User

    client = Client()
    path, params = landing(login(client, server))

    assert path == MEMBER_HOME and params == {"onboarding": "1"}
    user = User.objects.get(sub=MEMBER_SUB)
    assert user.acct == "member" and user.name == "김고객" and user.email is None  # 이메일 없음(Kakao)
    assert "member_refresh" not in client.cookies
    r = client.get("/api/auth/member/tenants")
    assert r.status_code == 200
    assert r.json() == {"name": "김고객", "email": None, "tenants": []}


@pytest.mark.django_db
def test_member_with_one_membership_gets_that_tenant_session(server) -> None:
    tenant = new_tenant("Solo Co")
    make_membership(tenant, "member", sub=MEMBER_SUB)

    client = Client()
    path, params = landing(login(client, server))

    assert path == MEMBER_HOME and params == {}
    me = client.get("/api/tenant/members/me", **_hdr(open_session(client))).json()
    assert me["tenant_name"] == "Solo Co" and me["role"] == "member"
    assert me["name"] == "김고객"  # 로그인할 때 ID 토큰으로 프로필 캐시를 갱신한다


@pytest.mark.django_db
def test_member_with_several_memberships_picks_a_tenant(server) -> None:
    a, b = new_tenant("Alpha"), new_tenant("Beta")
    make_membership(a, "admin", sub=MEMBER_SUB)
    from apps.tenants.models import Membership, User

    Membership.objects.create(user=User.objects.get(sub=MEMBER_SUB), tenant=b, role="member")

    client = Client()
    path, params = landing(login(client, server))
    assert params == {"select_tenant": "1"}
    assert "member_refresh" not in client.cookies

    names = {t["tenant_name"] for t in client.get("/api/auth/member/tenants").json()["tenants"]}
    assert names == {"Alpha", "Beta"}
    r = client.post("/api/auth/member/select", {"tenant_id": str(b.id)}, content_type="application/json")
    assert r.status_code == 200
    me = client.get("/api/tenant/members/me", **_hdr(r.json()["access_token"])).json()
    assert me["tenant_name"] == "Beta" and me["role"] == "member"
    assert client.post("/api/tenant/members/auth/refresh").status_code == 200  # Refresh 쿠키도 심었다


@pytest.mark.django_db
def test_cannot_select_a_tenant_you_do_not_belong_to(server) -> None:
    make_membership(new_tenant("Mine A"), "member", sub=MEMBER_SUB)
    from apps.tenants.models import Membership, User

    Membership.objects.create(user=User.objects.get(sub=MEMBER_SUB), tenant=new_tenant("Mine B"))
    stranger = new_tenant("Not Mine")

    client = Client()
    login(client, server)
    r = client.post("/api/auth/member/select", {"tenant_id": str(stranger.id)}, content_type="application/json")

    assert r.status_code == 403
    assert "member_refresh" not in client.cookies


@pytest.mark.django_db
def test_inactive_membership_and_suspended_tenant_do_not_count(server) -> None:
    make_membership(new_tenant("Gone"), "member", sub=MEMBER_SUB, is_active=False)
    suspended = new_tenant("Paused")
    from apps.tenants.models import Membership, User

    Membership.objects.create(user=User.objects.get(sub=MEMBER_SUB), tenant=suspended)
    suspended.is_active = False
    suspended.save()

    path, params = landing(login(Client(), server))
    assert params == {"onboarding": "1"}


@pytest.mark.django_db
def test_ticket_endpoints_need_the_callback_ticket() -> None:
    client = Client()
    assert client.get("/api/auth/member/tenants").status_code == 401
    client.cookies["member_ticket"] = "forged"
    r = client.post("/api/auth/member/select", {"tenant_id": "x"}, content_type="application/json")
    assert r.status_code == 401


@pytest.mark.django_db
def test_member_session_token_is_not_an_operator_session(server) -> None:
    make_membership(new_tenant("Solo Co"), "admin", sub=MEMBER_SUB)
    client = Client()
    login(client, server)
    token = open_session(client)

    assert client.get("/api/operator/tenants/", **_hdr(token)).status_code == 401


# ── 팀원 초대 링크 ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_invite_link_makes_the_invitee_a_member_with_the_given_role(
    server, tenant_admin_token, tenant_with_key
) -> None:
    from apps.tenants.models import Membership

    tenant, _ = tenant_with_key
    url = invite_url(Client(), tenant_admin_token, role="admin")

    invitee = Client()
    path, params = landing(login(invitee, server, start_url=url))

    assert path == MEMBER_HOME and params == {}
    joined = Membership.objects.get(user__sub=MEMBER_SUB)
    assert joined.tenant == tenant and joined.role == "admin"
    me = invitee.get("/api/tenant/members/me", **_hdr(open_session(invitee))).json()
    assert me["tenant_id"] == str(tenant.id)


@pytest.mark.django_db
def test_invite_token_is_unguessable_and_stored_only_as_a_hash(tenant_admin_token) -> None:
    from apps.tenants.models import TenantInvite

    r = Client().post("/api/tenant/invites/", {}, content_type="application/json", **_hdr(tenant_admin_token))
    data = r.json()
    assert data["role"] == "member"  # 기본 역할
    assert len(data["token"]) >= 43  # 256비트
    invite = TenantInvite.objects.get(id=data["id"])
    assert data["token"] not in invite.token_hash
    assert timedelta(days=6, hours=23) < invite.expires_at - timezone.now() <= timedelta(days=7)


@pytest.mark.django_db
def test_invite_can_be_used_only_once(server, tenant_admin_token) -> None:
    from apps.tenants.models import Membership

    url = invite_url(Client(), tenant_admin_token)
    landing(login(Client(), server, start_url=url))

    path, params = landing(login(Client(), server, start_url=url, sub=OTHER_SUB))

    assert params == {"login_error": "invite_used"}
    assert not Membership.objects.filter(user__sub=OTHER_SUB).exists()


@pytest.mark.django_db
def test_expired_invite_is_refused(server, tenant_admin_token) -> None:
    from apps.tenants.models import Membership, TenantInvite

    url = invite_url(Client(), tenant_admin_token)
    TenantInvite.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    path, params = landing(login(Client(), server, start_url=url))

    assert params == {"login_error": "invite_expired"}
    assert not Membership.objects.filter(user__sub=MEMBER_SUB).exists()


@pytest.mark.django_db
def test_revoked_or_made_up_invite_is_refused(server, tenant_admin_token) -> None:
    client = Client()
    url = invite_url(client, tenant_admin_token)
    invite_id = client.get("/api/tenant/invites/", **_hdr(tenant_admin_token)).json()[0]["id"]
    assert client.delete(f"/api/tenant/invites/{invite_id}", **_hdr(tenant_admin_token)).status_code == 204
    assert client.get("/api/tenant/invites/", **_hdr(tenant_admin_token)).json() == []

    assert landing(login(Client(), server, start_url=url))[1] == {"login_error": "invite_invalid"}
    made_up = "/api/auth/authorize?invite=not-a-real-token"
    assert landing(login(Client(), server, start_url=made_up))[1] == {"login_error": "invite_invalid"}


@pytest.mark.django_db
def test_invite_joins_only_its_own_tenant(server, tenant_admin_token, tenant_with_key) -> None:
    """다른 테넌트의 Admin 초대가 있어도, 이 링크는 발급한 테넌트에만 넣는다."""
    from apps.tenants.models import Membership

    tenant, _ = tenant_with_key
    other = new_tenant("Elsewhere")
    url = invite_url(Client(), tenant_admin_token)

    landing(login(Client(), server, start_url=url))

    assert set(Membership.objects.filter(user__sub=MEMBER_SUB).values_list("tenant", flat=True)) == {tenant.id}
    assert not Membership.objects.filter(tenant=other).exists()


@pytest.mark.django_db
def test_existing_member_keeps_their_role_when_reinvited(server, tenant_admin_token, tenant_with_key) -> None:
    tenant, _ = tenant_with_key
    me = make_membership(tenant, "admin", sub=MEMBER_SUB)
    url = invite_url(Client(), tenant_admin_token, role="member")

    landing(login(Client(), server, start_url=url))

    me.refresh_from_db()
    assert me.role == "admin"


@pytest.mark.django_db
def test_invite_brings_back_a_deactivated_member(server, tenant_admin_token, tenant_with_key) -> None:
    tenant, _ = tenant_with_key
    gone = make_membership(tenant, "admin", sub=MEMBER_SUB, is_active=False)
    url = invite_url(Client(), tenant_admin_token, role="member")

    landing(login(Client(), server, start_url=url))

    gone.refresh_from_db()
    assert gone.is_active and gone.role == "member"


@pytest.mark.django_db
def test_member_cannot_manage_invites(tenant_member_token) -> None:
    client = Client()
    r = client.post("/api/tenant/invites/", {"role": "member"}, content_type="application/json",
                    **_hdr(tenant_member_token))
    assert r.status_code == 403
    assert client.get("/api/tenant/invites/", **_hdr(tenant_member_token)).status_code == 403


@pytest.mark.django_db
def test_admin_cannot_revoke_another_tenants_invite(tenant_admin_token) -> None:
    from apps.tenants.members import create_invite

    foreign, _ = create_invite(new_tenant("Foreign"), "admin")
    r = Client().delete(f"/api/tenant/invites/{foreign.id}", **_hdr(tenant_admin_token))

    assert r.status_code == 404
    foreign.refresh_from_db()
    assert not foreign.revoked


@pytest.mark.django_db
def test_invalid_invite_role_is_rejected(tenant_admin_token) -> None:
    r = Client().post("/api/tenant/invites/", {"role": "owner"}, content_type="application/json",
                      **_hdr(tenant_admin_token))
    assert r.status_code == 400


# ── TENANT_KEY break-glass: Admin 초대 링크 발급(ADR-0025 D) ────────────────


@pytest.mark.django_db
def test_tenant_key_issues_an_admin_invite_that_restores_an_admin(server, tenant_with_key) -> None:
    from apps.tenants.models import Membership

    tenant, raw_key = tenant_with_key
    url = invite_url(Client(), raw_key, role="admin")

    landing(login(Client(), server, start_url=url))

    restored = Membership.objects.get(user__sub=MEMBER_SUB)
    assert restored.tenant == tenant and restored.role == "admin"


# ── sub로 사용자 잊기(탈퇴 웹훅 #55가 부른다) ────────────────────────────────


@pytest.mark.django_db
def test_forget_user_removes_memberships_and_sessions_but_keeps_handled_escalations() -> None:
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation, EscalationClaim
    from apps.tenants.members import forget_user
    from apps.tenants.models import Membership, RefreshToken, User
    from apps.tenants.refresh_tokens import issue_session

    tenant = new_tenant("Forget Co")
    m = make_membership(tenant, "admin", sub=MEMBER_SUB)
    issue_session(m)
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-1")
    esc = Escalation.objects.create(session=session, trigger_type=Escalation.TRIGGER_AGENT)
    EscalationClaim.objects.create(escalation=esc, claimed_by=m)

    assert forget_user("https://auth.sandcorp.co.kr", MEMBER_SUB) is True

    assert not User.objects.filter(sub=MEMBER_SUB).exists()
    assert not Membership.objects.exists() and not RefreshToken.objects.exists()
    assert EscalationClaim.objects.get(escalation=esc).claimed_by is None
    assert forget_user("https://auth.sandcorp.co.kr", MEMBER_SUB) is False


@pytest.mark.django_db
def test_forget_user_never_removes_the_owner() -> None:
    from apps.tenants.members import forget_user
    from apps.tenants.models import Operator
    from apps.tenants.operator_login import owner_operator

    owner_operator("https://auth.sandcorp.co.kr", "farrar142")

    assert forget_user("https://auth.sandcorp.co.kr", "farrar142") is False
    assert Operator.objects.filter(user__sub="farrar142").exists()


@pytest.mark.django_db
def test_member_token_from_helper_matches_login_session(tenant_with_key) -> None:
    """시험 도우미(`member_token`)가 콜백이 여는 세션과 같은 토큰을 만든다 — 다른 시험의 전제."""
    tenant, _ = tenant_with_key
    m = make_membership(tenant, "member")
    me = Client().get("/api/tenant/members/me", **_hdr(member_token(m)))
    assert me.status_code == 200 and me.json()["id"] == str(m.id)
