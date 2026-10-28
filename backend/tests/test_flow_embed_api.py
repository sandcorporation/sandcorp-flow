"""흐름 임베드 켬/끔·기본 흐름 바꾸기 API(#247, ADR-0034) — ``POST /api/tenant/flows/{id}/embed/``·``/default/``.

권한은 ``FLOW_DEPLOY``(Admin). 열림 판정은 ``Flow.embeddable``과 같은 조건이다. DB·인증·서비스는 실제 객체다.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.flows import services
from apps.flows.models import Flow
from tests.test_flow_api import BASE, h, jpost
from tests.test_flow_services import make_flow


def deployed_flow(tenant, name="상담"):
    flow = make_flow(tenant, name=name)
    services.save_version(tenant.id, flow.id)
    services.submit_deploy(tenant.id, flow.id, 1)
    return services.get_flow(tenant.id, flow.id)


def undeployed_flow(tenant, name="초안만"):
    return make_flow(tenant, name=name)


def reload(flow):
    return Flow.objects.get(pk=flow.pk)


# ── 응답의 임베드 칸 ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_and_detail_carry_embed_fields(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    tenant.slug = "embed-corp"
    tenant.save(update_fields=["slug"])
    flow = deployed_flow(tenant)
    Flow.objects.filter(pk=flow.pk).update(embed_enabled=True)

    row = next(r for r in client.get(BASE, **h(tenant_admin_token)).json() if r["id"] == str(flow.id))
    detail = client.get(f"{BASE}{flow.id}/", **h(tenant_admin_token)).json()["flow"]
    for body in (row, detail):
        assert body["embed_key"] == flow.embed_key
        assert body["embed_enabled"] is True
        assert body["is_public"] is False
        assert body["embed_path"] == f"/chatbot/{tenant.slug}/{flow.embed_key}/"


# ── 임베드 켬/끔 ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_admin_turns_embed_on_and_off(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = deployed_flow(tenant)
    url = f"{BASE}{flow.id}/embed/"

    on = jpost(client, url, {"enabled": True}, tenant_admin_token)
    assert on.status_code == 200 and on.json()["embed_enabled"] is True
    assert Flow.open_by_key(tenant.id, flow.embed_key) is not None

    off = jpost(client, url, {"enabled": False}, tenant_admin_token)
    assert off.status_code == 200 and off.json()["embed_enabled"] is False
    assert Flow.open_by_key(tenant.id, flow.embed_key) is None


@pytest.mark.django_db
def test_member_cannot_toggle_embed_or_change_default(client, tenant_with_key, tenant_member_token):
    tenant, _ = tenant_with_key
    flow = deployed_flow(tenant)
    assert jpost(client, f"{BASE}{flow.id}/embed/", {"enabled": True}, tenant_member_token).status_code == 403
    assert jpost(client, f"{BASE}{flow.id}/default/", {}, tenant_member_token).status_code == 403
    flow = reload(flow)
    assert flow.embed_enabled is False and flow.is_public is False


@pytest.mark.django_db
def test_undeployed_flow_cannot_be_turned_on_and_reason_is_given(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = undeployed_flow(tenant)
    res = jpost(client, f"{BASE}{flow.id}/embed/", {"enabled": True}, tenant_admin_token)
    assert res.status_code == 409
    assert res.json()["code"] == "not_embeddable"
    assert "배포" in res.json()["detail"]
    assert reload(flow).embed_enabled is False


@pytest.mark.django_db
def test_archived_or_foreign_flow_is_404(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = deployed_flow(tenant)
    other = deployed_flow(tenant, name="다른")
    services.archive_flow(tenant.id, other.id)
    assert jpost(client, f"{BASE}{other.id}/embed/", {"enabled": True}, tenant_admin_token).status_code == 404
    assert jpost(client, f"{BASE}00000000-0000-0000-0000-000000000000/default/", {}, tenant_admin_token).status_code == 404
    assert reload(flow).embed_enabled is False


# ── 기본 흐름 바꾸기 ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_default_flow_switch_moves_the_flag_and_keeps_one_per_tenant(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    old = deployed_flow(tenant, "옛 기본")
    new = deployed_flow(tenant, "새 기본")
    Flow.objects.filter(pk=old.pk).update(is_public=True)

    res = jpost(client, f"{BASE}{new.id}/default/", {}, tenant_admin_token)
    assert res.status_code == 200
    assert res.json()["is_public"] is True

    assert list(Flow.objects.filter(tenant=tenant, is_public=True).values_list("pk", flat=True)) == [new.pk]
    assert Flow.default_for(tenant.id).pk == new.pk
    # 옛 기본 흐름은 자기 키 주소로는 임베드를 켜야 열린다 — 바뀌었다고 켜지지 않는다.
    assert Flow.open_by_key(tenant.id, old.embed_key) is None


@pytest.mark.django_db
def test_default_flow_switch_to_current_default_is_noop_ok(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    cur = deployed_flow(tenant)
    Flow.objects.filter(pk=cur.pk).update(is_public=True)
    assert jpost(client, f"{BASE}{cur.id}/default/", {}, tenant_admin_token).status_code == 200
    assert reload(cur).is_public is True


@pytest.mark.django_db
def test_default_flow_switch_refuses_undeployed_and_keeps_old_default(client, tenant_with_key, tenant_admin_token):
    """원자성: 거절되면 옛 기본 흐름이 풀리지 않는다(기본 흐름 없는 순간이 남지 않는다)."""
    tenant, _ = tenant_with_key
    old = deployed_flow(tenant, "옛 기본")
    Flow.objects.filter(pk=old.pk).update(is_public=True)
    draft_only = undeployed_flow(tenant)

    res = jpost(client, f"{BASE}{draft_only.id}/default/", {}, tenant_admin_token)
    assert res.status_code == 409 and res.json()["code"] == "not_embeddable"
    assert reload(old).is_public is True
    assert reload(draft_only).is_public is False


@pytest.mark.django_db(transaction=True)
def test_default_switch_rolls_back_old_flag_when_new_flag_fails(tenant_with_key, monkeypatch):
    """새 흐름을 True로 바꾸는 저장이 깨지면 같은 트랜잭션이라 옛 기본 흐름도 그대로다."""
    tenant, _ = tenant_with_key
    old = deployed_flow(tenant, "옛 기본")
    new = deployed_flow(tenant, "새 기본")
    Flow.objects.filter(pk=old.pk).update(is_public=True)

    real_save = Flow.save

    def boom(self, *a, **kw):
        if self.pk == new.pk and self.is_public:
            raise IntegrityError("강제 실패")
        return real_save(self, *a, **kw)

    monkeypatch.setattr(Flow, "save", boom)
    with pytest.raises(IntegrityError):
        services.set_default_flow(tenant.id, new.id)
    monkeypatch.undo()
    assert reload(old).is_public is True and reload(new).is_public is False


@pytest.mark.django_db
def test_old_default_is_archivable_after_switch(tenant_with_key):
    tenant, _ = tenant_with_key
    old = deployed_flow(tenant, "옛 기본")
    new = deployed_flow(tenant, "새 기본")
    Flow.objects.filter(pk=old.pk).update(is_public=True)
    services.set_default_flow(tenant.id, new.id)
    services.archive_flow(tenant.id, old.id)
    assert reload(old).archived_at is not None
    with pytest.raises(services.FlowInUse):
        services.archive_flow(tenant.id, new.id)


@pytest.mark.django_db
def test_embedding_a_default_flow_is_allowed_and_default_stays_open_when_off(tenant_with_key):
    tenant, _ = tenant_with_key
    cur = deployed_flow(tenant)
    Flow.objects.filter(pk=cur.pk).update(is_public=True)
    services.set_embed_enabled(tenant.id, cur.id, False)
    assert Flow.open_by_key(tenant.id, cur.embed_key) is not None  # 기본 흐름은 늘 열려 있다
