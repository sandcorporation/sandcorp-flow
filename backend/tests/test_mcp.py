"""MCP 서버 뼈대(#75, 스펙 #74 결정 1~5) — `/mcp`·보호 리소스 메타데이터·테넌트/역할 판정.

외부 경계(JWKS·시계)만 #20 대역(`resource_token_fakes`)으로 바꾸고, 검증기·DB·뷰는 실제 객체다. 도우미는 `mcp_helpers`.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from pydantic import BaseModel

from apps.mcp import registry
from apps.mcp.registry import JSONObject, ToolContext
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator·write_tool은 픽스처
    API,
    MCP,
    MCP_METADATA,
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    owner_token,
    post,
    rpc,
    signer,
    tool_text,
    write_tool,
)
from resource_token_fakes import ISSUER, OWNER, Signer


# ── 보호 리소스 메타데이터 ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_mcp_metadata_names_the_mcp_resource_and_flow_scopes() -> None:
    response = Client().get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": MCP,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["flow.read", "flow.write", "flow.deploy", "flow.admin"],
        "bearer_methods_supported": ["header"],
    }


@pytest.mark.django_db
def test_api_metadata_is_unchanged() -> None:
    body = Client().get("/.well-known/oauth-protected-resource").json()

    assert body["resource"] == API
    assert body["scopes_supported"] == ["admin", "login"]


# ── 토큰 규칙 ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_no_token_is_401_pointing_at_mcp_metadata() -> None:
    response = post(rpc("ping"))

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == MCP_METADATA


@pytest.mark.django_db
def test_api_audience_token_is_rejected_at_mcp(signer: Signer) -> None:
    response = post(rpc("ping"), signer.member_token(aud=API, scope="flow.read"))

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == MCP_METADATA + ', error="invalid_token"'


@pytest.mark.django_db
def test_mcp_audience_token_is_rejected_at_api(signer: Signer) -> None:
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {owner_token(signer, 'admin login')}"

    assert client.get("/api/operator/tenants/").status_code == 401


@pytest.mark.django_db
def test_token_without_flow_read_is_403(signer: Signer) -> None:
    response = post(rpc("ping"), mcp_token(signer, scope="login"))

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.read"'


@pytest.mark.django_db
def test_member_token_with_flow_read_answers_ping(signer: Signer) -> None:
    response = post(rpc("ping"), mcp_token(signer, scope="flow.read"))

    assert response.status_code == 200
    assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
def test_owner_token_of_someone_else_is_rejected(signer: Signer) -> None:
    response = post(rpc("ping"), signer.token(aud=MCP, sub="somebody", scope="flow.read"))

    assert response.status_code == 401


@pytest.mark.django_db
def test_write_tool_without_flow_write_is_403_step_up(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    response = post(rpc("tools/call", {"name": write_tool, "arguments": {}}), mcp_token(signer, scope="flow.read"))

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.write"'


# ── JSON-RPC 전송 규칙 ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_is_405() -> None:
    response = Client().get("/mcp")

    assert response.status_code == 405
    assert response["Allow"] == "POST"


@pytest.mark.django_db
def test_parse_error_is_400_32700(signer: Signer) -> None:
    response = post(None, mcp_token(signer), raw=b"{not json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


@pytest.mark.django_db
def test_batch_is_rejected(signer: Signer) -> None:
    response = post([rpc("ping"), rpc("ping", id=2)], mcp_token(signer))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@pytest.mark.django_db
def test_body_over_limit_is_413(signer: Signer) -> None:
    response = post(None, mcp_token(signer), raw=b'{"pad":"' + b"x" * (1024 * 1024) + b'"}')

    assert response.status_code == 413
    assert response.json()["error"]["code"] == -32600


@pytest.mark.django_db
def test_unknown_method_is_32601(signer: Signer) -> None:
    body = post(rpc("resources/list"), mcp_token(signer)).json()

    assert body["id"] == 1
    assert body["error"]["code"] == -32601


@pytest.mark.django_db
def test_invalid_envelope_is_32600(signer: Signer) -> None:
    body = post({"jsonrpc": "1.0", "id": 1, "method": "ping"}, mcp_token(signer)).json()

    assert body["error"]["code"] == -32600


@pytest.mark.django_db
def test_unsupported_protocol_version_header_is_400(signer: Signer) -> None:
    response = post(rpc("ping"), mcp_token(signer), headers={"MCP-Protocol-Version": "1999-01-01"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@pytest.mark.django_db
def test_supported_protocol_version_header_passes(signer: Signer) -> None:
    response = post(rpc("ping"), mcp_token(signer), headers={"MCP-Protocol-Version": "2025-06-18"})

    assert response.status_code == 200


@pytest.mark.django_db
def test_notification_is_202_without_body(signer: Signer) -> None:
    response = post({"jsonrpc": "2.0", "method": "notifications/initialized"}, mcp_token(signer))

    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.django_db
def test_initialize_echoes_a_supported_version_and_offers_tools(signer: Signer) -> None:
    body = post(rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}), mcp_token(signer)).json()

    result = body["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "sandcorp-flow"


@pytest.mark.django_db
def test_initialize_instructions_state_scopes_two_step_rule_and_no_secrets(signer: Signer) -> None:
    body = post(rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}), mcp_token(signer)).json()

    text = body["result"]["instructions"]
    for scope in ("flow.read", "flow.write", "flow.deploy", "flow.admin"):
        assert scope in text
    assert "confirm_token" in text and "(2단계)" in text
    assert "비밀" in text
    for name in ("set_flow_embed", "set_default_flow", "set_embed_origins"):  # 임베드 쓰기 도구(#250)
        assert name in text
    assert "접근 통제가 아니" in text
    assert "편집·배포는 Admin만 된다" not in text  # 옛 문구(Member 쓰기 도구가 생겨 사실이 아니다)


@pytest.mark.django_db
def test_initialize_with_unknown_version_answers_latest(signer: Signer) -> None:
    body = post(rpc("initialize", {"protocolVersion": "2020-01-01"}), mcp_token(signer)).json()

    assert body["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.django_db
def test_tools_list_shows_both_tools_with_schemas(signer: Signer) -> None:
    tools = post(rpc("tools/list"), mcp_token(signer)).json()["result"]["tools"]

    by_name = {t["name"]: t for t in tools}
    assert {"list_tenants", "get_flow_schema"} <= set(by_name)
    for item in by_name.values():
        assert item["description"]
        assert item["inputSchema"]["type"] == "object"
        assert item["annotations"]["readOnlyHint"] is not registry.get_tool(item["name"]).writes


@pytest.mark.django_db
def test_unknown_tool_is_32602(signer: Signer) -> None:
    body = post(rpc("tools/call", {"name": "nope", "arguments": {}}), mcp_token(signer)).json()

    assert body["error"]["code"] == -32602


@pytest.mark.django_db
def test_bad_arguments_are_a_tool_error(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), write_tool, {"text": 3})

    assert body["result"]["isError"] is True
    assert "invalid_arguments" in tool_text(body)


# ── 테넌트 판정 ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_tenants_returns_active_memberships_with_roles(signer: Signer) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    member_of(make_tenant("beta"))
    member_of(make_tenant("gone"), active=False)
    member_of(make_tenant("paused", active=False))
    member_of(make_tenant("other"), sub="m_01JB2Z3Y4X5W6V7T8S9R0Q1P2Z")

    body = call(mcp_token(signer, scope="flow.read"), "list_tenants")

    ids = {m.tenant.slug: str(m.tenant_id) for m in Membership.objects.select_related("tenant")}
    assert body["result"]["isError"] is False
    assert body["result"]["structuredContent"] == {
        "tenants": [
            {"id": ids["acme"], "slug": "acme", "name": "acme corp", "role": "admin", "can_edit": True},
            {"id": ids["beta"], "slug": "beta", "name": "beta corp", "role": "member", "can_edit": False},
        ]
    }


@pytest.mark.django_db
def test_no_membership_is_a_tool_error(signer: Signer, write_tool: str) -> None:
    body = call(mcp_token(signer), write_tool)

    assert body["result"]["isError"] is True
    assert "no_membership" in tool_text(body)


@pytest.mark.django_db
def test_single_membership_lets_tenant_be_omitted(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), write_tool, {"text": "hi"})

    assert body["result"]["structuredContent"] == {"tenant": "acme", "text": "hi"}


@pytest.mark.django_db
def test_two_memberships_require_tenant(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    member_of(make_tenant("beta"), Membership.ROLE_ADMIN)

    missing = call(mcp_token(signer), write_tool)
    chosen = call(mcp_token(signer), write_tool, {"tenant": "BETA"})

    assert missing["result"]["isError"] is True
    assert "tenant_required" in tool_text(missing) and "acme" in tool_text(missing)
    assert chosen["result"]["structuredContent"]["tenant"] == "beta"


@pytest.mark.django_db
def test_someone_elses_tenant_is_not_found(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    member_of(make_tenant("other"), Membership.ROLE_ADMIN, sub="m_01JB2Z3Y4X5W6V7T8S9R0Q1P2Z")

    body = call(mcp_token(signer), write_tool, {"tenant": "other"})

    assert body["result"]["isError"] is True
    assert "tenant_not_found" in tool_text(body)


@pytest.mark.django_db
def test_slugless_tenant_can_be_chosen_by_id(signer: Signer, write_tool: str) -> None:
    """slug는 공개 챗봇 URL용이라 null일 수 있다(#119) — tenant 인자는 id(UUID)도 받는다."""
    noslug = make_tenant(None)
    member_of(noslug, Membership.ROLE_ADMIN)
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    by_id = call(mcp_token(signer), write_tool, {"tenant": str(noslug.id), "text": "hi"})
    by_slug = call(mcp_token(signer), write_tool, {"tenant": "ACME", "text": "hi"})
    missing = call(mcp_token(signer), write_tool)

    assert by_id["result"]["isError"] is False
    assert by_id["result"]["structuredContent"] == {"tenant": None, "text": "hi"}
    assert by_slug["result"]["structuredContent"]["tenant"] == "acme"
    assert "tenant_required" in tool_text(missing) and str(noslug.id) in tool_text(missing)


@pytest.mark.django_db
def test_someone_elses_tenant_id_is_not_found(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)
    other = make_tenant(None)
    member_of(other, Membership.ROLE_ADMIN, sub="m_01JB2Z3Y4X5W6V7T8S9R0Q1P2Z")

    body = call(mcp_token(signer), write_tool, {"tenant": str(other.id)})

    assert body["result"]["isError"] is True
    assert "tenant_not_found" in tool_text(body)


@pytest.mark.django_db
def test_inactive_membership_and_tenant_are_not_usable(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("gone"), Membership.ROLE_ADMIN, active=False)
    member_of(make_tenant("paused", active=False), Membership.ROLE_ADMIN)

    for slug in ("gone", "paused"):
        body = call(mcp_token(signer), write_tool, {"tenant": slug})
        assert body["result"]["isError"] is True
        assert "no_membership" in tool_text(body)


@pytest.mark.django_db
def test_owner_sees_only_tenants_with_membership(signer: Signer) -> None:
    member_of(make_tenant("mine"), Membership.ROLE_ADMIN, sub=OWNER)
    make_tenant("not-mine")

    body = call(owner_token(signer), "list_tenants")

    assert [t["slug"] for t in body["result"]["structuredContent"]["tenants"]] == ["mine"]


@pytest.mark.django_db
def test_owner_without_membership_has_none(signer: Signer, write_tool: str) -> None:
    make_tenant("acme")

    body = call(owner_token(signer), write_tool, {"tenant": "acme"})

    assert body["result"]["isError"] is True
    assert "no_membership" in tool_text(body)


# ── 역할 판정 ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_member_role_cannot_use_write_tool_even_with_flow_write(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_MEMBER)

    body = call(mcp_token(signer, scope="flow.read flow.write"), write_tool)

    assert body["result"]["isError"] is True
    assert "admin_required" in tool_text(body)


@pytest.mark.django_db
def test_admin_role_can_use_write_tool(signer: Signer, write_tool: str) -> None:
    member_of(make_tenant("acme"), Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), write_tool, {"tenant": "acme", "text": "ok"})

    assert body["result"]["isError"] is False


# ── get_flow_schema ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_flow_schema_describes_every_node_kind(signer: Signer) -> None:
    from apps.flows.schema import NodeKind

    body = call(mcp_token(signer, scope="flow.read"), "get_flow_schema")

    schema = body["result"]["structuredContent"]
    assert set(schema["node_kinds"]) == {k.value for k in NodeKind}
    assert schema["node_kinds"]["ai_answer"]["handles"] == ["next", "handoff"]
    assert "from" in json.dumps(schema["document"])
    assert any("await_reply" in rule for rule in schema["rules"])


def test_registry_rejects_duplicate_names() -> None:
    class _Args(BaseModel):
        pass

    async def noop(ctx: ToolContext, args: _Args) -> JSONObject:
        return {}

    with pytest.raises(ValueError):
        registry.register("list_tenants", "중복", _Args, scope="flow.read", permission=None)(noop)
