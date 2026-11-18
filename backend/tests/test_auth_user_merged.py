"""#350 — sandcorp-auth 계정 합치기 웹훅(`user.merged {from_sub, to_sub}`)으로 from 사용자의 데이터를 to로 옮긴다.

봉투는 `user.deleted`와 같다(서명 헤더·`id` 멱등·`type`). 엔드포인트·검증·DB·세션 폐기는 실제로 돈다.
운영과 같은 모양을 만든다: 회원(acct=member)은 두 테넌트 Admin 멤버십(각 refresh 여럿)·초대 사용·흐름 버전·
배포 요청·MCP 감사 기록이 있고, 대표(acct=owner)는 Operator 하나만 있다. 겹침 경우는 따로 만든다.
sub는 모두 가짜다.
"""

from __future__ import annotations

import json
import secrets
from io import StringIO
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import ForeignObjectRel
from django.test import Client

from authkit_fakes import ISSUER, OWNER, FlowAuthServer
from test_auth_withdrawal_webhook import sign
from test_operator_login import clock, flow_client, server, signer  # noqa: F401 — 픽스처 재사용

SECRET = "whsec_test_merge_0123456789abcdef"
FROM_SUB = "m_01FAKEFROM0000000000000000"
TO_MEMBER_SUB = "m_01FAKETO000000000000000000"
URL = "/api/auth/webhook"
MEMBER_HOME = "/admin-ui/tenant"

GRAPH = {
    "nodes": [
        {"id": "s", "kind": "start", "code": "N-01", "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "a", "kind": "ai_answer", "code": "N-02", "position": {"x": 200, "y": 0}, "config": {}},
    ],
    "edges": [{"from": "s", "to": "a"}],
}


@pytest.fixture(autouse=True)
def webhook(settings):
    settings.AUTH_WEBHOOK_SECRET = SECRET
    settings.AUTH_ISSUER = ISSUER
    settings.AUTH_OWNER_SUB = OWNER
    settings.AUTH_MEMBER_REDIRECT = MEMBER_HOME


def merged_body(event_id: str = "evt_merge_1", from_sub: str = FROM_SUB, to_sub: str = OWNER) -> bytes:
    return json.dumps(
        {"id": event_id, "type": "user.merged", "from_sub": from_sub, "to_sub": to_sub,
         "occurred_at": "2026-11-18T03:15:00Z"},
        separators=(",", ":"),
    ).encode()


def deliver(body: bytes, signature: str | None = None):
    return Client().post(
        URL, data=body, content_type="application/json",
        HTTP_X_SANDCORP_SIGNATURE=sign(body, secret=SECRET) if signature is None else signature,
        HTTP_X_SANDCORP_EVENT="user.merged",
    )


def new_tenant(name: str):
    from apps.tenants.models import Tenant

    return Tenant.objects.create_with_key(name=name, raw_key=secrets.token_urlsafe(16))


def user(sub: str, acct: str = "member", name: str | None = None):
    from apps.tenants.models import User

    return User.objects.create(iss=ISSUER, sub=sub, acct=acct, name=name)


def owner_user():
    from apps.tenants.members import owner_operator

    return owner_operator(ISSUER, OWNER).user


def production_shape():
    """운영 모양: 회원 = 두 테넌트 Admin(refresh 각 2) + 초대 사용 + 흐름 버전 + 배포 요청 + MCP 감사 + 테넌트 생성자."""
    from apps.flows.models import DeployRequest, Flow, FlowVersion
    from apps.mcp.models import McpAuditEvent
    from apps.tenants.members import accept_invite, create_invite
    from apps.tenants.models import Membership
    from apps.tenants.refresh_tokens import issue_session

    owner = owner_user()
    source = user(FROM_SUB, name="회원")
    tenant_a, tenant_b = new_tenant("Merge A"), new_tenant("Merge B")
    tenant_a.created_by = source
    tenant_a.save(update_fields=["created_by"])
    m_a = Membership.objects.create(user=source, tenant=tenant_a, role="admin")
    _, raw_invite = create_invite(tenant_b, "admin")
    m_b = accept_invite(source, raw_invite)
    refresh = [issue_session(m) for m in (m_a, m_a, m_b, m_b)]
    flow = Flow.objects.create(tenant=tenant_a, name="상담")
    version = FlowVersion.objects.create(flow=flow, graph=GRAPH, created_by=source)
    DeployRequest.objects.create(flow=flow, version=version, requested_by=source, decided_by=source)
    McpAuditEvent.objects.create(
        tenant=tenant_a, user=source, token_sub=FROM_SUB, client_id="c", jti="j", tool="list_flows"
    )
    return owner, source, (m_a, m_b), refresh


def rows_pointing_at(user_id) -> dict[str, int]:
    """users를 가리키는 모든 FK(메타에서 읽음)마다 이 사용자를 가리키는 행 수."""
    from apps.tenants.models import User

    counts = {}
    for rel in User._meta.get_fields(include_hidden=True):  # related_name="+" FK까지
        if not isinstance(rel, ForeignObjectRel):
            continue
        model, fk = rel.related_model, rel.field
        n = model._default_manager.filter(**{fk.attname: user_id}).count()
        if n:
            counts[f"{model._meta.db_table}.{fk.column}"] = n
    return counts


def refresh_status(raw: str) -> int:
    c = Client()
    c.cookies["member_refresh"] = raw
    return c.post("/api/tenant/members/auth/refresh").status_code


# ── 웹훅 봉투 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_bad_signature_moves_nothing() -> None:
    from apps.tenants.models import User

    owner_user()
    user(FROM_SUB)
    body = merged_body()

    assert deliver(body, signature=sign(body, secret="whsec_wrong")).status_code == 401
    assert User.objects.filter(sub=FROM_SUB).exists()


# ── 운영 모양: to(대표)가 있다 ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_merge_into_owner_moves_every_reference_and_drops_the_from_row() -> None:
    from apps.mcp.models import McpAuditEvent
    from apps.tenants.models import Membership, Operator, User

    owner, source, _, refresh = production_shape()
    before = rows_pointing_at(source.id)
    operator_id = owner.operator.id

    assert deliver(merged_body()).status_code == 200

    assert not User.objects.filter(sub=FROM_SUB).exists()
    after = rows_pointing_at(owner.id)
    # 회원을 가리키던 모든 표가 그대로 대표로 왔다(+ 대표의 Operator 1:1).
    assert after == {**before, "operators.user_id": 1}
    assert {(m.tenant.name, m.role) for m in Membership.objects.filter(user=owner)} == {
        ("Merge A", "admin"), ("Merge B", "admin")
    }
    assert Operator.objects.get(user=owner).id == operator_id  # 대표 Operator는 그대로
    assert McpAuditEvent.objects.get().token_sub == FROM_SUB  # 감사 기록의 문자열 사본은 둔다
    # 옮긴 멤버십의 테넌트 세션은 닫혔다(auth가 from 계정 세션을 폐기했으니 다시 로그인).
    assert {refresh_status(raw) for raw in refresh} == {401}


@pytest.mark.django_db
def test_same_event_twice_and_a_resend_under_a_new_id_change_nothing_more() -> None:
    from apps.tenants.models import Membership

    owner, *_ = production_shape()
    assert deliver(merged_body()).status_code == 200
    first = rows_pointing_at(owner.id)

    assert deliver(merged_body()).status_code == 200  # 같은 id — 기록만 보고 끝
    assert deliver(merged_body(event_id="evt_merge_2")).status_code == 200  # 새 id — from이 없어 할 일 없음

    assert rows_pointing_at(owner.id) == first
    assert Membership.objects.filter(user=owner).count() == 2


# ── 겹침: 같은 테넌트에 둘 다 멤버 ─────────────────────────────────────────────


@pytest.mark.django_db
def test_overlapping_membership_keeps_the_target_side() -> None:
    from apps.tenants.members import create_invite
    from apps.tenants.models import Membership, TenantInvite, User

    tenant = new_tenant("Overlap Co")
    other = new_tenant("Only From Co")
    source, target = user(FROM_SUB), user(TO_MEMBER_SUB)
    from_m = Membership.objects.create(user=source, tenant=tenant, role="admin", is_active=True)
    to_m = Membership.objects.create(user=target, tenant=tenant, role="member", is_active=False)
    Membership.objects.create(user=source, tenant=other, role="member")
    invite, _ = create_invite(tenant, "member", created_by=from_m)

    assert deliver(merged_body(to_sub=TO_MEMBER_SUB)).status_code == 200

    assert not User.objects.filter(sub=FROM_SUB).exists()
    kept = Membership.objects.get(user=target, tenant=tenant)
    assert (kept.id, kept.role, kept.is_active) == (to_m.id, "member", False)  # role·is_active는 to 우선
    assert Membership.objects.filter(user=target, tenant=other).exists()  # 겹치지 않는 건 옮긴다
    # from 쪽 멤버십을 가리키던 초대 발급자는 사라지지 않고 to 쪽 멤버십으로 돈다.
    assert TenantInvite.objects.get(id=invite.id).created_by_id == to_m.id


# ── to가 없다: from 행의 sub만 바꾼다 ─────────────────────────────────────────


@pytest.mark.django_db
def test_missing_target_renames_the_from_row() -> None:
    from apps.tenants.models import Membership, User
    from apps.tenants.refresh_tokens import issue_session

    tenant = new_tenant("Rename Co")
    source = user(FROM_SUB)
    m = Membership.objects.create(user=source, tenant=tenant, role="admin")
    raw = issue_session(m)

    assert deliver(merged_body(to_sub=TO_MEMBER_SUB)).status_code == 200

    renamed = User.objects.get(id=source.id)
    assert (renamed.sub, renamed.acct) == (TO_MEMBER_SUB, "member")
    assert not User.objects.filter(sub=FROM_SUB).exists()
    assert Membership.objects.get(id=m.id).user_id == source.id
    assert refresh_status(raw) == 401


@pytest.mark.django_db
def test_missing_owner_target_becomes_the_owner_row() -> None:
    """to가 대표 sub인데 행이 없으면 acct도 owner — 그래야 대표 로그인이 이 행에 Operator를 잇는다."""
    from apps.tenants.members import owner_operator
    from apps.tenants.models import User

    source = user(FROM_SUB)

    assert deliver(merged_body(to_sub=OWNER)).status_code == 200

    assert User.objects.get(id=source.id).acct == "owner"
    assert owner_operator(ISSUER, OWNER).user_id == source.id


# ── 하지 않는 경우 ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_missing_from_is_acknowledged() -> None:
    owner = owner_user()

    assert deliver(merged_body(from_sub="m_01FAKENOBODY00000000000000")).status_code == 200
    assert rows_pointing_at(owner.id) == {"operators.user_id": 1}


@pytest.mark.django_db
def test_owner_as_the_from_side_is_refused_but_acknowledged() -> None:
    from apps.tenants.models import Operator, User

    owner = owner_user()
    target = user(TO_MEMBER_SUB)

    assert deliver(merged_body(from_sub=OWNER, to_sub=TO_MEMBER_SUB)).status_code == 200

    assert User.objects.filter(id=owner.id, sub=OWNER).exists()
    assert Operator.objects.get().user_id == owner.id
    assert rows_pointing_at(target.id) == {}


# ── 관리 명령 ───────────────────────────────────────────────────────────────


def run(*args: str) -> str:
    out = StringIO()
    call_command("merge_auth_user", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_dry_run_counts_without_writing() -> None:
    from apps.tenants.models import RefreshToken, User

    owner, source, _, _ = production_shape()
    before = rows_pointing_at(source.id)

    out = run("--from-sub", FROM_SUB, "--to-sub", OWNER, "--dry-run")

    assert "outcome: moved (dry-run" in out
    assert "  memberships.user_id: 2" in out
    assert "  flow_versions.created_by_id: 1" in out
    assert "  mcp_audit_event.user_id: 1" in out
    assert "memberships 겹침(to 쪽 남김): 0" in out
    assert "refresh 폐기: 4" in out
    assert User.objects.filter(id=source.id).exists()
    assert rows_pointing_at(source.id) == before
    assert not RefreshToken.objects.filter(revoked=True).exists()


@pytest.mark.django_db
def test_command_merges_and_refuses_an_owner_source() -> None:
    from apps.tenants.models import User

    owner, *_ = production_shape()

    assert "outcome: moved" in run("--from-sub", FROM_SUB, "--to-sub", OWNER)
    assert not User.objects.filter(sub=FROM_SUB).exists()
    with pytest.raises(CommandError, match="refused_from_owner"):
        run("--from-sub", OWNER, "--to-sub", TO_MEMBER_SUB)


# ── 합친 뒤 대표가 고객 진입으로 들어온다 ──────────────────────────────────────


def member_entry_as_owner(client: Client, server: FlowAuthServer):
    """대표의 소셜 세션(acct 없음, admin 없음, `amr` ["fed"] — A4)으로 고객 진입 한 바퀴."""
    started = client.get("/api/auth/authorize/member")
    return client.get("/api/auth/callback", server.approve(started["Location"], flow_scope="login", amr=["fed"]))


def landing(response) -> tuple[str, dict[str, str]]:
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    return location.path, {k: v[0] for k, v in parse_qs(location.query).items()}


@pytest.mark.django_db
def test_owner_opens_a_tenant_session_with_the_merged_memberships(server) -> None:
    production_shape()
    assert deliver(merged_body()).status_code == 200

    client = Client()
    path, params = landing(member_entry_as_owner(client, server))
    assert path == MEMBER_HOME and params == {"select_tenant": "1"}  # 테넌트가 둘 — 고른다

    listed = client.get("/api/auth/member/tenants")
    assert listed.status_code == 200
    tenants = {t["tenant_name"]: t["tenant_id"] for t in listed.json()["tenants"]}
    assert set(tenants) == {"Merge A", "Merge B"}

    selected = client.post(
        "/api/auth/member/select", {"tenant_id": tenants["Merge B"]}, content_type="application/json"
    )
    assert selected.status_code == 200
    token = selected.json()["access_token"]
    me = client.get("/api/tenant/members/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert me.status_code == 200, me.content


@pytest.mark.django_db
def test_owner_with_one_merged_membership_lands_in_the_session(server) -> None:
    from apps.tenants.models import Membership, Operator

    owner = owner_user()
    Membership.objects.create(user=owner, tenant=new_tenant("Single Co"), role="admin")
    client = Client()

    response = member_entry_as_owner(client, server)

    assert landing(response) == (MEMBER_HOME, {})
    assert "member_refresh" in response.cookies and "op_refresh" not in response.cookies
    assert client.post("/api/tenant/members/auth/refresh").status_code == 200
    assert Operator.objects.count() == 1  # 고객 진입이 Operator를 새로 만들지 않는다


@pytest.mark.django_db
def test_owner_without_memberships_is_still_refused_on_the_member_entry(server) -> None:
    owner_user()

    path, params = landing(member_entry_as_owner(Client(), server))

    assert path == MEMBER_HOME and params == {"login_error": "owner"}
