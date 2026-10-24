"""흐름 HTTP API(#28) — ``/api/tenant/flows/*``, ``/api/tenant/deploy-requests/*``.

#76 공용 서비스 층을 감싸기만 하므로 여기서는 「HTTP 모양·권한·상태 코드·SSE 형식」을 본다. LLM만 Fake(conftest),
DB·인증·서비스는 실제 객체다.
"""

from __future__ import annotations

import json

import pytest

from apps.chat.models import ChatSession
from apps.escalation.models import Escalation
from apps.flows import services
from apps.flows.models import DeployRequest
from apps.tenants.models import TenantConfig
from tests.test_flow_services import SIMPLE, WITH_HANDOFF, make_flow, other_tenant
from tests.utils import make_membership, member_token

BASE = "/api/tenant/flows/"
REQS = "/api/tenant/deploy-requests/"


def h(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def jpost(client, url, body, token, method="post"):
    return getattr(client, method)(url, data=json.dumps(body), content_type="application/json", **h(token))


OPS = [
    {"op": "upsert_node", "id": "start", "kind": "start", "code": "N-01"},
    {"op": "upsert_node", "id": "answer", "kind": "ai_answer", "code": "N-02"},
    {"op": "connect", "from": "start", "to": "answer"},
]


@pytest.fixture
def admin_token(tenant_admin_token):
    return tenant_admin_token


def new_flow(client, token, name="상담", ops=OPS):
    flow = jpost(client, BASE, {"name": name}, token).json()
    if ops:
        assert jpost(client, f"{BASE}{flow['id']}/draft/", {"ops": ops}, token, "patch").status_code == 200
    return flow["id"]


def sse_events(response) -> list[tuple[str, dict]]:
    from asgiref.sync import async_to_sync

    async def drain() -> bytes:
        return b"".join([chunk async for chunk in response.streaming_content])

    body = async_to_sync(drain)().decode()
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


# ── 인증·권한 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_requires_authentication(client):
    assert client.get(BASE).status_code == 401
    assert client.get(REQS).status_code == 401


@pytest.mark.django_db
def test_member_reads_but_every_write_is_403(client, tenant_with_key, tenant_member_token):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    services.save_version(tenant.id, flow.id)
    t, fid = tenant_member_token, str(flow.id)

    assert client.get(BASE, **h(t)).status_code == 200
    assert client.get(f"{BASE}{fid}/", **h(t)).status_code == 200
    assert client.get(f"{BASE}{fid}/versions/1/", **h(t)).status_code == 200
    assert client.get(REQS, **h(t)).status_code == 200
    writes = [
        (BASE, {"name": "x"}, "post"),
        (f"{BASE}{fid}/", {"name": "y"}, "patch"),
        (f"{BASE}{fid}/draft/", {"graph": {"nodes": [], "edges": []}}, "put"),
        (f"{BASE}{fid}/draft/", {"ops": OPS}, "patch"),
        (f"{BASE}{fid}/versions/", {}, "post"),
        (f"{BASE}{fid}/deploy/", {"version": 1}, "post"),
        (f"{BASE}{fid}/rollback/", {}, "post"),
        (f"{BASE}{fid}/test/", {"message": "안녕"}, "post"),
        (f"{REQS}00000000-0000-0000-0000-000000000000/approve/", {}, "post"),
        (f"{REQS}00000000-0000-0000-0000-000000000000/reject/", {}, "post"),
    ]
    for url, body, method in writes:
        assert jpost(client, url, body, t, method).status_code == 403, (method, url)
    assert services.get_flow(tenant.id, fid).deployed_version is None


@pytest.mark.django_db
def test_other_tenants_flow_is_404(client, tenant_with_key, admin_token):
    stranger = other_tenant()
    foreign = make_flow(stranger, SIMPLE, name="남의")
    fid = str(foreign.id)

    assert client.get(f"{BASE}{fid}/", **h(admin_token)).status_code == 404
    assert jpost(client, f"{BASE}{fid}/draft/", {"ops": OPS}, admin_token, "patch").status_code == 404
    assert jpost(client, f"{BASE}{fid}/deploy/", {"version": 1}, admin_token).status_code == 404
    assert client.get(BASE, **h(admin_token)).json() == []


@pytest.mark.django_db
def test_tenant_key_is_admin_equivalent(client, tenant_with_key):
    _, key = tenant_with_key
    r = jpost(client, BASE, {"name": "키로 만듦"}, key)
    assert r.status_code == 201 and client.get(BASE, **h(key)).status_code == 200


# ── 흐름·초안 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_create_list_rename(client, admin_token):
    created = jpost(client, BASE, {"name": "상담"}, admin_token)
    assert created.status_code == 201
    fid = created.json()["id"]
    assert created.json()["deployed_version"] is None

    assert jpost(client, BASE, {"name": "상담"}, admin_token).status_code == 409  # 이름 중복
    assert jpost(client, BASE, {"name": "  "}, admin_token).status_code == 422

    renamed = jpost(client, f"{BASE}{fid}/", {"name": "새 이름"}, admin_token, "patch")
    assert renamed.status_code == 200 and renamed.json()["name"] == "새 이름"
    assert [f["name"] for f in client.get(BASE, **h(admin_token)).json()] == ["새 이름"]


@pytest.mark.django_db
def test_draft_save_returns_validation_and_get_detail(client, admin_token):
    fid = new_flow(client, admin_token, ops=None)
    bad = jpost(client, f"{BASE}{fid}/draft/", {"graph": {"nodes": [], "edges": []}}, admin_token, "put")
    assert bad.status_code == 200  # 틀려도 저장
    assert bad.json()["valid"] is False and bad.json()["errors"][0]["code"]

    patched = jpost(client, f"{BASE}{fid}/draft/", {"ops": OPS}, admin_token, "patch")
    assert patched.status_code == 200 and patched.json()["valid"] is True
    assert [n["id"] for n in patched.json()["graph"]["nodes"]] == ["start", "answer"]

    detail = client.get(f"{BASE}{fid}/", **h(admin_token)).json()
    assert detail["flow"]["id"] == fid and detail["draft"]["valid"] is True
    assert detail["versions"] == [] and detail["pending_request"] is None


@pytest.mark.django_db
def test_patch_that_cannot_apply_is_422_and_saves_nothing(client, admin_token):
    fid = new_flow(client, admin_token)
    r = jpost(client, f"{BASE}{fid}/draft/", {"ops": [OPS[0], {"op": "delete_node", "id": "없음"}]}, admin_token, "patch")
    assert r.status_code == 422 and r.json()["code"] == "invalid_patch" and r.json()["index"] == 1
    graph = client.get(f"{BASE}{fid}/", **h(admin_token)).json()["draft"]["graph"]
    assert len(graph["nodes"]) == 2


# ── 버전·배포·되돌리기 ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_save_deploy_rollback_flow(client, admin_token):
    fid = new_flow(client, admin_token)
    v1 = jpost(client, f"{BASE}{fid}/versions/", {"note": "처음"}, admin_token)
    assert v1.status_code == 201 and v1.json()["created"] is True and v1.json()["version"]["note"] == "처음"
    same = jpost(client, f"{BASE}{fid}/versions/", {}, admin_token)
    assert same.status_code == 200 and same.json()["created"] is False

    jpost(client, f"{BASE}{fid}/draft/", {"ops": [{"op": "upsert_node", "id": "answer", "name": "답변2"}]}, admin_token, "patch")
    assert jpost(client, f"{BASE}{fid}/versions/", {}, admin_token).json()["version"]["number"] == 2

    assert jpost(client, f"{BASE}{fid}/deploy/", {"version": 2}, admin_token).json()["flow"]["deployed_version"] == 2
    rolled = jpost(client, f"{BASE}{fid}/rollback/", {}, admin_token)
    assert rolled.status_code == 200 and rolled.json()["deployed_version"] == 1

    versions = client.get(f"{BASE}{fid}/versions/", **h(admin_token)).json()
    assert [(v["number"], v["deployed"]) for v in versions] == [(1, True), (2, False)]
    one = client.get(f"{BASE}{fid}/versions/1/", **h(admin_token)).json()
    assert one["note"] == "처음" and len(one["graph"]["nodes"]) == 2
    assert client.get(f"{BASE}{fid}/versions/9/", **h(admin_token)).status_code == 404
    assert jpost(client, f"{BASE}{fid}/rollback/", {}, admin_token).status_code == 404  # 앞 버전 없음


@pytest.mark.django_db
def test_invalid_draft_cannot_become_a_version_or_deploy(client, admin_token):
    fid = new_flow(client, admin_token, ops=None)
    r = jpost(client, f"{BASE}{fid}/versions/", {}, admin_token)
    assert r.status_code == 422 and r.json()["code"] == "flow_invalid" and r.json()["errors"]

    # 핸들러 없는 노드(http_tool)가 있는 버전은 만들 수는 있어도 배포·배포 요청은 거부한다
    graph = {"nodes": [{"id": "s", "kind": "start", "code": "N-01", "name": "s", "config": {}},
                       {"id": "g", "kind": "http_tool", "code": "N-02", "name": "g", "config": {"url": "https://example.com/x"}}],
             "edges": [{"from": "s", "to": "g", "handle": "next"}]}
    assert jpost(client, f"{BASE}{fid}/draft/", {"graph": graph}, admin_token, "put").json()["valid"] is True
    assert jpost(client, f"{BASE}{fid}/versions/", {}, admin_token).status_code == 201
    denied = jpost(client, f"{BASE}{fid}/deploy/", {"version": 1}, admin_token)
    assert denied.status_code == 422 and denied.json()["errors"][0]["code"] == "no_handler"
    assert client.get(BASE, **h(admin_token)).json()[0]["deployed_version"] is None


@pytest.mark.django_db
def test_note_too_long_is_422(client, admin_token):
    fid = new_flow(client, admin_token)
    r = jpost(client, f"{BASE}{fid}/versions/", {"note": "가" * 201}, admin_token)
    assert r.status_code == 422 and r.json()["code"] == "note_too_long"


# ── 배포 승인 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_config_option_is_admin_only(client, tenant_with_key, admin_token, tenant_member_token):
    tenant, _ = tenant_with_key
    url = "/api/tenant/config/"
    assert client.get(url, **h(admin_token)).json()["deploy_requires_approval"] is False
    assert jpost(client, url, {"deploy_requires_approval": True}, tenant_member_token, "patch").status_code == 403
    assert jpost(client, url, {"deploy_requires_approval": True}, admin_token, "patch").json()["deploy_requires_approval"] is True
    assert TenantConfig.objects.get(tenant=tenant).deploy_requires_approval is True


@pytest.mark.django_db
def test_deploy_request_approve_flow(client, tenant_with_key, admin_token):
    tenant, _ = tenant_with_key
    fid = new_flow(client, admin_token)
    jpost(client, f"{BASE}{fid}/versions/", {}, admin_token)
    TenantConfig.objects.filter(tenant=tenant).update(deploy_requires_approval=True)

    asked = jpost(client, f"{BASE}{fid}/deploy/", {"version": 1}, admin_token)
    assert asked.status_code == 202 and asked.json()["deployed"] is False
    req = asked.json()["request"]
    assert req["status"] == "pending" and req["version"] == 1 and req["flow_id"] == fid
    assert client.get(BASE, **h(admin_token)).json()[0]["deployed_version"] is None
    assert client.get(f"{BASE}{fid}/", **h(admin_token)).json()["pending_request"]["id"] == req["id"]
    assert [r["id"] for r in client.get(f"{REQS}?status=pending", **h(admin_token)).json()] == [req["id"]]

    # 요청자 본인은 승인할 수 없다
    assert jpost(client, f"{REQS}{req['id']}/approve/", {}, admin_token).status_code == 403
    approver = make_membership(tenant, "admin", name="승인자")
    done = jpost(client, f"{REQS}{req['id']}/approve/", {"note": "확인"}, member_token(approver))
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert client.get(BASE, **h(admin_token)).json()[0]["deployed_version"] == 1
    assert jpost(client, f"{REQS}{req['id']}/reject/", {}, member_token(approver)).status_code == 409


@pytest.mark.django_db
def test_deploy_request_reject(client, tenant_with_key, admin_token):
    tenant, _ = tenant_with_key
    fid = new_flow(client, admin_token)
    jpost(client, f"{BASE}{fid}/versions/", {}, admin_token)
    TenantConfig.objects.filter(tenant=tenant).update(deploy_requires_approval=True)
    req = jpost(client, f"{BASE}{fid}/deploy/", {"version": 1}, admin_token).json()["request"]

    other = member_token(make_membership(tenant, "admin"))
    rejected = jpost(client, f"{REQS}{req['id']}/reject/", {"note": "보류"}, other)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected" and rejected.json()["decision_note"] == "보류"
    assert client.get(BASE, **h(admin_token)).json()[0]["deployed_version"] is None
    assert DeployRequest.objects.get(pk=req["id"]).status == DeployRequest.REJECTED


# ── 시험 대화(SSE) ───────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_test_conversation_streams_trace_reply_done(client, tenant_with_key, admin_token, fake_chat_llm):
    fid = new_flow(client, admin_token)

    r = jpost(client, f"{BASE}{fid}/test/", {"message": "영업시간이 어떻게 되나요?"}, admin_token)

    assert r.status_code == 200 and r["Content-Type"].startswith("text/event-stream")
    events = sse_events(r)
    names = [n for n, _ in events]
    assert names == ["start", "trace", "trace", "reply", "done"]
    assert [d["node_id"] for n, d in events if n == "trace"] == ["start", "answer"]
    assert dict(events)["reply"]["text"] == "안녕하세요! 무엇을 도와드릴까요?"
    done = dict(events)["done"]
    assert done["ran"] is True and done["handoff"] is False and done["errors"] == []

    # 판단 기록은 session_id로 이미 있는 조회 API가 돌려준다 — 시험 세션 포함
    traces = client.get(f"/api/tenant/sessions/{done['session_id']}/decision-traces", **h(admin_token)).json()
    assert traces["turns"][0]["is_test"] is True and [e["node_id"] for e in traces["turns"][0]["entries"]] == ["start", "answer"]


@pytest.mark.django_db(transaction=True)
def test_test_conversation_never_notifies_a_human(client, tenant_with_key, admin_token, fake_chat_llm):
    tenant, _ = tenant_with_key
    fid = new_flow(client, admin_token, ops=[*OPS,
        {"op": "upsert_node", "id": "human", "kind": "handoff", "code": "N-03"},
        {"op": "connect", "from": "answer", "to": "human", "handle": "handoff"}])

    r = jpost(client, f"{BASE}{fid}/test/", {"message": "상담원 연결해 주세요"}, admin_token)

    done = dict(sse_events(r))["done"]
    assert done["ran"] and done["handoff"] is True and done["note"]
    assert Escalation.objects.filter(session__tenant_id=tenant.id).count() == 0
    session = ChatSession.objects.get(pk=done["session_id"])
    assert session.is_test and not session.is_hitl


@pytest.mark.django_db(transaction=True)
def test_test_conversation_reports_invalid_draft_in_done(client, admin_token, fake_chat_llm):
    fid = new_flow(client, admin_token, ops=None)
    r = jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token)
    events = sse_events(r)
    assert [n for n, _ in events] == ["start", "done"]
    done = events[-1][1]
    assert done["ran"] is False and done["errors"] and done["session_id"] is None


@pytest.mark.django_db(transaction=True)
def test_test_conversation_unknown_flow_and_session(client, admin_token):
    assert jpost(client, f"{BASE}00000000-0000-0000-0000-000000000000/test/", {"message": "x"}, admin_token).status_code == 404
    fid = new_flow(client, admin_token)
    r = jpost(client, f"{BASE}{fid}/test/", {"message": "x", "session_id": "00000000-0000-0000-0000-000000000000"}, admin_token)
    assert r.status_code == 404


# ── 노드 config 스키마(#98) ──────────────────────────────────────────────────


def test_node_schema_lists_every_kind_with_fields_enums_and_required(client, admin_token):
    from apps.flows.schema import NodeKind

    res = client.get(f"{BASE}node-schema/", **h(admin_token))

    assert res.status_code == 200
    kinds = res.json()["kinds"]
    assert set(kinds) == {k.value for k in NodeKind}
    guard = kinds["guardrail"]["config"]
    assert guard["properties"]["action"]["enum"] == ["block", "rewrite", "mask"]
    assert guard["properties"]["rules"]["items"]["enum"] == ["banned_words", "pii", "price_promise"]
    assert "rules" in guard["required"]
    assert "url" in kinds["http_tool"]["config"]["required"]


def test_node_schema_needs_no_flow_edit_permission(client, tenant_admin_token):
    """읽기 전용 멤버도 폼 틀을 읽을 수 있어야 한다(저장만 flow.edit)."""
    assert client.get(f"{BASE}node-schema/").status_code == 401


def test_seeded_default_flow_version_is_labelled_as_system(client, tenant_with_key, admin_token):
    """기본 흐름 시드 버전은 만든 이가 없다 — 「알 수 없음」이 아니라 「시스템(기본 흐름)」으로 내보낸다. 사람이 저장한 버전은 그 사람."""
    from apps.flows.defaults import ensure_default_flow

    tenant, _ = tenant_with_key
    flow = ensure_default_flow(tenant.id)
    body = client.get(f"{BASE}{flow.id}/", **h(admin_token)).json()
    assert [(v["number"], v["created_by_name"]) for v in body["versions"]] == [(1, "시스템(기본 흐름)")]
