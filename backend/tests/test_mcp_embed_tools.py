"""MCP 임베드 도구(#250, 우산 #245 E5) — 읽기 칸과 쓰기 3개(``set_flow_embed``·``set_default_flow``·``set_embed_origins``).

- 읽기: ``list_flows``·``get_flow``에 임베드 칸, ``get_settings``에 테넌트 ``embed_origins``.
- 쓰기: 스코프 ``flow.deploy``·권한 ``FLOW_DEPLOY``(Admin)·2단계 확인·감사. 규칙은 REST와 같은 서비스·검증 함수를 쓴다.
"""

from __future__ import annotations

import json

import pytest

from apps.flows import embed_policy, services
from apps.flows.models import Flow
from apps.mcp import registry
from apps.mcp.models import McpAuditEvent
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
from authkit_fakes import Signer
from test_mcp_flow_tools import SIMPLE_OPS, new_flow

FULL = "flow.read flow.write flow.deploy flow.admin"
DEPLOY = "flow.read flow.write flow.deploy"
WRITE_ONLY = "flow.read flow.write"
TOOLS = ("set_flow_embed", "set_default_flow", "set_embed_origins")


def ok(body: dict) -> dict:
    assert not body["result"].get("isError"), tool_text(body)
    return body["result"]["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def setup_tenant(role: str = Membership.ROLE_ADMIN, slug: str = "acme"):
    tenant = make_tenant(slug)
    member_of(tenant, role)
    return tenant


def deployed_flow(signer: Signer, name: str = "상담") -> str:
    token = mcp_token(signer, scope=FULL)
    flow_id = new_flow(token, name=name, ops=SIMPLE_OPS)
    ok(call(token, "save_version", {"flow_id": flow_id}))
    ok(call(token, "deploy_version", {"flow_id": flow_id, "version": 1}))
    return flow_id


def draft_flow(signer: Signer, name: str = "초안") -> str:
    return new_flow(mcp_token(signer, scope=FULL), name=name, ops=SIMPLE_OPS)


def confirmed(token: str, tool: str, args: dict) -> dict:
    first = ok(call(token, tool, args))
    assert first["confirm_required"] is True
    return ok(call(token, tool, {**args, "confirm_token": first["confirm_token"]}))


# ── 읽기 ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_flows_and_get_flow_carry_the_embed_columns(signer: Signer) -> None:
    tenant = setup_tenant()
    flow_id = deployed_flow(signer)
    flow = Flow.objects.get(pk=flow_id)
    flow.embed_origins = ["https://a.example.com"]
    flow.save(update_fields=["embed_origins"])
    token = mcp_token(signer, scope="flow.read")

    row = next(f for f in ok(call(token, "list_flows"))["flows"] if f["id"] == flow_id)
    got = ok(call(token, "get_flow", {"flow_id": flow_id}))

    for view in (row, got):
        assert view["embed_key"] == flow.embed_key
        assert view["embed_enabled"] is False
        assert view["embed_path"] == f"/chatbot/{tenant.slug}/{flow.embed_key}/"
        assert view["embed_origins"] == ["https://a.example.com"]
        assert view["is_default"] is view["is_public"]


@pytest.mark.django_db
def test_embed_columns_mark_the_default_flow(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=FULL)
    confirmed(token, "set_default_flow", {"flow_id": flow_id})

    row = next(f for f in ok(call(token, "list_flows"))["flows"] if f["id"] == flow_id)

    assert row["is_default"] is True and row["is_public"] is True
    others = [f for f in ok(call(token, "list_flows"))["flows"] if f["id"] != flow_id]
    assert all(f["is_default"] is False for f in others)


@pytest.mark.django_db
def test_get_settings_gives_tenant_embed_origins(signer: Signer) -> None:
    tenant = setup_tenant()
    tenant.config.embed_origins = ["https://site.example.com"]
    tenant.config.save()

    got = ok(call(mcp_token(signer, scope="flow.read"), "get_settings"))

    assert got["embed_origins"] == ["https://site.example.com"]


# ── 레지스트리 ───────────────────────────────────────────────────────────────


def test_embed_tools_need_flow_deploy_scope_and_bit() -> None:
    for name in TOOLS:
        tool = registry.get_tool(name)
        assert tool is not None and tool.writes and tool.confirm, name
        assert tool.scope == "flow.deploy" and tool.permission == P.FLOW_DEPLOY, name


# ── set_flow_embed ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_set_flow_embed_is_two_step_and_turns_it_on(signer: Signer) -> None:
    tenant = setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)

    first = ok(call(token, "set_flow_embed", {"flow_id": flow_id, "enabled": True}))

    assert first["confirm_required"] is True
    preview = first["preview"]
    flow = Flow.objects.get(pk=flow_id)
    assert preview["embed_path"] == f"/chatbot/{tenant.slug}/{flow.embed_key}/"
    assert preview["current"] == {"enabled": False, "origins": []} and preview["new"]["enabled"] is True
    assert "열" in preview["effect"]
    assert Flow.objects.get(pk=flow_id).embed_enabled is False  # 토큰 없이는 부작용 0

    out = ok(call(token, "set_flow_embed", {"flow_id": flow_id, "enabled": True, "confirm_token": first["confirm_token"]}))

    assert out["embed_enabled"] is True and out["id"] == flow_id
    assert Flow.objects.get(pk=flow_id).embed_enabled is True


@pytest.mark.django_db
def test_set_flow_embed_off_preview_says_pasted_code_shows_not_open(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    confirmed(token, "set_flow_embed", {"flow_id": flow_id, "enabled": True})

    preview = ok(call(token, "set_flow_embed", {"flow_id": flow_id, "enabled": False}))["preview"]

    assert "열려 있지 않음" in preview["effect"]
    out = confirmed(token, "set_flow_embed", {"flow_id": flow_id, "enabled": False})
    assert out["embed_enabled"] is False


@pytest.mark.django_db
def test_set_flow_embed_off_on_the_default_flow_stays_open(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    confirmed(token, "set_default_flow", {"flow_id": flow_id})

    preview = ok(call(token, "set_flow_embed", {"flow_id": flow_id, "enabled": False}))["preview"]

    assert "기본 흐름" in preview["effect"] and "열려 있" in preview["effect"]


@pytest.mark.django_db
def test_set_flow_embed_writes_origins_with_the_same_normalizer_and_clears_policy_cache(signer: Signer) -> None:
    tenant = setup_tenant()
    flow_id = deployed_flow(signer)
    flow = Flow.objects.get(pk=flow_id)
    assert embed_policy.frame_ancestors(tenant.slug, flow.embed_key) == "*"  # 캐시를 덥힌다
    token = mcp_token(signer, scope=DEPLOY)
    args = {"flow_id": flow_id, "enabled": True, "origins": [" HTTPS://Shop.Example.com/ ", "https://shop.example.com"]}

    preview = ok(call(token, "set_flow_embed", args))["preview"]
    assert preview["new"]["origins"] == ["https://shop.example.com"]  # 정규화된 값을 미리 보인다
    assert "https://shop.example.com" in preview["effect"]

    out = ok(call(token, "set_flow_embed", {**args, "confirm_token": ok(call(token, "set_flow_embed", args))["confirm_token"]}))

    assert out["embed_origins"] == ["https://shop.example.com"]
    assert Flow.objects.get(pk=flow_id).embed_origins == ["https://shop.example.com"]
    assert embed_policy.frame_ancestors(tenant.slug, flow.embed_key) == "'self' https://shop.example.com"


@pytest.mark.django_db
def test_set_flow_embed_without_origins_leaves_the_list_alone(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    Flow.objects.filter(pk=flow_id).update(embed_origins=["https://keep.example.com"])

    out = confirmed(mcp_token(signer, scope=DEPLOY), "set_flow_embed", {"flow_id": flow_id, "enabled": True})

    assert out["embed_origins"] == ["https://keep.example.com"]


@pytest.mark.django_db
def test_set_flow_embed_empty_origins_clears_the_flow_list(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    Flow.objects.filter(pk=flow_id).update(embed_origins=["https://old.example.com"])
    token = mcp_token(signer, scope=DEPLOY)

    preview = ok(call(token, "set_flow_embed", {"flow_id": flow_id, "enabled": True, "origins": []}))["preview"]
    assert "테넌트 목록" in preview["effect"]

    out = confirmed(token, "set_flow_embed", {"flow_id": flow_id, "enabled": True, "origins": []})
    assert out["embed_origins"] == []


@pytest.mark.django_db
def test_set_flow_embed_rejects_bad_origins_before_anything_changes(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    args = {"flow_id": flow_id, "enabled": True, "origins": ["https://ok.example.com", "http://evil.example.com"]}

    assert "invalid_embed_origins" in err(call(token, "set_flow_embed", args))  # 미리보기 단계에서도 막는다
    flow = Flow.objects.get(pk=flow_id)
    assert flow.embed_enabled is False and flow.embed_origins == []


@pytest.mark.django_db
def test_set_flow_embed_not_deployed_is_not_embeddable_and_origins_stay(signer: Signer) -> None:
    setup_tenant()
    flow_id = draft_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    args = {"flow_id": flow_id, "enabled": True, "origins": ["https://a.example.com"]}

    first = ok(call(token, "set_flow_embed", args))
    message = err(call(token, "set_flow_embed", {**args, "confirm_token": first["confirm_token"]}))

    assert "not_embeddable" in message and "먼저 배포" in message
    flow = Flow.objects.get(pk=flow_id)
    assert flow.embed_enabled is False and flow.embed_origins == []  # 반쯤 적용되지 않는다


@pytest.mark.django_db
def test_set_flow_embed_unknown_flow_and_missing_args(signer: Signer) -> None:
    setup_tenant()
    token = mcp_token(signer, scope=DEPLOY)

    assert "flow_not_found" in err(call(token, "set_flow_embed", {"flow_id": "00000000-0000-0000-0000-000000000000", "enabled": True}))
    assert "invalid_arguments" in err(call(token, "set_flow_embed", {"flow_id": "x"}))


# ── set_default_flow ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_set_default_flow_switches_atomically_and_previews_the_effect(signer: Signer) -> None:
    tenant = setup_tenant()
    first_id = deployed_flow(signer, "하나")
    second_id = deployed_flow(signer, "둘")
    token = mcp_token(signer, scope=DEPLOY)
    confirmed(token, "set_default_flow", {"flow_id": first_id})

    first = ok(call(token, "set_default_flow", {"flow_id": second_id}))

    preview = first["preview"]
    assert preview["current_default"]["id"] == first_id and preview["new_default"]["id"] == second_id
    assert f"/chatbot/{tenant.slug}/" in preview["effect"] and "새 대화" in preview["effect"]
    assert "끊기지 않" in preview["effect"]
    assert Flow.objects.get(pk=first_id).is_public is True  # 토큰 없이는 부작용 0

    out = ok(call(token, "set_default_flow", {"flow_id": second_id, "confirm_token": first["confirm_token"]}))

    assert out["id"] == second_id and out["is_default"] is True
    assert Flow.objects.get(pk=first_id).is_public is False
    assert Flow.objects.get(pk=second_id).is_public is True


@pytest.mark.django_db
def test_set_default_flow_already_default_is_flagged_no_change(signer: Signer) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    confirmed(token, "set_default_flow", {"flow_id": flow_id})

    assert ok(call(token, "set_default_flow", {"flow_id": flow_id}))["preview"]["no_change"] is True


@pytest.mark.django_db
def test_set_default_flow_refuses_an_undeployed_flow_with_the_rest_reason(signer: Signer) -> None:
    setup_tenant()
    flow_id = draft_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)

    first = ok(call(token, "set_default_flow", {"flow_id": flow_id}))
    message = err(call(token, "set_default_flow", {"flow_id": flow_id, "confirm_token": first["confirm_token"]}))

    assert "not_embeddable" in message and "아직 배포한 버전이 없어" in message
    assert Flow.objects.get(pk=flow_id).is_public is False


@pytest.mark.django_db
def test_set_default_flow_unknown_flow(signer: Signer) -> None:
    setup_tenant()

    message = err(call(mcp_token(signer, scope=DEPLOY), "set_default_flow", {"flow_id": "00000000-0000-0000-0000-000000000000"}))

    assert "flow_not_found" in message


# ── set_embed_origins (테넌트) ───────────────────────────────────────────────


@pytest.mark.django_db
def test_set_embed_origins_is_two_step_normalizes_and_clears_cache(signer: Signer) -> None:
    tenant = setup_tenant()
    assert embed_policy.frame_ancestors(tenant.slug, "") == "*"  # 캐시를 덥힌다
    token = mcp_token(signer, scope=DEPLOY)
    args = {"origins": ["HTTPS://Site.Example.com/", "https://*.example.org"]}

    first = ok(call(token, "set_embed_origins", args))

    preview = first["preview"]
    assert preview["current"] == [] and preview["new"] == ["https://site.example.com", "https://*.example.org"]
    assert f"/chatbot/{tenant.slug}/" in preview["effect"]
    tenant.config.refresh_from_db()
    assert tenant.config.embed_origins == []  # 토큰 없이는 부작용 0

    out = ok(call(token, "set_embed_origins", {**args, "confirm_token": first["confirm_token"]}))

    assert out == {"embed_origins": {"before": [], "after": ["https://site.example.com", "https://*.example.org"]}}
    tenant.config.refresh_from_db()
    assert tenant.config.embed_origins == ["https://site.example.com", "https://*.example.org"]
    assert embed_policy.frame_ancestors(tenant.slug, "") == "'self' https://site.example.com https://*.example.org"


@pytest.mark.django_db
def test_set_embed_origins_empty_list_opens_to_anywhere_and_says_so(signer: Signer) -> None:
    tenant = setup_tenant()
    tenant.config.embed_origins = ["https://site.example.com"]
    tenant.config.save()

    preview = ok(call(mcp_token(signer, scope=DEPLOY), "set_embed_origins", {"origins": []}))["preview"]

    assert "어디서나" in preview["effect"]


@pytest.mark.django_db
def test_set_embed_origins_rejects_bad_values_like_rest(signer: Signer) -> None:
    tenant = setup_tenant()
    token = mcp_token(signer, scope=DEPLOY)

    for bad in (["http://evil.example.com"], ["https://a.example.com; script-src *"], ["https://x.example.com/path"]):
        assert "invalid_embed_origins" in err(call(token, "set_embed_origins", {"origins": bad}))

    too_many = [f"https://s{i}.example.com" for i in range(embed_policy.MAX_EMBED_ORIGINS + 1)]
    assert "invalid_embed_origins" in err(call(token, "set_embed_origins", {"origins": too_many}))
    tenant.config.refresh_from_db()
    assert tenant.config.embed_origins == []


@pytest.mark.django_db
def test_set_embed_origins_same_list_is_no_change(signer: Signer) -> None:
    tenant = setup_tenant()
    tenant.config.embed_origins = ["https://site.example.com"]
    tenant.config.save()

    preview = ok(call(mcp_token(signer, scope=DEPLOY), "set_embed_origins", {"origins": ["https://site.example.com"]}))["preview"]

    assert preview["no_change"] is True


# ── 권한·스코프·감사 ─────────────────────────────────────────────────────────

CALLS = {
    "set_flow_embed": lambda flow_id: {"flow_id": flow_id, "enabled": True},
    "set_default_flow": lambda flow_id: {"flow_id": flow_id},
    "set_embed_origins": lambda flow_id: {"origins": ["https://a.example.com"]},
}


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_member_cannot_use_embed_tools(signer: Signer, tool: str) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN, sub="sub-admin")
    member_of(tenant, Membership.ROLE_MEMBER)
    flow_id = str(Flow.objects.create(tenant=tenant, name="x").pk)

    assert "admin_required" in err(call(mcp_token(signer, scope=DEPLOY), tool, CALLS[tool](flow_id)))

    flow = Flow.objects.get(pk=flow_id)
    tenant.config.refresh_from_db()
    assert flow.embed_enabled is False and flow.is_public is False and tenant.config.embed_origins == []


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_flow_write_only_token_is_403_asking_for_flow_deploy(signer: Signer, tool: str) -> None:
    setup_tenant()

    response = post(
        rpc("tools/call", {"name": tool, "arguments": CALLS[tool]("00000000-0000-0000-0000-000000000000")}),
        mcp_token(signer, scope=WRITE_ONLY),
    )

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.deploy"'


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_embed_tool_audit_records_both_stages_without_the_token(signer: Signer, tool: str) -> None:
    setup_tenant()
    flow_id = deployed_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    McpAuditEvent.objects.all().delete()

    first = ok(call(token, tool, CALLS[tool](flow_id)))
    ok(call(token, tool, {**CALLS[tool](flow_id), "confirm_token": first["confirm_token"]}))

    events = list(McpAuditEvent.objects.filter(tool=tool).order_by("id"))
    assert [e.confirm_stage for e in events] == ["preview", "confirm"]
    assert all(e.result_code == "ok" for e in events)
    assert first["confirm_token"] not in json.dumps([e.arguments for e in events])


@pytest.mark.django_db
def test_embed_tool_failure_is_audited_with_its_code(signer: Signer) -> None:
    setup_tenant()
    flow_id = draft_flow(signer)
    token = mcp_token(signer, scope=DEPLOY)
    McpAuditEvent.objects.all().delete()
    first = ok(call(token, "set_default_flow", {"flow_id": flow_id}))

    err(call(token, "set_default_flow", {"flow_id": flow_id, "confirm_token": first["confirm_token"]}))

    last = McpAuditEvent.objects.filter(tool="set_default_flow").order_by("-id").first()
    assert last is not None and last.confirm_stage == "confirm" and last.result_code == "not_embeddable"


@pytest.mark.django_db
def test_rest_and_mcp_reach_the_same_state(signer: Signer) -> None:
    """같은 서비스 함수를 쓴다 — MCP로 켠 것을 서비스가 같은 값으로 본다."""
    tenant = setup_tenant()
    flow_id = deployed_flow(signer)
    confirmed(mcp_token(signer, scope=DEPLOY), "set_flow_embed", {"flow_id": flow_id, "enabled": True})

    assert services.get_flow(tenant.id, flow_id).embed_enabled is True
