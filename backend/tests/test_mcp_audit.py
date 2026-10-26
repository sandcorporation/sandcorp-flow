"""MCP 쓰기 감사 로그 + 도구별 권한 비트·힌트(#208, 우산 #207).

- 쓰기 도구(`writes=True`)의 `tools/call`은 성공·ToolError·내부 오류·권한 거부·인자 오류·스코프 거부 모두 한 줄씩 남는다.
- 읽기 도구는 남지 않는다.
- 권한은 도구마다 비트(`permission`)로 `has_permission` 한 곳에서 판정한다.
- `tools/list`의 annotations에 readOnlyHint·destructiveHint·idempotentHint가 정확히 붙는다.
- 읽기 경로: 테넌트 HTTP `GET /api/tenant/audit-events/`·MCP `list_audit_events` — 내 테넌트만, Admin(AUDIT_READ)만.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.mcp import registry
from apps.mcp.access import TenantArgs
from apps.mcp.models import McpAuditEvent
from apps.mcp.registry import JSONObject, ToolContext, ToolError
from apps.tenants import permissions as P
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator·write_tool은 픽스처
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    post,
    rpc,
    signer,
    tool_text,
    write_tool,
)
from resource_token_fakes import MEMBER_SUB, Signer
from tests.utils import make_membership, member_token


class _Args(TenantArgs):
    text: str = ""


@pytest.fixture
def failing_tools() -> Iterator[dict[str, str]]:
    """ToolError를 던지는 쓰기 도구와 예상 못 한 예외를 내는 쓰기 도구."""

    async def refuse(ctx: ToolContext, args: _Args) -> JSONObject:
        raise ToolError("flow_not_found", "없는 흐름")

    async def crash(ctx: ToolContext, args: _Args) -> JSONObject:
        raise RuntimeError("boom")

    registry.register("test_refuse_write", "시험용", _Args, scope="flow.write", permission=P.FLOW_EDIT, writes=True)(refuse)
    registry.register("test_crash_write", "시험용", _Args, scope="flow.write", permission=P.FLOW_EDIT, writes=True)(crash)
    yield {"refuse": "test_refuse_write", "crash": "test_crash_write"}
    registry.unregister("test_refuse_write")
    registry.unregister("test_crash_write")


@pytest.fixture
def member_write_tool() -> Iterator[str]:
    """권한 비트가 없는(None) 쓰기 도구 — Member도 부른다(#207 Member 쓰기 도구의 바탕)."""

    async def note(ctx: ToolContext, args: _Args) -> JSONObject:
        return {"ok": True}

    registry.register("test_member_write", "시험용", _Args, scope="flow.write", permission=None, writes=True)(note)
    yield "test_member_write"
    registry.unregister("test_member_write")


def rows() -> list[McpAuditEvent]:
    return list(McpAuditEvent.objects.order_by("id"))


# ── 기록 ────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_successful_write_is_recorded_with_caller_and_token_ids(signer: Signer, write_tool: str) -> None:
    tenant = make_tenant("acme")
    membership = member_of(tenant, Membership.ROLE_ADMIN)
    token = mcp_token(signer, client_id="claude-connector", jti="jti-1")

    body = call(token, write_tool, {"text": "hello"})

    assert body["result"]["isError"] is False
    [event] = rows()
    assert event.tool == write_tool
    assert event.tenant_id == tenant.id
    assert event.user_id == membership.user_id
    assert (event.token_sub, event.client_id, event.jti) == (MEMBER_SUB, "claude-connector", "jti-1")
    assert event.arguments == {"text": "hello"}
    assert event.result_code == "ok"
    assert event.confirm_stage is None
    assert event.created_at is not None


@pytest.mark.django_db
def test_tool_error_is_recorded_with_its_code(signer: Signer, failing_tools: dict[str, str]) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), failing_tools["refuse"])

    assert body["result"]["isError"] is True
    assert [e.result_code for e in rows()] == ["flow_not_found"]


@pytest.mark.django_db
def test_internal_error_is_recorded(signer: Signer, failing_tools: dict[str, str]) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = post(rpc("tools/call", {"name": failing_tools["crash"], "arguments": {}}), mcp_token(signer)).json()

    assert body["error"]["code"] == -32603
    assert [e.result_code for e in rows()] == ["internal"]


@pytest.mark.django_db
def test_member_refused_by_permission_bit_is_recorded(signer: Signer, write_tool: str) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_MEMBER)

    body = call(mcp_token(signer), write_tool, {"text": "x"})

    assert body["result"]["isError"] is True
    assert "admin_required" in tool_text(body)
    [event] = rows()
    assert (event.result_code, event.tenant_id) == ("admin_required", tenant.id)


@pytest.mark.django_db
def test_write_tool_without_permission_bit_lets_member_in(signer: Signer, member_write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_MEMBER)

    body = call(mcp_token(signer), member_write_tool)

    assert body["result"]["isError"] is False
    assert [e.result_code for e in rows()] == ["ok"]


@pytest.mark.django_db
def test_invalid_arguments_and_unknown_tenant_are_recorded(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    token = mcp_token(signer)

    call(token, write_tool, {"text": 123})
    call(token, write_tool, {"tenant": "someone-else"})

    assert [(e.result_code, e.tenant_id) for e in rows()] == [("invalid_arguments", None), ("tenant_not_found", None)]


@pytest.mark.django_db
def test_insufficient_scope_is_recorded(signer: Signer, write_tool: str) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)

    response = post(rpc("tools/call", {"name": write_tool, "arguments": {}}), mcp_token(signer, scope="flow.read"))

    assert response.status_code == 403
    [event] = rows()
    assert (event.result_code, event.tenant_id) == ("insufficient_scope", tenant.id)


@pytest.mark.django_db
def test_read_tools_are_not_recorded(signer: Signer) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    token = mcp_token(signer)

    for tool in ("list_tenants", "list_flows", "get_flow_schema", "list_documents", "list_sessions", "list_audit_events"):
        assert call(token, tool)["result"]["isError"] is False, tool

    assert rows() == []


@pytest.mark.django_db
def test_long_arguments_are_cut_to_the_cap(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    call(mcp_token(signer), write_tool, {"text": "가" * 20_000})

    [event] = rows()
    assert event.arguments["_truncated"] is True
    assert len(event.arguments["preview"]) <= 4000
    assert len(json.dumps(event.arguments, ensure_ascii=False)) < 4200


@pytest.mark.django_db
def test_tool_does_not_run_when_the_record_cannot_be_written(signer: Signer) -> None:
    """기록이 안 되면 실행하지 않는다(fail-closed). Postgres jsonb는 NUL 문자를 받지 않는다 — 실제 쓰기 실패."""
    ran: list[str] = []

    async def effect(ctx: ToolContext, args: _Args) -> JSONObject:
        ran.append(args.text)
        return {}

    registry.register("test_effect_write", "시험용", _Args, scope="flow.write", permission=P.FLOW_EDIT, writes=True)(effect)
    try:
        member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
        body = post(rpc("tools/call", {"name": "test_effect_write", "arguments": {"text": "a\u0000b"}}), mcp_token(signer)).json()
    finally:
        registry.unregister("test_effect_write")

    assert body["error"]["code"] == -32603
    assert ran == []
    assert rows() == []


# ── 권한 비트·힌트 ─────────────────────────────────────────────────────────────

WRITE_HINTS = {
    #               permission       destructive idempotent
    "create_flow": (P.FLOW_EDIT, False, False),
    "update_flow": (P.FLOW_EDIT, True, False),
    "save_version": (P.FLOW_EDIT, False, True),
    "deploy_version": (P.FLOW_DEPLOY, True, False),
    "test_turn": (P.FLOW_EDIT, False, False),
    "archive_flow": (P.FLOW_EDIT, True, False),
}
READ_TOOLS = {
    "list_tenants": None,
    "get_flow_schema": None,
    "list_flows": None,
    "get_flow": None,
    "list_documents": None,
    "search_knowledge": None,
    "list_sessions": None,
    "get_session": None,
    "list_audit_events": P.AUDIT_READ,
    "get_settings": None,
    "list_members": None,
    "list_invites": P.AGENTS_MANAGE,
    "list_secrets": None,
    "list_allowed_domains": None,
    "get_usage": None,
    "list_deploy_requests": P.FLOW_APPROVE,
    "get_session_trace": None,
    "list_visitors": None,
    "get_visitor_memory": None,
}


def test_existing_tools_keep_their_scopes_and_get_permission_bits() -> None:
    for name, (permission, _, _) in WRITE_HINTS.items():
        tool = registry.get_tool(name)
        assert tool is not None and tool.writes and tool.scope == ("flow.deploy" if name == "deploy_version" else "flow.write"), name  # #210
        assert tool.permission == permission, name
    for name, permission in READ_TOOLS.items():
        tool = registry.get_tool(name)
        assert tool is not None and not tool.writes and tool.scope == "flow.read", name
        assert tool.permission == permission, name
    assert registry.MCP_SCOPES == ["flow.read", "flow.write", "flow.deploy", "flow.admin"]


@pytest.mark.django_db
def test_tools_list_carries_all_three_hints(signer: Signer) -> None:
    listed = {t["name"]: t["annotations"] for t in post(rpc("tools/list"), mcp_token(signer)).json()["result"]["tools"]}

    for name, (_, destructive, idempotent) in WRITE_HINTS.items():
        assert listed[name] == {"readOnlyHint": False, "destructiveHint": destructive, "idempotentHint": idempotent}, name
    for name in READ_TOOLS:
        assert listed[name] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}, name


def test_write_tool_is_destructive_unless_told_otherwise() -> None:
    async def noop(ctx: ToolContext, args: _Args) -> JSONObject:
        return {}

    registry.register("test_default_hints", "시험용", _Args, scope="flow.write", permission=None, writes=True)(noop)
    try:
        tool = registry.get_tool("test_default_hints")
        assert tool is not None
        assert tool.annotations() == {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}
    finally:
        registry.unregister("test_default_hints")
    with pytest.raises(ValueError):
        registry.register("test_bad_hints", "시험용", _Args, scope="flow.read", permission=None, destructive=True)(noop)


def test_audit_read_is_admin_only_bit() -> None:
    assert P.AUDIT_READ in P.ADMIN_ONLY


# ── 읽기 경로: MCP list_audit_events ──────────────────────────────────────────


@pytest.mark.django_db
def test_list_audit_events_shows_only_my_tenant_newest_first_with_cursor(signer: Signer, write_tool: str) -> None:
    acme = make_tenant("acme")
    member_of(acme, Membership.ROLE_ADMIN)
    other = make_tenant("other")
    McpAuditEvent.objects.create(tenant=other, tool="create_flow", token_sub="x", client_id="c", jti="j", result_code="ok")
    token = mcp_token(signer)
    for text in ("one", "two", "three"):
        call(token, write_tool, {"text": text})

    first = call(token, "list_audit_events", {"limit": 2})["result"]["structuredContent"]
    second = call(token, "list_audit_events", {"limit": 2, "cursor": first["next_cursor"]})["result"]["structuredContent"]

    assert [e["arguments"]["text"] for e in first["events"]] == ["three", "two"]
    assert [e["arguments"]["text"] for e in second["events"]] == ["one"]
    assert second["next_cursor"] is None
    event = first["events"][0]
    assert set(event) >= {"id", "created_at", "tool", "user_sub", "client_id", "jti", "arguments", "result_code", "confirm_stage"}


@pytest.mark.django_db
def test_list_audit_events_refuses_member(signer: Signer) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_MEMBER)

    body = call(mcp_token(signer), "list_audit_events")

    assert body["result"]["isError"] is True
    assert "admin_required" in tool_text(body)


@pytest.mark.django_db
def test_list_audit_events_rejects_bad_cursor(signer: Signer) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), "list_audit_events", {"cursor": "garbage"})

    assert "invalid_cursor" in tool_text(body)


# ── 읽기 경로: 테넌트 HTTP ─────────────────────────────────────────────────────

URL = "/api/tenant/audit-events/"


def h(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.mark.django_db
def test_http_admin_reads_own_tenant_events_with_cursor() -> None:
    acme = make_tenant("acme")
    admin = make_membership(acme, "admin")
    other = make_tenant("other")
    for i in range(3):
        McpAuditEvent.objects.create(
            tenant=acme, user=admin.user, tool="update_flow", token_sub=admin.user.sub, client_id="c", jti=f"j{i}",
            arguments={"i": i}, result_code="ok",
        )
    McpAuditEvent.objects.create(tenant=other, tool="update_flow", token_sub="x", client_id="c", jti="o", result_code="ok")
    client = Client()

    first = client.get(URL, {"limit": 2}, **h(member_token(admin)))
    assert first.status_code == 200, first.content
    page = first.json()
    second = client.get(URL, {"limit": 2, "cursor": page["next_cursor"]}, **h(member_token(admin))).json()

    assert [e["jti"] for e in page["items"]] == ["j2", "j1"]
    assert [e["jti"] for e in second["items"]] == ["j0"]
    assert second["next_cursor"] is None
    assert page["items"][0]["user_sub"] == admin.user.sub
    assert page["items"][0]["arguments"] == {"i": 2}


@pytest.mark.django_db
def test_http_member_is_forbidden() -> None:
    member = make_membership(make_tenant("acme"), "member")

    assert Client().get(URL, **h(member_token(member))).status_code == 403


@pytest.mark.django_db
def test_http_bad_cursor_is_400() -> None:
    admin = make_membership(make_tenant("acme"), "admin")

    assert Client().get(URL, {"cursor": "garbage"}, **h(member_token(admin))).status_code == 400


# ── 보존 ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_prune_deletes_events_older_than_retention(settings) -> None:
    settings.MCP_AUDIT_RETENTION_DAYS = 180
    tenant = make_tenant("acme")
    old = McpAuditEvent.objects.create(tenant=tenant, tool="t", token_sub="s", client_id="c", jti="old", result_code="ok")
    McpAuditEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=181))
    McpAuditEvent.objects.create(tenant=tenant, tool="t", token_sub="s", client_id="c", jti="new", result_code="ok")

    call_command("prune_mcp_audit")

    assert [e.jti for e in rows()] == ["new"]
