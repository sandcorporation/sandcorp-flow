"""배포 승인 규칙(#28) — 권한 비트 · 서비스 층 승인 · MCP가 같은 규칙을 탄다.

권한 판정은 ``apps.tenants.permissions`` 한 곳, 승인 규칙은 ``apps.flows.services`` 한 곳이다.
"""

from __future__ import annotations

import pytest
from mcp_helpers import call, clock, flow_validator, mcp_token, member_of, make_tenant, signer  # noqa: F401
from resource_token_fakes import Signer

from apps.flows import services
from apps.flows.models import DeployRequest
from apps.tenants import permissions as P
from apps.tenants.models import Membership, TenantConfig
from tests.test_flow_services import SIMPLE, make_flow
from tests.test_mcp_flow_tools import ok
from tests.utils import make_membership


def require_approval(tenant, on: bool = True) -> None:
    TenantConfig.objects.filter(tenant=tenant).update(deploy_requires_approval=on)


def versioned(tenant, name="상담"):
    flow = make_flow(tenant, SIMPLE, name=name)
    services.save_version(tenant.id, flow.id)
    return flow


# ── 권한 비트 ────────────────────────────────────────────────────────────────


def test_flow_bits_are_admin_only_and_member_reads_only():
    admin, member = Membership(role=P.ROLE_ADMIN), Membership(role=P.ROLE_MEMBER)
    for bit in (P.FLOW_EDIT, P.FLOW_DEPLOY, P.FLOW_APPROVE):
        assert P.has_permission(admin, bit)
        assert not P.has_permission(member, bit)


# ── 서비스 층 승인 규칙 ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_without_the_option_submit_deploys_immediately(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)

    outcome = services.submit_deploy(tenant.id, flow.id, 1)

    assert outcome.deployed and outcome.request is None
    assert services.get_flow(tenant.id, flow.id).deployed_version.number == 1


@pytest.mark.django_db
def test_with_the_option_submit_creates_a_request_and_deploys_nothing(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    require_approval(tenant)

    outcome = services.submit_deploy(tenant.id, flow.id, 1)

    assert not outcome.deployed and outcome.request.status == DeployRequest.PENDING
    assert services.get_flow(tenant.id, flow.id).deployed_version is None
    with pytest.raises(services.FlowServiceError) as exc:  # 우회로(deploy_version 직접)도 막힌다
        services.deploy_version(tenant.id, flow.id, 1)
    assert exc.value.code == "approval_required"


@pytest.mark.django_db
def test_another_admin_approves_and_the_version_goes_live(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    requester, approver = make_membership(tenant, "admin"), make_membership(tenant, "admin")
    require_approval(tenant)
    req = services.submit_deploy(tenant.id, flow.id, 1, requested_by_id=requester.user_id).request

    done = services.approve_deploy_request(tenant.id, req.id, decided_by_id=approver.user_id, note="좋아요")

    assert done.status == DeployRequest.APPROVED and done.decided_by_id == approver.user_id and done.decided_at
    assert services.get_flow(tenant.id, flow.id).deployed_version.number == 1


@pytest.mark.django_db
def test_requester_cannot_approve_their_own_request(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    admin = make_membership(tenant, "admin")
    require_approval(tenant)
    req = services.submit_deploy(tenant.id, flow.id, 1, requested_by_id=admin.user_id).request

    with pytest.raises(services.FlowServiceError) as exc:
        services.approve_deploy_request(tenant.id, req.id, decided_by_id=admin.user_id)
    assert exc.value.code == "self_approval"
    # 키 인증(break-glass, 사용자 없음)은 승인할 수 있다 — 관리자가 한 명뿐인 테넌트가 막히지 않게
    assert services.approve_deploy_request(tenant.id, req.id, decided_by_id=None).status == DeployRequest.APPROVED


@pytest.mark.django_db
def test_reject_keeps_the_old_version_and_a_decided_request_cannot_be_decided_again(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    require_approval(tenant)
    req = services.submit_deploy(tenant.id, flow.id, 1).request

    rejected = services.reject_deploy_request(tenant.id, req.id, note="아직 이르다")

    assert rejected.status == DeployRequest.REJECTED and rejected.decision_note == "아직 이르다"
    assert services.get_flow(tenant.id, flow.id).deployed_version is None
    for decide in (services.approve_deploy_request, services.reject_deploy_request):
        with pytest.raises(services.FlowServiceError) as exc:
            decide(tenant.id, req.id)
        assert exc.value.code == "request_not_pending"


@pytest.mark.django_db
def test_a_new_request_supersedes_the_pending_one_of_the_same_flow(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    services.patch_draft(tenant.id, flow.id, [services.UpsertNode(op="upsert_node", id="answer", name="답변2")])
    services.save_version(tenant.id, flow.id)
    require_approval(tenant)

    first = services.submit_deploy(tenant.id, flow.id, 1).request
    second = services.submit_deploy(tenant.id, flow.id, 2).request

    first.refresh_from_db()
    assert first.status == DeployRequest.SUPERSEDED and second.status == DeployRequest.PENDING
    assert [r.id for r in services.list_deploy_requests(tenant.id, status=DeployRequest.PENDING)] == [second.id]


@pytest.mark.django_db
def test_requests_are_tenant_scoped(tenant_with_key):
    from tests.test_flow_services import other_tenant

    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    require_approval(tenant)
    req = services.submit_deploy(tenant.id, flow.id, 1).request
    stranger = other_tenant()

    assert services.list_deploy_requests(stranger.id) == []
    with pytest.raises(services.FlowServiceError) as exc:
        services.approve_deploy_request(stranger.id, req.id)
    assert exc.value.code == "request_not_found"


@pytest.mark.django_db
def test_rollback_is_exempt_from_approval(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = versioned(tenant)
    services.patch_draft(tenant.id, flow.id, [services.UpsertNode(op="upsert_node", id="answer", name="답변2")])
    services.save_version(tenant.id, flow.id)
    services.deploy_version(tenant.id, flow.id, 1)
    services.deploy_version(tenant.id, flow.id, 2)
    require_approval(tenant)

    assert services.rollback(tenant.id, flow.id).deployed_version.number == 1  # 긴급 복구는 승인을 기다리지 않는다


@pytest.mark.django_db
def test_rename_flow_checks_name(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    make_flow(tenant, name="다른")

    assert services.rename_flow(tenant.id, flow.id, " 새 이름 ").name == "새 이름"
    with pytest.raises(services.FlowServiceError) as exc:
        services.rename_flow(tenant.id, flow.id, "다른")
    assert exc.value.code == "duplicate_name"


# ── MCP deploy_version이 같은 규칙을 탄다 ────────────────────────────────────


@pytest.mark.django_db
def test_mcp_deploy_version_follows_the_approval_option(signer: Signer) -> None:  # noqa: F811
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)
    flow = versioned(tenant)
    require_approval(tenant)
    token = mcp_token(signer, scope="flow.read flow.write flow.deploy")

    result = ok(call(token, "deploy_version", {"flow_id": str(flow.id), "version": 1}))

    assert result["deployed"] is False and result["approval_required"] is True and result["request_id"]
    assert services.get_flow(tenant.id, flow.id).deployed_version is None
    assert DeployRequest.objects.get(pk=result["request_id"]).status == DeployRequest.PENDING
