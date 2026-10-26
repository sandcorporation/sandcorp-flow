"""MCP 2단계 확인(미리보기 → confirm_token) 기반(#209, 우산 #207).

- `register(..., preview=fn)`으로 등록한 도구는 `confirm_token` 없이 부르면 실행하지 않고 미리보기와 토큰을 돌려준다.
- 같은 인자에 토큰을 실어 다시 부르면 그때 실행한다.
- 토큰은 도구·테넌트·사용자 sub·정규화 인자 해시에 묶이고, 5분 뒤 만료되며 한 번만 쓴다(DB 고유 키로 소비).
- 거부 사유(위조·만료·재사용·인자 불일치·다른 사용자)는 ToolError code로 나뉜다.
- 미리보기도 스코프·권한 검사를 통과해야 한다. 감사 로그에는 `preview`/`confirm` 단계가 남는다.
- 2단계 도구는 레지스트리가 `destructiveHint=true`와 설명 끝 공통 문구를 강제한다.
시험 전용 도구로 검증한다(실제 2단계 도구는 T5~T8).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core.management import call_command
from pydantic import BaseModel

from apps.mcp import confirm, registry
from apps.mcp.access import TenantArgs
from apps.mcp.models import McpAuditEvent, McpConfirmUse
from apps.mcp.registry import JSONObject, ToolContext
from apps.tenants import permissions as P
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator는 픽스처
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
)
from resource_token_fakes import FrozenClock, Signer

OTHER_SUB = "m_01JB2Z3Y4X5W6V7T8S9R0Q1P2M"
TOOL = "test_confirm_write"


class _Args(TenantArgs):
    name: str
    count: int = 1


class _Calls:
    def __init__(self) -> None:
        self.previews: list[str] = []
        self.effects: list[str] = []


@pytest.fixture(autouse=True)
def confirm_clock(clock: FrozenClock) -> Iterator[None]:
    confirm.use_clock(clock)
    yield
    confirm.use_clock(None)


@pytest.fixture
def calls() -> Iterator[_Calls]:
    """시험용 2단계 도구 둘(같은 인자 모양) — 미리보기·실행 횟수를 센다."""
    seen = _Calls()

    async def preview(ctx: ToolContext, args: _Args) -> JSONObject:
        seen.previews.append(args.name)
        return {"would_delete": args.name, "count": args.count}

    async def run(ctx: ToolContext, args: _Args) -> JSONObject:
        seen.effects.append(args.name)
        return {"deleted": args.name}

    for name in (TOOL, "test_confirm_other"):
        registry.register(name, "시험용 삭제.", _Args, scope="flow.write", permission=P.FLOW_EDIT, writes=True, preview=preview)(run)
    yield seen
    registry.unregister(TOOL)
    registry.unregister("test_confirm_other")


def _admin_tenant(slug: str = "acme", sub: str | None = None):
    tenant = make_tenant(slug)
    if sub is None:
        member_of(tenant, Membership.ROLE_ADMIN)
    else:
        member_of(tenant, Membership.ROLE_ADMIN, sub=sub)
    return tenant


def _preview(token: str, arguments: dict[str, object], tool: str = TOOL) -> JSONObject:
    body = call(token, tool, arguments)
    assert body["result"]["isError"] is False, body
    return body["result"]["structuredContent"]


def _error_code(body: dict) -> str:
    assert body["result"]["isError"] is True, body
    return tool_text(body).split(":", 1)[0]


# ── 정상 흐름 ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_first_call_returns_preview_and_token_without_running(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()
    out = _preview(mcp_token(signer), {"name": "doc-1"})

    assert out["confirm_required"] is True
    assert out["preview"] == {"would_delete": "doc-1", "count": 1}
    assert isinstance(out["confirm_token"], str) and out["confirm_token"]
    assert out["expires_in"] == 300
    assert calls.previews == ["doc-1"]
    assert calls.effects == []  # 부작용 0


@pytest.mark.django_db
def test_same_arguments_with_token_run_the_tool(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()
    token = mcp_token(signer)
    out = _preview(token, {"name": "doc-1"})

    body = call(token, TOOL, {"name": "doc-1", "confirm_token": out["confirm_token"]})

    assert body["result"]["isError"] is False, body
    assert body["result"]["structuredContent"] == {"deleted": "doc-1"}
    assert calls.effects == ["doc-1"]


@pytest.mark.django_db
def test_tenant_notation_and_default_arguments_are_normalized(signer: Signer, calls: _Calls) -> None:
    tenant = _admin_tenant()
    token = mcp_token(signer)
    out = _preview(token, {"tenant": "ACME", "name": "doc-1"})

    body = call(token, TOOL, {"tenant": str(tenant.id), "name": "doc-1", "count": 1, "confirm_token": out["confirm_token"]})

    assert body["result"]["isError"] is False, body
    assert calls.effects == ["doc-1"]


# ── 거부 사유 다섯 가지 ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_forged_token_is_refused(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()
    token = mcp_token(signer)
    real = _preview(token, {"name": "doc-1"})["confirm_token"]

    for forged in (real[:-2] + ("AA" if not real.endswith("AA") else "BB"), "not-a-token", ""):
        assert _error_code(call(token, TOOL, {"name": "doc-1", "confirm_token": forged})) == "confirm_token_invalid"
    assert calls.effects == []


@pytest.mark.django_db
def test_expired_token_is_refused(signer: Signer, clock: FrozenClock, calls: _Calls) -> None:
    _admin_tenant()
    out = _preview(mcp_token(signer), {"name": "doc-1"})

    clock.advance(301)
    body = call(mcp_token(signer), TOOL, {"name": "doc-1", "confirm_token": out["confirm_token"]})

    assert _error_code(body) == "confirm_token_expired"
    assert calls.effects == []


@pytest.mark.django_db
def test_token_works_only_once(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()
    token = mcp_token(signer)
    out = _preview(token, {"name": "doc-1"})
    arguments = {"name": "doc-1", "confirm_token": out["confirm_token"]}

    assert call(token, TOOL, arguments)["result"]["isError"] is False
    assert _error_code(call(token, TOOL, arguments)) == "confirm_token_used"
    assert calls.effects == ["doc-1"]
    assert McpConfirmUse.objects.count() == 1


@pytest.mark.django_db
def test_different_arguments_tool_or_tenant_are_refused_without_spending_the_token(signer: Signer, calls: _Calls) -> None:
    _admin_tenant("acme")
    member_of(make_tenant("beta"), Membership.ROLE_ADMIN)
    token = mcp_token(signer)
    issued = _preview(token, {"tenant": "acme", "name": "doc-1"})["confirm_token"]

    for tool, arguments in (
        (TOOL, {"tenant": "acme", "name": "doc-2"}),
        (TOOL, {"tenant": "acme", "name": "doc-1", "count": 2}),
        (TOOL, {"tenant": "beta", "name": "doc-1"}),
        ("test_confirm_other", {"tenant": "acme", "name": "doc-1"}),
    ):
        body = call(token, tool, {**arguments, "confirm_token": issued})
        assert _error_code(body) == "confirm_token_mismatch", (tool, arguments)
    assert calls.effects == []

    body = call(token, TOOL, {"tenant": "acme", "name": "doc-1", "confirm_token": issued})
    assert body["result"]["isError"] is False, body  # 불일치는 토큰을 쓰지 않는다
    assert calls.effects == ["doc-1"]


@pytest.mark.django_db
def test_other_user_cannot_use_my_token(signer: Signer, calls: _Calls) -> None:
    tenant = _admin_tenant()
    member_of(tenant, Membership.ROLE_ADMIN, sub=OTHER_SUB)
    issued = _preview(mcp_token(signer), {"name": "doc-1"})["confirm_token"]

    body = call(mcp_token(signer, sub=OTHER_SUB), TOOL, {"name": "doc-1", "confirm_token": issued})

    assert _error_code(body) == "confirm_token_wrong_user"
    assert calls.effects == []


# ── 미리보기도 권한·스코프 검사 ──────────────────────────────────────────────


@pytest.mark.django_db
def test_preview_needs_the_permission_bit(signer: Signer, calls: _Calls) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_MEMBER)

    body = call(mcp_token(signer), TOOL, {"name": "doc-1"})

    assert _error_code(body) == "admin_required"
    assert calls.previews == []


@pytest.mark.django_db
def test_preview_needs_the_scope(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()

    response = post(rpc("tools/call", {"name": TOOL, "arguments": {"name": "doc-1"}}), mcp_token(signer, scope="flow.read"))

    assert response.status_code == 403
    assert calls.previews == []


@pytest.mark.django_db
def test_invalid_arguments_never_reach_the_preview(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()

    assert _error_code(call(mcp_token(signer), TOOL, {"count": 2})) == "invalid_arguments"
    assert calls.previews == []


# ── 감사 로그 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_audit_records_preview_and_confirm_stages_without_the_token(signer: Signer, calls: _Calls) -> None:
    tenant = _admin_tenant()
    token = mcp_token(signer)
    issued = _preview(token, {"name": "doc-1"})["confirm_token"]
    call(token, TOOL, {"name": "doc-1", "confirm_token": issued})
    call(token, TOOL, {"name": "doc-1", "confirm_token": issued})

    events = list(McpAuditEvent.objects.order_by("id"))
    assert [(e.confirm_stage, e.result_code) for e in events] == [
        ("preview", "ok"),
        ("confirm", "ok"),
        ("confirm", "confirm_token_used"),
    ]
    assert all(e.tenant_id == tenant.id for e in events)
    assert events[0].arguments == {"name": "doc-1"}
    assert events[1].arguments == {"name": "doc-1", "confirm_token": "[redacted]"}
    assert issued not in str([e.arguments for e in events])


@pytest.mark.django_db
def test_ordinary_write_tools_keep_no_stage(signer: Signer) -> None:
    async def run(ctx: ToolContext, args: _Args) -> JSONObject:
        return {"ok": True}

    registry.register("test_plain_write", "시험용", _Args, scope="flow.write", permission=None, writes=True)(run)
    try:
        member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
        assert call(mcp_token(signer), "test_plain_write", {"name": "x"})["result"]["isError"] is False
    finally:
        registry.unregister("test_plain_write")
    [event] = McpAuditEvent.objects.all()
    assert event.confirm_stage is None


@pytest.mark.django_db
def test_prune_drops_spent_tokens_past_expiry(signer: Signer, calls: _Calls) -> None:
    _admin_tenant()
    token = mcp_token(signer)
    issued = _preview(token, {"name": "doc-1"})["confirm_token"]
    call(token, TOOL, {"name": "doc-1", "confirm_token": issued})
    [used] = McpConfirmUse.objects.all()
    fresh = McpConfirmUse.objects.create(jti="fresh", expires_at=used.expires_at + timedelta(days=3650))
    McpConfirmUse.objects.filter(pk=used.pk).update(expires_at=used.expires_at - timedelta(days=3650))

    call_command("prune_mcp_audit")

    assert list(McpConfirmUse.objects.values_list("jti", flat=True)) == [fresh.jti]


# ── 레지스트리 규약 ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_two_step_tool_is_listed_destructive_with_confirm_token_and_suffix(signer: Signer, calls: _Calls) -> None:
    response = post(rpc("tools/list"), mcp_token(signer))
    tools = {t["name"]: t for t in response.json()["result"]["tools"]}

    tool = tools[TOOL]
    assert tool["annotations"]["destructiveHint"] is True
    assert tool["annotations"]["readOnlyHint"] is False
    assert tool["description"].startswith("시험용 삭제.")
    assert tool["description"].endswith(confirm.DESCRIPTION_SUFFIX)
    assert "confirm_token" in tool["inputSchema"]["properties"]
    assert "confirm_token" not in tool["inputSchema"].get("required", [])
    two_step = {"restore_flow", "set_deploy_approval", "set_public_flow_view", "approve_deploy_request", "reject_deploy_request"}  # #212·#213 — 진짜 2단계 도구
    for name, existing in tools.items():  # 그 밖의 기존 도구는 2단계가 아니다
        if not name.startswith("test_") and name not in two_step:
            assert "confirm_token" not in existing["inputSchema"].get("properties", {}), name
            assert not existing["description"].endswith(confirm.DESCRIPTION_SUFFIX), name


def test_registry_refuses_inconsistent_two_step_tools() -> None:
    async def noop(ctx: ToolContext, args: _Args) -> JSONObject:
        return {}

    class _NoTenant(BaseModel):
        name: str

    async def noop_plain(ctx: ToolContext, args: _NoTenant) -> JSONObject:
        return {}

    with pytest.raises(ValueError):  # 2단계 도구는 파괴적이다
        registry.register("t_bad1", "x", _Args, scope="flow.write", permission=None, writes=True, destructive=False, preview=noop)
    with pytest.raises(ValueError):  # 읽기 도구는 2단계가 될 수 없다
        registry.register("t_bad2", "x", _Args, scope="flow.read", permission=None, preview=noop)
    with pytest.raises(ValueError):  # 토큰이 테넌트에 묶이므로 테넌트 도구여야 한다
        registry.register("t_bad3", "x", _NoTenant, scope="flow.write", permission=None, writes=True, preview=noop_plain)
    assert registry.get_tool("t_bad1") is None and registry.get_tool("t_bad3") is None
