"""MCP 팀 쓰기 도구(#215, 우산 #207).

``change_member_role``·``deactivate_member``·``cancel_invite``: flow.admin 스코프, AGENTS_MANAGE(Admin), 2단계.
초대 만들기는 MCP에 없다 — 어떤 응답에도 초대 토큰·링크가 없다.
"""

from __future__ import annotations

import json

import pytest
from mcp_helpers import call, clock, flow_validator, make_tenant, mcp_token, member_of, post, rpc, signer, tool_text  # noqa: F401
from authkit_fakes import Signer

from apps.mcp.models import McpAuditEvent
from apps.tenants.members import create_invite
from apps.tenants.models import Membership, RefreshToken
from apps.tenants.refresh_tokens import issue_session
from tests.utils import make_membership

SCOPES = "flow.read flow.write flow.admin"


def ok(body: dict) -> dict:
    assert not body["result"].get("isError"), tool_text(body)
    return body["result"]["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def team(role: str = Membership.ROLE_ADMIN):
    """caller(MEMBER_SUB)가 role로 있고, 다른 Member 한 명(김멤버)이 있는 테넌트."""
    tenant = make_tenant("acme")
    me = member_of(tenant, role)
    other = make_membership(tenant, "member", name="김멤버", email="kim@example.com")
    return tenant, me, other


def two_step(token: str, tool: str, args: dict) -> dict:
    first = ok(call(token, tool, args))
    return ok(call(token, tool, {**args, "confirm_token": first["confirm_token"]}))


# ── change_member_role ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_change_role_previews_then_changes(signer: Signer) -> None:
    _, _, other = team()
    token = mcp_token(signer, scope=SCOPES)
    args = {"member_id": str(other.id), "role": "admin"}

    first = ok(call(token, "change_member_role", args))
    preview = first["preview"]
    other.refresh_from_db()
    assert other.role == "member"  # 토큰 없이는 부작용 0
    assert preview["name"] == "김멤버" and preview["email"] == "kim@example.com"
    assert preview["current_role"] == "member" and preview["new_role"] == "admin" and "warning" not in preview

    out = ok(call(token, "change_member_role", {**args, "confirm_token": first["confirm_token"]}))

    assert out["changed"] is True and out["role"] == "admin"
    other.refresh_from_db()
    assert other.role == "admin"


@pytest.mark.django_db
def test_demoting_the_last_admin_is_refused_at_preview(signer: Signer) -> None:
    _, me, _ = team()
    token = mcp_token(signer, scope=SCOPES)

    assert "last_admin" in err(call(token, "change_member_role", {"member_id": str(me.id), "role": "member"}))
    me.refresh_from_db()
    assert me.role == "admin"


@pytest.mark.django_db
def test_self_demotion_warns_when_another_admin_exists(signer: Signer) -> None:
    tenant, me, _ = team()
    make_membership(tenant, "admin", name="다른관리자", email="adm@example.com")
    token = mcp_token(signer, scope=SCOPES)

    preview = ok(call(token, "change_member_role", {"member_id": str(me.id), "role": "member"}))["preview"]

    assert "자신" in preview["warning"] and preview["is_me"] is True
    me.refresh_from_db()
    assert me.role == "admin"


# ── deactivate_member ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_deactivate_previews_then_deactivates_and_closes_sessions(signer: Signer) -> None:
    _, _, other = team()
    issue_session(other)
    token = mcp_token(signer, scope=SCOPES)
    args = {"member_id": str(other.id)}

    first = ok(call(token, "deactivate_member", args))
    other.refresh_from_db()
    assert other.is_active is True
    assert first["preview"]["name"] == "김멤버" and "즉시" in first["preview"]["effect"] and "MCP" in first["preview"]["effect"]

    out = ok(call(token, "deactivate_member", {**args, "confirm_token": first["confirm_token"]}))

    assert out["deactivated"] is True and out["is_active"] is False
    other.refresh_from_db()
    assert other.is_active is False
    assert not RefreshToken.objects.filter(membership=other, revoked=False).exists()  # 열린 로그인 세션도 닫힘


@pytest.mark.django_db
def test_deactivating_the_last_admin_is_refused_at_preview(signer: Signer) -> None:
    _, me, _ = team()

    assert "last_admin" in err(call(mcp_token(signer, scope=SCOPES), "deactivate_member", {"member_id": str(me.id)}))
    me.refresh_from_db()
    assert me.is_active is True


@pytest.mark.django_db
def test_self_deactivation_warns_when_another_admin_exists(signer: Signer) -> None:
    tenant, me, _ = team()
    make_membership(tenant, "admin", name="다른관리자", email="adm@example.com")

    preview = ok(call(mcp_token(signer, scope=SCOPES), "deactivate_member", {"member_id": str(me.id)}))["preview"]

    assert "자신" in preview["warning"]


@pytest.mark.django_db
def test_deactivated_member_is_locked_out_of_mcp(signer: Signer) -> None:
    tenant, me, _ = team()
    other_admin = make_membership(tenant, "admin", name="다른관리자", email="adm@example.com")
    token = mcp_token(signer, scope=SCOPES)
    assert not call(token, "list_members")["result"].get("isError")

    two_step(token, "deactivate_member", {"member_id": str(me.id)})

    assert "no_membership" in err(call(token, "list_members"))
    other_admin.refresh_from_db()
    assert other_admin.is_active is True


# ── cancel_invite ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_cancel_invite_previews_then_revokes(signer: Signer) -> None:
    tenant, _, _ = team()
    invite, raw = create_invite(tenant, Membership.ROLE_ADMIN)
    token = mcp_token(signer, scope=SCOPES)
    args = {"invite_id": str(invite.id)}

    first = ok(call(token, "cancel_invite", args))
    invite.refresh_from_db()
    assert invite.revoked is False
    preview = first["preview"]
    assert preview["role"] == "admin" and preview["expires_at"] and preview["created_at"]

    out = ok(call(token, "cancel_invite", {**args, "confirm_token": first["confirm_token"]}))

    assert out["cancelled"] is True
    invite.refresh_from_db()
    assert invite.revoked is True
    assert raw not in json.dumps([first, out])
    assert "invite_not_found" in err(call(token, "cancel_invite", args))  # 이미 취소됨


# ── 공통 ─────────────────────────────────────────────────────────────────────


def _args(tool: str, other, invite) -> dict:
    return {
        "change_member_role": {"member_id": str(other.id), "role": "admin"},
        "deactivate_member": {"member_id": str(other.id)},
        "cancel_invite": {"invite_id": str(invite.id)},
    }[tool]


TOOLS = ["change_member_role", "deactivate_member", "cancel_invite"]


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_member_is_refused(signer: Signer, tool: str) -> None:
    tenant, _, other = team(Membership.ROLE_MEMBER)
    invite, _ = create_invite(tenant, "member")

    assert "admin_required" in err(call(mcp_token(signer, scope=SCOPES), tool, _args(tool, other, invite)))
    other.refresh_from_db()
    invite.refresh_from_db()
    assert other.role == "member" and other.is_active and not invite.revoked


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_flow_write_only_token_is_403_asking_for_flow_admin(signer: Signer, tool: str) -> None:
    tenant, _, other = team()
    invite, _ = create_invite(tenant, "member")

    response = post(
        rpc("tools/call", {"name": tool, "arguments": _args(tool, other, invite)}),
        mcp_token(signer, scope="flow.read flow.write"),
    )

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.admin"'


@pytest.mark.django_db
def test_other_tenants_ids_are_not_found(signer: Signer) -> None:
    team()
    stranger = make_tenant("beta")
    foreign = make_membership(stranger, "member", name="남", email="x@example.com")
    foreign_invite, _ = create_invite(stranger, "member")
    token = mcp_token(signer, scope=SCOPES)

    assert "member_not_found" in err(call(token, "change_member_role", {"member_id": str(foreign.id), "role": "admin"}))
    assert "member_not_found" in err(call(token, "deactivate_member", {"member_id": str(foreign.id)}))
    assert "member_not_found" in err(call(token, "deactivate_member", {"member_id": "not-a-uuid"}))
    assert "invite_not_found" in err(call(token, "cancel_invite", {"invite_id": str(foreign_invite.id)}))
    foreign.refresh_from_db()
    foreign_invite.refresh_from_db()
    assert foreign.role == "member" and foreign.is_active and not foreign_invite.revoked


@pytest.mark.django_db
def test_audit_records_both_stages_without_the_token(signer: Signer) -> None:
    _, _, other = team()
    token = mcp_token(signer, scope=SCOPES)
    first = ok(call(token, "deactivate_member", {"member_id": str(other.id)}))
    ok(call(token, "deactivate_member", {"member_id": str(other.id), "confirm_token": first["confirm_token"]}))

    events = list(McpAuditEvent.objects.filter(tool="deactivate_member").order_by("id"))

    assert [e.confirm_stage for e in events] == ["preview", "confirm"]
    assert all(e.result_code == "ok" for e in events)
    assert first["confirm_token"] not in json.dumps([e.arguments for e in events])


@pytest.mark.django_db
def test_no_response_contains_invite_token_or_url(signer: Signer) -> None:
    tenant, _, other = team()
    invite, raw = create_invite(tenant, "member")
    token = mcp_token(signer, scope=SCOPES)
    seen = []

    for tool in TOOLS:
        args = _args(tool, other, invite)
        first = call(token, tool, args)
        second = call(token, tool, {**args, "confirm_token": ok(first)["confirm_token"]})
        seen += [first, second]

    text = json.dumps(seen, ensure_ascii=False)
    assert raw not in text and "authorize/member" not in text and "invite=" not in text
    assert "create_invite" not in {t["name"] for t in post(rpc("tools/list"), token).json()["result"]["tools"]}
