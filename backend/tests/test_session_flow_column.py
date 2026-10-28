"""세션 목록·방문자 세션·사람 넘김·MCP의 흐름 칸과 흐름 필터 (#249, 우산 #245 E4).

`ChatSession.flow` FK를 쓴다. 옛 세션(flow null)은 flow_id·flow_name이 null이다.
"""
from __future__ import annotations

import json
import uuid

import pytest

from apps.chat.models import ChatSession
from apps.escalation.models import Escalation
from apps.flows import services as flow_services
from mcp_helpers import call, clock, flow_validator, make_tenant, member_of, mcp_token, signer, tool_text  # noqa: F401
from resource_token_fakes import Signer


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _two_flows(tenant):
    a = flow_services.create_flow(tenant.id, "봄 이벤트")
    b = flow_services.create_flow(tenant.id, "환불 안내")
    sa = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="va", flow=a)
    sb = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="vb", flow=b)
    so = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="vold")
    return a, b, sa, sb, so


@pytest.mark.django_db
def test_sessions_list_has_flow_columns(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    a, b, *_ = _two_flows(tenant)

    rows = client.get("/api/tenant/sessions/", **_auth(tenant_admin_token)).json()

    by_vid = {r["visitor_id"]: r for r in rows}
    assert by_vid["va"]["flow_id"] == str(a.id) and by_vid["va"]["flow_name"] == "봄 이벤트"
    assert by_vid["vb"]["flow_name"] == "환불 안내"
    assert by_vid["vold"]["flow_id"] is None and by_vid["vold"]["flow_name"] is None


@pytest.mark.django_db
def test_sessions_list_filters_by_flow(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    a, *_ = _two_flows(tenant)

    rows = client.get(f"/api/tenant/sessions/?flow_id={a.id}", **_auth(tenant_admin_token)).json()

    assert [r["visitor_id"] for r in rows] == ["va"]


@pytest.mark.django_db
def test_sessions_list_foreign_or_bad_flow_id_is_empty(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    _two_flows(tenant)
    other = make_tenant("other-corp")
    foreign = flow_services.create_flow(other.id, "남의 흐름")
    ChatSession.objects.create(tenant_id=other.id, visitor_id="vx", flow=foreign)

    for flow_id in (str(foreign.id), str(uuid.uuid4()), "not-a-uuid"):
        resp = client.get(f"/api/tenant/sessions/?flow_id={flow_id}", **_auth(tenant_admin_token))
        assert resp.status_code == 200 and resp.json() == []


@pytest.mark.django_db
def test_sessions_list_query_count_does_not_grow_with_flows(client, tenant_admin_token, tenant_with_key, django_assert_max_num_queries):
    tenant, _ = tenant_with_key
    for i in range(6):
        f = flow_services.create_flow(tenant.id, f"흐름{i}")
        ChatSession.objects.create(tenant_id=tenant.id, visitor_id=f"v{i}", flow=f)

    with django_assert_max_num_queries(12):
        resp = client.get("/api/tenant/sessions/", **_auth(tenant_admin_token))
    assert resp.status_code == 200 and len(resp.json()) == 6


@pytest.mark.django_db
def test_visitor_sessions_have_flow_and_filter(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    a = flow_services.create_flow(tenant.id, "봄 이벤트")
    b = flow_services.create_flow(tenant.id, "환불 안내")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1", flow=a)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1", flow=b)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")

    rows = client.get("/api/tenant/visitors/v1/sessions/", **_auth(tenant_admin_token)).json()
    assert sorted(r["flow_name"] or "" for r in rows) == ["", "봄 이벤트", "환불 안내"]

    only_b = client.get(f"/api/tenant/visitors/v1/sessions/?flow_id={b.id}", **_auth(tenant_admin_token)).json()
    assert [r["flow_name"] for r in only_b] == ["환불 안내"]
    bad = client.get("/api/tenant/visitors/v1/sessions/?flow_id=zzz", **_auth(tenant_admin_token))
    assert bad.status_code == 200 and bad.json() == []


@pytest.mark.django_db
def test_escalations_have_flow_and_filter_single_queue(client, tenant_admin_token, tenant_with_key):
    tenant, _ = tenant_with_key
    a, b, sa, sb, so = _two_flows(tenant)
    for s in (sa, sb, so):
        Escalation.objects.create(session=s, trigger_type=Escalation.TRIGGER_AI, status=Escalation.STATUS_PENDING)

    allrows = client.get("/api/tenant/escalations/", **_auth(tenant_admin_token)).json()
    assert {r["session_id"]: r["flow_name"] for r in allrows} == {str(sa.id): "봄 이벤트", str(sb.id): "환불 안내", str(so.id): None}

    only_a = client.get(f"/api/tenant/escalations/?flow_id={a.id}", **_auth(tenant_admin_token)).json()
    assert [r["session_id"] for r in only_a] == [str(sa.id)]
    foreign = client.get(f"/api/tenant/escalations/?flow_id={uuid.uuid4()}", **_auth(tenant_admin_token))
    assert foreign.status_code == 200 and foreign.json() == []


@pytest.mark.django_db
def test_mcp_list_sessions_flow_columns_and_filter(signer: Signer) -> None:
    tenant = make_tenant("acme")
    member_of(tenant)
    a = flow_services.create_flow(tenant.id, "봄 이벤트")
    b = flow_services.create_flow(tenant.id, "환불 안내")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="va", flow=a)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="vb", flow=b)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="vold")
    token = mcp_token(signer, scope="flow.read")

    def rows(args: dict) -> list[dict]:
        body = call(token, "list_sessions", args)
        assert not body["result"].get("isError"), tool_text(body)
        return json.loads(tool_text(body))["sessions"]

    by_vid = {r["visitor_id"]: r for r in rows({})}
    assert by_vid["va"]["flow_id"] == str(a.id) and by_vid["va"]["flow_name"] == "봄 이벤트"
    assert by_vid["vold"]["flow_id"] is None and by_vid["vold"]["flow_name"] is None
    assert [r["visitor_id"] for r in rows({"flow_id": str(b.id)})] == ["vb"]
    assert rows({"flow_id": str(uuid.uuid4())}) == []


@pytest.mark.django_db
def test_mcp_get_session_reports_flow(signer: Signer) -> None:
    tenant = make_tenant("acme")
    member_of(tenant)
    a = flow_services.create_flow(tenant.id, "봄 이벤트")
    s = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="va", flow=a)
    old = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="vold")
    token = mcp_token(signer, scope="flow.read")

    got = json.loads(tool_text(call(token, "get_session", {"session_id": str(s.id)})))
    assert got["flow"] == {"id": str(a.id), "name": "봄 이벤트"}
    got_old = json.loads(tool_text(call(token, "get_session", {"session_id": str(old.id)})))
    assert got_old["flow"] is None
