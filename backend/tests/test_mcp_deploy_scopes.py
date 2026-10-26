"""MCP 스코프 4개 + 배포 도구의 호환 전환 + rollback_flow(#210, 우산 #207).

- 보호 리소스 메타데이터가 4개 스코프를 알린다.
- 배포 도구(`deploy_version`·`rollback_flow`)는 `flow.deploy`를 요구한다. 호환 기간에는 `flow.write`만 가진 토큰도
  통과하되 감사 로그에 `legacy_scope`가 남고 응답에 재연결 안내가 붙는다(#207 — 호환 분기를 지울 때 이 시험도 정리).
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
from resource_token_fakes import Signer
from test_mcp_flow_tools import SIMPLE_OPS, err, new_flow, ok

DEPLOYERS = ["deploy_version", "rollback_flow"]
FULL = "flow.read flow.write flow.deploy flow.admin"
LEGACY = "flow.read flow.write"
NOTICE = "커넥터를 다시 연결하면 새 권한을 받습니다"


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


def test_deploy_tools_are_flow_deploy_with_the_write_fallback() -> None:
    for name in DEPLOYERS:
        tool = registry.get_tool(name)
        assert tool is not None and tool.scope == "flow.deploy", name
        assert tool.legacy_scope == "flow.write", name
        assert tool.permission == P.FLOW_DEPLOY, name
    other = registry.get_tool("save_version")
    assert other is not None and other.scope == "flow.write" and other.legacy_scope is None


@pytest.mark.django_db
def test_flow_deploy_token_deploys_without_legacy_mark(signer: Signer) -> None:
    flow_id = two_versions(signer)

    result = ok(call(mcp_token(signer, scope=FULL), "deploy_version", {"flow_id": flow_id, "version": 2}))

    assert result["deployed"] is True and result["deployed_version"] == 2
    assert "notice" not in result
    assert last_event("deploy_version").legacy_scope is False


@pytest.mark.django_db
def test_flow_write_only_token_still_deploys_and_is_marked_legacy(signer: Signer) -> None:
    flow_id = two_versions(signer)

    result = ok(call(mcp_token(signer, scope=LEGACY), "deploy_version", {"flow_id": flow_id, "version": 2}))

    assert result["deployed"] is True and result["deployed_version"] == 2
    assert NOTICE in result["notice"]
    event = last_event("deploy_version")
    assert event.legacy_scope is True and event.result_code == "ok"


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
def test_rollback_flow_legacy_scope_marks_and_notices(signer: Signer) -> None:
    flow_id = two_versions(signer)
    ok(call(mcp_token(signer, scope=FULL), "deploy_version", {"flow_id": flow_id, "version": 2}))

    result = ok(call(mcp_token(signer, scope=LEGACY), "rollback_flow", {"flow_id": flow_id}))

    assert result["deployed_version"] == 1 and NOTICE in result["notice"]
    assert last_event("rollback_flow").legacy_scope is True


@pytest.mark.django_db
def test_member_cannot_rollback_even_with_flow_deploy(signer: Signer) -> None:
    flow_id = two_versions(signer)
    # 같은 테넌트의 Member로 바꿔 부른다.
    Membership.objects.update(role=Membership.ROLE_MEMBER)

    message = err(call(mcp_token(signer, scope=FULL), "rollback_flow", {"flow_id": flow_id}))

    assert message.startswith("admin_required")
    assert last_event("rollback_flow").result_code == "admin_required"
    assert last_event("rollback_flow").legacy_scope is False


@pytest.mark.django_db
def test_legacy_mark_is_listed_by_list_audit_events(signer: Signer) -> None:
    flow_id = two_versions(signer)
    ok(call(mcp_token(signer, scope=LEGACY), "deploy_version", {"flow_id": flow_id, "version": 2}))

    events = ok(call(mcp_token(signer, scope=FULL), "list_audit_events", {}))["events"]

    deploys = [e for e in events if e["tool"] == "deploy_version"]
    assert [e["legacy_scope"] for e in deploys] == [True, False]  # 최신순: 방금 것(legacy), 준비 때 것
