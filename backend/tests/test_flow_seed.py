"""기본 흐름 시드(#23): 흐름 없는 테넌트에 기본 흐름을 만들어 배포 중·공개로 둔다.

경로: 데이터 마이그레이션(이미 있는 테넌트), 온보딩(가입자가 조직 생성), Operator 테넌트 생성. 멱등.
"""
import importlib
import secrets

import pytest
from django.apps import apps as django_apps
from django.test import Client

SEED_MIGRATION = "apps.flows.migrations.0002_seed_default_flows"


def _tenant(name="Seed Corp"):
    from apps.tenants.models import Tenant

    return Tenant.objects.create_with_key(name=name, raw_key=secrets.token_urlsafe(32))


def _assert_default_flow(tenant):
    from apps.flows.defaults import DEFAULT_FLOW, DEFAULT_FLOW_NAME
    from apps.flows.models import Flow, FlowVersion
    from apps.flows.schema import flow_hash

    flows = list(Flow.objects.for_tenant(tenant.id))
    assert len(flows) == 1
    flow = flows[0]
    assert (flow.name, flow.is_public) == (DEFAULT_FLOW_NAME, True)
    assert flow.deployed_version is not None
    assert flow.deployed_version.hash == flow_hash(DEFAULT_FLOW)
    assert FlowVersion.objects.filter(flow=flow).count() == 1
    assert flow.draft.graph == DEFAULT_FLOW
    assert Flow.public_version_for(tenant.id) == flow.deployed_version


@pytest.mark.django_db
def test_ensure_default_flow_creates_public_deployed_flow_once():
    from apps.flows.defaults import ensure_default_flow

    tenant = _tenant()
    first = ensure_default_flow(tenant.id)
    second = ensure_default_flow(tenant.id)

    assert first.id == second.id
    _assert_default_flow(tenant)


@pytest.mark.django_db
def test_ensure_default_flow_leaves_tenant_with_a_flow_alone():
    from apps.flows.defaults import ensure_default_flow
    from apps.flows.models import Flow

    tenant = _tenant()
    mine = Flow.objects.create(tenant=tenant, name="내 흐름")

    assert ensure_default_flow(tenant.id).id == mine.id
    assert list(Flow.objects.for_tenant(tenant.id)) == [mine]


@pytest.mark.django_db
def test_onboarding_new_tenant_gets_default_flow():
    from apps.chat.sse import get_redis_client
    from apps.tenants.members import issue_member_ticket, member_user
    from apps.tenants.models import Tenant

    r = get_redis_client()
    for key in r.scan_iter("ratelimit:onboarding:*"):
        r.delete(key)

    user = member_user("https://auth.sandcorp.co.kr", "m_SEED")
    c = Client()
    c.cookies["member_ticket"] = issue_member_ticket(user)
    response = c.post("/api/auth/member/tenants", {"name": "시드 조직", "slug": "seed-org"},
                      content_type="application/json")

    assert response.status_code in (200, 201), response.content
    _assert_default_flow(Tenant.objects.get(slug="seed-org"))


@pytest.mark.django_db
def test_operator_created_tenant_gets_default_flow(client, operator_token):
    from apps.tenants.models import Tenant

    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
    response = client.post("/api/operator/tenants/", {"name": "Operator Seed"}, content_type="application/json")

    assert response.status_code == 201
    _assert_default_flow(Tenant.objects.get(id=response.json()["id"]))


@pytest.mark.django_db
def test_data_migration_seeds_existing_tenants_and_is_idempotent():
    """운영에 이미 있는 테넌트(흐름 없음)는 기본 흐름을 받고, 흐름이 있는 테넌트는 그대로다. 두 번 돌려도 하나."""
    from apps.flows.models import Flow

    migration = importlib.import_module(SEED_MIGRATION)
    bare_a, bare_b, custom = _tenant("A"), _tenant("B"), _tenant("C")
    mine = Flow.objects.create(tenant=custom, name="내 흐름")

    migration.seed_default_flows(django_apps, None)
    migration.seed_default_flows(django_apps, None)

    _assert_default_flow(bare_a)
    _assert_default_flow(bare_b)
    assert list(Flow.objects.for_tenant(custom.id)) == [mine]


@pytest.mark.django_db
def test_data_migration_reverse_removes_only_seeded_flows():
    """되돌리면 시드한 기본 흐름만 지운다 — 거기에 고정된 대화는 고정을 풀고(None = 기본 흐름), 내 흐름은 남긴다."""
    from apps.chat.models import ChatSession
    from apps.flows.models import Flow

    migration = importlib.import_module(SEED_MIGRATION)
    bare, custom = _tenant("A"), _tenant("C")
    mine = Flow.objects.create(tenant=custom, name="내 흐름")
    migration.seed_default_flows(django_apps, None)
    pinned = ChatSession.objects.create(tenant_id=bare.id, visitor_id="v-pinned")
    assert pinned.flow_version_id is not None

    migration.unseed_default_flows(django_apps, None)

    assert not Flow.objects.for_tenant(bare.id).exists()
    assert list(Flow.objects.for_tenant(custom.id)) == [mine]
    pinned.refresh_from_db()
    assert pinned.flow_version_id is None
