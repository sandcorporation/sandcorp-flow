"""MCP 배포 요청 승인·거절 도구(#213, 우산 #207).

``approve_deploy_request``·``reject_deploy_request``: flow.deploy 스코프(호환 분기 없음), FLOW_APPROVE, 2단계.
MCP에서는 자기가 낸 요청을 승인하지 못한다(미리보기부터 ``self_approval``). 거절(철회)은 허용.
"""

from __future__ import annotations

import json

import pytest
from mcp_helpers import call, clock, flow_validator, make_tenant, mcp_token, member_of, post, rpc, signer, tool_text  # noqa: F401
from authkit_fakes import Signer

from apps.flows import services
from apps.flows.models import DeployRequest
from apps.flows.services import UpsertNode
from apps.mcp.models import McpAuditEvent
from apps.tenants.models import Membership, TenantConfig
from tests.test_flow_services import SIMPLE, make_flow
from tests.utils import make_membership

SCOPES = "flow.read flow.write flow.deploy"
TOOLS = ["approve_deploy_request", "reject_deploy_request"]


def ok(body: dict) -> dict:
    assert not body["result"].get("isError"), tool_text(body)
    return body["result"]["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def setup(role: str = Membership.ROLE_ADMIN, *, requester: str = "other"):
    """caller(MEMBER_SUB)와 대기 중인 배포 요청 하나. requester='self'면 호출자가 낸 요청."""
    tenant = make_tenant("acme")
    caller = member_of(tenant, role)
    TenantConfig.objects.filter(tenant=tenant).update(deploy_requires_approval=True)
    flow = make_flow(tenant, SIMPLE, name="상담")
    services.save_version(tenant.id, flow.id)
    other = make_membership(tenant, "admin", name="김요청", email="req@example.com")
    by = caller.user_id if requester == "self" else other.user_id
    req = services.submit_deploy(tenant.id, flow.id, 1, requested_by_id=by).request
    return tenant, flow, req


def deployed(tenant, flow):
    v = services.get_flow(tenant.id, flow.id).deployed_version
    return v.number if v else None


@pytest.mark.django_db
def test_approve_previews_then_deploys(signer: Signer) -> None:
    tenant, flow, req = setup()
    token = mcp_token(signer, scope=SCOPES)

    first = ok(call(token, "approve_deploy_request", {"request_id": str(req.id), "note": "좋아요"}))
    preview = first["preview"]
    assert deployed(tenant, flow) is None
    assert preview["flow_name"] == "상담" and preview["requested_version"] == 1
    assert preview["current_version"] is None and preview["status"] == "pending"
    assert preview["requested_by"] == {"name": "김요청", "email": "req@example.com"}
    assert preview["requested_at"]

    out = ok(call(token, "approve_deploy_request", {"request_id": str(req.id), "note": "좋아요", "confirm_token": first["confirm_token"]}))

    assert out["approved"] is True and out["status"] == "approved"
    assert deployed(tenant, flow) == 1
    req.refresh_from_db()
    assert req.status == DeployRequest.APPROVED and req.decision_note == "좋아요" and req.decided_by_id is not None


@pytest.mark.django_db
def test_reject_previews_then_rejects_without_deploying(signer: Signer) -> None:
    tenant, flow, req = setup()
    token = mcp_token(signer, scope=SCOPES)
    args = {"request_id": str(req.id), "note": "다시"}

    first = ok(call(token, "reject_deploy_request", args))
    assert req.status == DeployRequest.PENDING
    out = ok(call(token, "reject_deploy_request", {**args, "confirm_token": first["confirm_token"]}))

    assert out["rejected"] is True and out["status"] == "rejected"
    assert deployed(tenant, flow) is None
    req.refresh_from_db()
    assert req.status == DeployRequest.REJECTED and req.decision_note == "다시"


@pytest.mark.django_db
def test_preview_shows_current_deployed_version(signer: Signer) -> None:
    tenant, flow, req = setup()
    services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", name="v2")])
    services.save_version(tenant.id, flow.id)
    services.approve_deploy_request(tenant.id, services.submit_deploy(tenant.id, flow.id, 2).request.id)  # 2를 배포 → 앞 요청은 대체됨
    req = services.submit_deploy(tenant.id, flow.id, 1).request

    preview = ok(call(mcp_token(signer, scope=SCOPES), "reject_deploy_request", {"request_id": str(req.id)}))["preview"]

    assert preview["current_version"] == 2 and preview["requested_version"] == 1


@pytest.mark.django_db
def test_own_request_approval_is_refused_at_preview_and_confirm(signer: Signer) -> None:
    tenant, flow, req = setup(requester="self")
    token = mcp_token(signer, scope=SCOPES)

    assert "self_approval" in err(call(token, "approve_deploy_request", {"request_id": str(req.id)}))
    # 확정 단계도 막는다(토큰이 있어도): 잘못된 토큰은 토큰 오류, 서비스 규칙은 service 단계에서 self_approval
    assert "confirm_token_invalid" in err(call(token, "approve_deploy_request", {"request_id": str(req.id), "confirm_token": "x"}))
    req.refresh_from_db()
    assert req.status == DeployRequest.PENDING and deployed(tenant, flow) is None


@pytest.mark.django_db
def test_own_request_can_be_rejected_as_withdrawal(signer: Signer) -> None:
    _, _, req = setup(requester="self")
    token = mcp_token(signer, scope=SCOPES)
    first = ok(call(token, "reject_deploy_request", {"request_id": str(req.id)}))

    ok(call(token, "reject_deploy_request", {"request_id": str(req.id), "confirm_token": first["confirm_token"]}))

    req.refresh_from_db()
    assert req.status == DeployRequest.REJECTED


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_member_is_refused(signer: Signer, tool: str) -> None:
    _, _, req = setup(Membership.ROLE_MEMBER)

    assert "admin_required" in err(call(mcp_token(signer, scope=SCOPES), tool, {"request_id": str(req.id)}))
    req.refresh_from_db()
    assert req.status == DeployRequest.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_flow_write_only_token_is_403_asking_for_flow_deploy(signer: Signer, tool: str) -> None:
    _, _, req = setup()

    response = post(
        rpc("tools/call", {"name": tool, "arguments": {"request_id": str(req.id)}}),
        mcp_token(signer, scope="flow.read flow.write"),
    )

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.deploy"'
    req.refresh_from_db()
    assert req.status == DeployRequest.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_already_decided_request_fails_at_preview(signer: Signer, tool: str) -> None:
    _, _, req = setup()
    services.reject_deploy_request(req.flow.tenant_id, req.id)

    assert "request_not_pending" in err(call(mcp_token(signer, scope=SCOPES), tool, {"request_id": str(req.id)}))


@pytest.mark.django_db
@pytest.mark.parametrize("tool", TOOLS)
def test_other_tenants_or_unknown_request_is_not_found(signer: Signer, tool: str) -> None:
    setup()
    stranger = make_tenant("beta")
    other_flow = make_flow(stranger, SIMPLE, name="남의 흐름")
    services.save_version(stranger.id, other_flow.id)
    TenantConfig.objects.filter(tenant=stranger).update(deploy_requires_approval=True)
    foreign = services.submit_deploy(stranger.id, other_flow.id, 1).request
    token = mcp_token(signer, scope=SCOPES)

    assert "request_not_found" in err(call(token, tool, {"request_id": str(foreign.id), "tenant": "acme"}))
    assert "request_not_found" in err(call(token, tool, {"request_id": "not-a-uuid"}))
    foreign.refresh_from_db()
    assert foreign.status == DeployRequest.PENDING


@pytest.mark.django_db
def test_audit_records_both_stages_without_the_token(signer: Signer) -> None:
    _, _, req = setup()
    token = mcp_token(signer, scope=SCOPES)
    first = ok(call(token, "approve_deploy_request", {"request_id": str(req.id)}))
    ok(call(token, "approve_deploy_request", {"request_id": str(req.id), "confirm_token": first["confirm_token"]}))

    events = list(McpAuditEvent.objects.filter(tool="approve_deploy_request").order_by("id"))

    assert [e.confirm_stage for e in events] == ["preview", "confirm"]
    assert all(e.result_code == "ok" for e in events)
    assert first["confirm_token"] not in json.dumps([e.arguments for e in events])
