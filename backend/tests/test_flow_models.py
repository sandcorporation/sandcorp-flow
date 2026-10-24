"""Flow·Draft·Flow Version 모델(#22, ADR-0027): 버전 불변, 해시, 세션 버전 고정, 테넌트 격리."""

import copy
import json

import pytest
from django.db import IntegrityError

from apps.chat.models import ChatSession
from apps.flows.models import Flow, FlowDraft, FlowVersion, ImmutableFlowVersionError
from apps.flows.schema import FlowValidationError, flow_hash
from apps.tenants.models import Tenant, User

GRAPH = {
    "nodes": [
        {"id": "s", "kind": "start", "code": "N-01", "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "a", "kind": "ai_answer", "code": "N-02", "position": {"x": 200, "y": 0}, "config": {}},
    ],
    "edges": [{"from": "s", "to": "a"}],
}


def make_tenant(name):
    return Tenant.objects.create_with_key(name=name, raw_key=f"key-{name}")


def deploy(flow, graph=GRAPH, user=None):
    version = FlowVersion.objects.create(flow=flow, graph=graph, created_by=user)
    flow.deployed_version = version
    flow.save()
    return version


@pytest.fixture
def tenant(db):
    return make_tenant("acme")


@pytest.fixture
def flow(tenant):
    f = Flow.objects.create(tenant=tenant, name="홈 상담", is_public=True)
    FlowDraft.objects.create(flow=f, graph=GRAPH)
    return f


def test_version_is_validated_hashed_and_numbered_on_create(flow):
    user = User.objects.create(iss="https://auth.sandcorp.co.kr", sub="m_1", acct=User.Acct.MEMBER)
    v1 = FlowVersion.objects.create(flow=flow, graph=GRAPH, created_by=user)
    v2 = FlowVersion.objects.create(flow=flow, graph=GRAPH)
    assert (v1.number, v2.number) == (1, 2)
    assert v1.hash == flow_hash(GRAPH) == v2.hash
    assert v1.created_by == user


def test_invalid_graph_cannot_become_a_version(flow):
    broken = copy.deepcopy(GRAPH)
    broken["edges"] = []
    with pytest.raises(FlowValidationError):
        FlowVersion.objects.create(flow=flow, graph=broken)
    assert not FlowVersion.objects.exists()


def test_draft_may_hold_an_invalid_work_in_progress(flow):
    flow.draft.graph = {"nodes": []}
    flow.draft.save()
    flow.draft.refresh_from_db()
    assert flow.draft.graph == {"nodes": []}


def test_saved_version_cannot_be_modified(flow):
    v = FlowVersion.objects.create(flow=flow, graph=GRAPH)
    v.graph = {"nodes": []}
    with pytest.raises(ImmutableFlowVersionError):
        v.save()
    v = FlowVersion.objects.get(pk=v.pk)
    v.hash = "0" * 64
    with pytest.raises(ImmutableFlowVersionError):
        v.save()
    with pytest.raises(ImmutableFlowVersionError):
        FlowVersion.objects.filter(pk=v.pk).update(hash="0" * 64)
    v.refresh_from_db()
    assert v.graph == GRAPH and v.hash == flow_hash(GRAPH)


def test_hash_is_stable_across_db_roundtrip(flow):
    reordered = json.loads(json.dumps(GRAPH, sort_keys=True, indent=2))
    a = FlowVersion.objects.create(flow=flow, graph=GRAPH)
    b = FlowVersion.objects.create(flow=flow, graph=reordered)
    a.refresh_from_db()
    assert a.hash == b.hash == flow_hash(a.graph)


def test_deployed_version_must_belong_to_the_flow(tenant, flow):
    other = Flow.objects.create(tenant=tenant, name="다른 흐름")
    foreign = FlowVersion.objects.create(flow=other, graph=GRAPH)
    flow.deployed_version = foreign
    with pytest.raises(ValueError):
        flow.save()


def test_only_one_public_flow_per_tenant(tenant, flow):
    with pytest.raises(IntegrityError):
        Flow.objects.create(tenant=tenant, name="둘째 공개", is_public=True)


def test_session_is_pinned_to_the_version_deployed_at_start(tenant, flow):
    v1 = deploy(flow)
    early = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-early")
    v2 = deploy(flow)

    late = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-late")
    early.refresh_from_db()
    early.is_hitl = True
    early.save()  # 이후 저장도 고정을 바꾸지 않는다

    assert ChatSession.objects.get(pk=early.pk).flow_version == v1
    assert late.flow_version == v2


@pytest.mark.django_db(transaction=True)
async def test_session_pinned_through_async_get_or_create():
    """위젯 경로(chat/api.py)는 aget_or_create로 세션을 만든다 — 그 경로에서도 고정된다."""
    from asgiref.sync import sync_to_async

    def setup():
        t = make_tenant("async-co")
        f = Flow.objects.create(tenant=t, name="흐름", is_public=True)
        return t, deploy(f)

    tenant, version = await sync_to_async(setup)()
    session, _ = await ChatSession.objects.aget_or_create(tenant_id=tenant.id, visitor_id="v1")
    assert session.flow_version_id == version.id


def test_session_without_public_flow_has_no_version(tenant):
    Flow.objects.create(tenant=tenant, name="비공개")
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v")
    assert session.flow_version is None


def test_public_flow_without_deployment_gives_no_version(tenant, flow):
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v")
    assert session.flow_version is None


def test_tenant_isolation_in_queries(tenant, flow):
    deploy(flow)
    other = make_tenant("other")
    other_flow = Flow.objects.create(tenant=other, name="남의 흐름", is_public=True)
    other_version = deploy(other_flow)

    assert list(Flow.objects.for_tenant(tenant.id)) == [flow]
    assert other_version not in FlowVersion.objects.for_tenant(tenant.id)
    assert Flow.public_version_for(tenant.id) == flow.deployed_version
    assert Flow.public_version_for(other.id) == other_version


def test_session_cannot_be_pinned_to_another_tenants_version(tenant, flow):
    other = make_tenant("other")
    other_version = deploy(Flow.objects.create(tenant=other, name="남의 흐름"))
    with pytest.raises(ValueError):
        ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v", flow_version=other_version)


def test_version_in_use_by_session_cannot_be_deleted(tenant, flow):
    from django.db.models import ProtectedError

    v = deploy(flow)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v")
    flow.deployed_version = None
    flow.save()
    with pytest.raises(ProtectedError):
        v.delete()
