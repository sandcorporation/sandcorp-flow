"""MCP 스코프 4개 + 배포 도구의 `flow.deploy` 요구 + rollback_flow(#210, #220, 우산 #207).

- 보호 리소스 메타데이터가 4개 스코프를 알린다.
- 배포 도구(`deploy_version`·`rollback_flow`)는 `flow.deploy`를 요구한다. `flow.write`만 가진 토큰은
  막힌다(#220에서 호환 분기를 걷었다 — HTTP 403, scope `flow.deploy`).
"""

from __future__ import annotations

import json

import pytest
from django.test import Client

from apps.mcp import registry
from apps.mcp.models import McpAuditEvent
from apps.tenants import permissions as P
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator는 픽스처
    MCP,
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
from test_mcp_flow_tools import SIMPLE_OPS, err, new_flow, ok

DEPLOYERS = ["deploy_version", "rollback_flow"]
FULL = "flow.read flow.write flow.deploy flow.admin"
LEGACY = "flow.read flow.write"


def two_versions(signer: Signer) -> str:
    """Admin이 버전 1·2를 만들어 1을 배포해 둔다(쓰기 권한이 있는 옛 토큰으로 — 준비 단계)."""
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)
    token = mcp_token(signer, scope=FULL)
    flow_id = new_flow(token, ops=SIMPLE_OPS)
    ok(call(token, "save_version", {"flow_id": flow_id}))
    ok(call(token, "update_flow", {"flow_id": flow_id, "ops": [{"op": "upsert_node", "id": "extra", "kind": "ai_answer", "code": "N-03"}, {"op": "connect", "from": "answer", "to": "extra"}]}))
    ok(call(token, "save_version", {"flow_id": flow_id}))
    ok(call(token, "deploy_version", {"flow_id": flow_id, "version": 1}))
    return flow_id


def last_event(tool: str) -> McpAuditEvent:
    return McpAuditEvent.objects.filter(tool=tool).order_by("-id").first()  # type: ignore[return-value]


@pytest.mark.django_db
def test_metadata_lists_the_four_scopes() -> None:
    body = Client().get("/.well-known/oauth-protected-resource/mcp").json()

    assert body["scopes_supported"] == ["flow.read", "flow.write", "flow.deploy", "flow.admin"]
    assert registry.MCP_SCOPES == ["flow.read", "flow.write", "flow.deploy", "flow.admin"]


def test_deploy_tools_are_flow_deploy_only() -> None:
    for name in DEPLOYERS:
        tool = registry.get_tool(name)
        assert tool is not None and tool.scope == "flow.deploy", name
        assert tool.permission == P.FLOW_DEPLOY, name
    other = registry.get_tool("save_version")
    assert other is not None and other.scope == "flow.write"


@pytest.mark.django_db
def test_flow_deploy_token_deploys(signer: Signer) -> None:
    flow_id = two_versions(signer)

    result = ok(call(mcp_token(signer, scope=FULL), "deploy_version", {"flow_id": flow_id, "version": 2}))

    assert result["deployed"] is True and result["deployed_version"] == 2
    assert "notice" not in result


@pytest.mark.django_db
def test_flow_write_only_token_gets_403_asking_for_flow_deploy(signer: Signer) -> None:
    flow_id = two_versions(signer)
    for name in DEPLOYERS:
        args = {"flow_id": flow_id, "version": 2} if name == "deploy_version" else {"flow_id": flow_id}
        response = post(rpc("tools/call", {"name": name, "arguments": args}), mcp_token(signer, scope=LEGACY))

        assert response.status_code == 403, name
        assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.deploy"', name
        event = last_event(name)
        assert event.result_code == "insufficient_scope" and event.legacy_scope is False


@pytest.mark.django_db
def test_flow_read_only_token_gets_403_asking_for_flow_deploy(signer: Signer) -> None:
    flow_id = two_versions(signer)
    for name in DEPLOYERS:
        args = {"flow_id": flow_id, "version": 2} if name == "deploy_version" else {"flow_id": flow_id}
        response = post(rpc("tools/call", {"name": name, "arguments": args}), mcp_token(signer, scope="flow.read"))

        assert response.status_code == 403, name
        assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.deploy"', name
        assert last_event(name).result_code == "insufficient_scope"


@pytest.mark.django_db
def test_other_write_tools_keep_flow_write(signer: Signer) -> None:
    two_versions(signer)

    response = post(rpc("tools/call", {"name": "create_flow", "arguments": {"name": "x"}}), mcp_token(signer, scope="flow.read flow.deploy"))

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.write"'


@pytest.mark.django_db
def test_rollback_flow_redeploys_the_previous_version(signer: Signer) -> None:
    flow_id = two_versions(signer)
    token = mcp_token(signer, scope=FULL)
    ok(call(token, "deploy_version", {"flow_id": flow_id, "version": 2}))

    result = ok(call(token, "rollback_flow", {"flow_id": flow_id}))

    assert result["deployed"] is True and result["deployed_version"] == 1
    assert "notice" not in result
    assert last_event("rollback_flow").result_code == "ok"
    assert last_event("rollback_flow").confirm_stage is None  # 2단계 없음(사용자 결정)


@pytest.mark.django_db
def test_rollback_flow_without_earlier_version_fails_readably(signer: Signer) -> None:
    flow_id = two_versions(signer)  # 버전 1이 배포 중 — 그 앞은 없다

    message = err(call(mcp_token(signer, scope=FULL), "rollback_flow", {"flow_id": flow_id}))

    assert message.startswith("version_not_found")


@pytest.mark.django_db
def test_member_cannot_rollback_even_with_flow_deploy(signer: Signer) -> None:
    flow_id = two_versions(signer)
    # 같은 테넌트의 Member로 바꿔 부른다.
    Membership.objects.update(role=Membership.ROLE_MEMBER)

    message = err(call(mcp_token(signer, scope=FULL), "rollback_flow", {"flow_id": flow_id}))

    assert message.startswith("admin_required")
    assert last_event("rollback_flow").result_code == "admin_required"


@pytest.mark.django_db
def test_audit_listing_keeps_the_legacy_column_always_false(signer: Signer) -> None:
    flow_id = two_versions(signer)
    post(rpc("tools/call", {"name": "deploy_version", "arguments": {"flow_id": flow_id, "version": 2}}), mcp_token(signer, scope=LEGACY))

    events = ok(call(mcp_token(signer, scope=FULL), "list_audit_events", {}))["events"]

    deploys = [e for e in events if e["tool"] == "deploy_version"]
    assert [e["legacy_scope"] for e in deploys] == [False, False]  # 지난 기록용 칸(#220 이후 늘 false)
