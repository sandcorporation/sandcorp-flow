"""MCP 읽기 도구(#77) — 문서·지식그래프·상담 세션을 `flow.read`만으로, 자기 테넌트 안에서만 읽는다.

외부 경계는 JWKS·시계만 대역이다. DB·그래프 저장소·Redis(presence)는 실제 객체다.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatSession
from apps.escalation.models import Escalation
from apps.rag.graph_store import GraphStore
from apps.rag.models import Document
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator는 픽스처
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    signer,
    tool_text,
)
from resource_token_fakes import Signer

READ_TOOLS = ["list_documents", "search_knowledge", "list_sessions", "get_session"]


def ok(body: dict) -> dict:
    result = body["result"]
    assert not result.get("isError"), tool_text(body)
    return json.loads(tool_text(body))


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def member(slug: str = "acme"):
    tenant = make_tenant(slug)
    member_of(tenant, Membership.ROLE_MEMBER)
    return tenant


def readonly_token(signer: Signer) -> str:
    return mcp_token(signer, scope="flow.read")


def session(tenant, visitor: str = "v1", *, is_test: bool = False, age_days: int = 0) -> ChatSession:
    s = ChatSession.objects.create(tenant_id=tenant.id, visitor_id=visitor, is_test=is_test)
    if age_days:
        ChatSession.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    return s


def say(s: ChatSession, role: str, content: str) -> ChatMessage:
    return ChatMessage.objects.create(session=s, role=role, content=content)


# ── 도구 등록 ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_read_tools_are_registered_as_flow_read() -> None:
    from apps.mcp import registry
    import apps.mcp.tools  # noqa: F401 — 등록

    for name in READ_TOOLS:
        tool = registry.get_tool(name)
        assert tool is not None, name
        assert tool.scope == "flow.read" and tool.writes is False


# ── list_documents ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_documents_shows_status_error_and_graph_freshness(signer: Signer) -> None:
    tenant = member()
    Document.objects.create(tenant_id=tenant.id, name="환불 규정.pdf", mime_type="application/pdf", status="ready")
    Document.objects.create(
        tenant_id=tenant.id, name="깨진.pdf", mime_type="application/pdf", status="failed", error_message="추출 실패"
    )
    GraphStore(str(tenant.id)).set_freshness("stale")

    result = ok(call(readonly_token(signer), "list_documents"))

    rows = {d["name"]: d for d in result["documents"]}
    assert rows["환불 규정.pdf"]["status"] == "ready" and rows["환불 규정.pdf"]["error"] == ""
    assert rows["깨진.pdf"]["status"] == "failed" and rows["깨진.pdf"]["error"] == "추출 실패"
    assert rows["깨진.pdf"]["updated_at"]
    assert result["graph_freshness"] == "stale"
    assert result["next_cursor"] is None


@pytest.mark.django_db
def test_list_documents_excludes_other_tenants(signer: Signer) -> None:
    mine, other = member("acme"), make_tenant("other")
    Document.objects.create(tenant_id=mine.id, name="내것", mime_type="text/plain")
    Document.objects.create(tenant_id=other.id, name="남의것", mime_type="text/plain")

    names = [d["name"] for d in ok(call(readonly_token(signer), "list_documents"))["documents"]]
    assert names == ["내것"]


@pytest.mark.django_db
def test_list_documents_pages_with_cursor(signer: Signer) -> None:
    tenant = member()
    for i in range(5):
        Document.objects.create(tenant_id=tenant.id, name=f"문서{i}", mime_type="text/plain")
    token = readonly_token(signer)

    first = ok(call(token, "list_documents", {"limit": 2}))
    second = ok(call(token, "list_documents", {"limit": 2, "cursor": first["next_cursor"]}))
    third = ok(call(token, "list_documents", {"limit": 2, "cursor": second["next_cursor"]}))

    names = [d["name"] for page in (first, second, third) for d in page["documents"]]
    assert len(names) == 5 and len(set(names)) == 5
    assert third["next_cursor"] is None


# ── search_knowledge ──────────────────────────────────────────────────────────


def seed_graph(tenant) -> None:
    gs = GraphStore(str(tenant.id))
    gs.upsert_mention("m1", "환불정책", "policy", "구매 후 7일 이내 환불 가능")
    gs.upsert_mention("m2", "배송", "topic", "영업일 기준 2일")
    gs.upsert_mention_relation("m1", "m2", "환불 시 배송비는 고객 부담")


@pytest.mark.django_db
def test_search_knowledge_returns_entities_with_relations(signer: Signer) -> None:
    tenant = member()
    seed_graph(tenant)

    result = ok(call(readonly_token(signer), "search_knowledge", {"query": "환불정책"}))

    entity = next(e for e in result["entities"] if e["name"] == "환불정책")
    assert entity["description"] == "구매 후 7일 이내 환불 가능"
    assert {"source": "환불정책", "target": "배송", "description": "환불 시 배송비는 고객 부담"} in entity["relations"]


@pytest.mark.django_db
def test_search_knowledge_is_tenant_isolated(signer: Signer) -> None:
    member("acme")
    other = make_tenant("other")
    seed_graph(other)

    result = ok(call(readonly_token(signer), "search_knowledge", {"query": "환불정책"}))
    assert result["entities"] == []


@pytest.mark.django_db
def test_search_knowledge_caps_top_k_and_relations(signer: Signer) -> None:
    tenant = member()
    gs = GraphStore(str(tenant.id))
    gs.upsert_mention("hub", "허브제품", "product", "중심")
    for i in range(30):
        gs.upsert_mention(f"s{i}", f"허브부품{i}", "part", "부품")
        gs.upsert_mention_relation("hub", f"s{i}", f"구성{i}")
    token = readonly_token(signer)

    result = ok(call(token, "search_knowledge", {"query": "허브제품"}))
    hub = next(e for e in result["entities"] if e["name"] == "허브제품")
    assert len(hub["relations"]) <= 20 and hub["more_relations"] is True

    many = ok(call(token, "search_knowledge", {"query": "허브부품", "top_k": 3}))
    assert len(many["entities"]) <= 3


@pytest.mark.django_db
def test_search_knowledge_rejects_oversized_top_k(signer: Signer) -> None:
    member()
    body = call(readonly_token(signer), "search_knowledge", {"query": "x", "top_k": 99})
    assert body["result"]["isError"] or "error" in body


# ── list_sessions ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_sessions_recent_first_without_test_sessions(signer: Signer) -> None:
    tenant = member()
    old = session(tenant, "old", age_days=1)
    new = session(tenant, "new")
    session(tenant, "tester", is_test=True)
    say(old, "user", "안녕")
    say(new, "user", "문의")

    result = ok(call(readonly_token(signer), "list_sessions"))

    assert [s["session_id"] for s in result["sessions"]] == [str(new.id), str(old.id)]
    assert {"visitor_id", "is_hitl", "escalation_status", "created_at", "last_activity"} <= set(result["sessions"][0])
    assert result["next_cursor"] is None


@pytest.mark.django_db
def test_list_sessions_filters_by_period_and_escalation(signer: Signer) -> None:
    tenant = member()
    recent = session(tenant, "recent")
    ancient = session(tenant, "ancient", age_days=30)
    esc = session(tenant, "esc", age_days=2)
    Escalation.objects.create(session=esc, trigger_type=Escalation.TRIGGER_AI, status=Escalation.STATUS_PENDING)
    token = readonly_token(signer)

    since = (timezone.now() - timedelta(days=10)).isoformat()
    in_period = {s["session_id"] for s in ok(call(token, "list_sessions", {"since": since}))["sessions"]}
    assert in_period == {str(recent.id), str(esc.id)}

    wide = {s["session_id"] for s in ok(call(token, "list_sessions", {"since": (timezone.now() - timedelta(days=60)).isoformat()}))["sessions"]}
    assert str(ancient.id) in wide

    escalated = ok(call(token, "list_sessions", {"status": "escalated", "since": (timezone.now() - timedelta(days=60)).isoformat()}))
    assert [s["session_id"] for s in escalated["sessions"]] == [str(esc.id)]
    assert escalated["sessions"][0]["escalation_status"] == "pending"


@pytest.mark.django_db
def test_list_sessions_pages_with_cursor(signer: Signer) -> None:
    tenant = member()
    made = [session(tenant, f"v{i}") for i in range(5)]
    token = readonly_token(signer)

    pages = []
    cursor = None
    while True:
        args: dict[str, object] = {"limit": 2}
        if cursor:
            args["cursor"] = cursor
        page = ok(call(token, "list_sessions", args))
        pages.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break

    ids = [s["session_id"] for p in pages for s in p["sessions"]]
    assert len(pages) == 3 and sorted(ids) == sorted(str(s.id) for s in made)


@pytest.mark.django_db
def test_list_sessions_excludes_other_tenants(signer: Signer) -> None:
    mine, other = member("acme"), make_tenant("other")
    session(other, "stranger")
    s = session(mine, "me")

    ids = [x["session_id"] for x in ok(call(readonly_token(signer), "list_sessions"))["sessions"]]
    assert ids == [str(s.id)]


# ── get_session ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_session_reads_messages_escalations_and_flow_version(signer: Signer) -> None:
    from apps.flows import services

    tenant = member()
    flow = services.create_flow(tenant.id, "상담")
    s = session(tenant, "v1")
    say(s, "user", "환불하고 싶어요")
    say(s, "assistant", "도와드릴게요")
    Escalation.objects.create(session=s, trigger_type=Escalation.TRIGGER_VISITOR, reason="사람 연결 요청")

    result = ok(call(readonly_token(signer), "get_session", {"session_id": str(s.id)}))

    assert result["visitor_id"] == "v1" and result["is_test"] is False
    assert [(m["role"], m["content"]) for m in result["messages"]] == [("user", "환불하고 싶어요"), ("assistant", "도와드릴게요")]
    assert result["has_more"] is False and result["next_cursor"] is None
    assert result["escalations"][0]["trigger"] == "visitor" and result["escalations"][0]["reason"] == "사람 연결 요청"
    assert result["flow_version"] is None  # 공개 흐름이 없을 때 세션은 버전에 고정되지 않는다
    assert flow.name


@pytest.mark.django_db
def test_get_session_reports_the_pinned_flow_version(signer: Signer) -> None:
    from apps.flows import services

    tenant = member()
    flow = services.create_flow(tenant.id, "상담", {"nodes": [{"id": "start", "kind": "start", "code": "N-01"}], "edges": []})
    services.save_version(tenant.id, flow.id)
    services.deploy_version(tenant.id, flow.id, 1)
    flow.is_public = True
    flow.save(update_fields=["is_public"])
    s = session(tenant)

    result = ok(call(readonly_token(signer), "get_session", {"session_id": str(s.id)}))
    assert result["flow_version"] == {"flow": "상담", "number": 1}


@pytest.mark.django_db
def test_get_session_truncates_long_conversations_with_cursor(signer: Signer) -> None:
    tenant = member()
    s = session(tenant)
    for i in range(7):
        say(s, "user", f"m{i}")
    token = readonly_token(signer)

    first = ok(call(token, "get_session", {"session_id": str(s.id), "limit": 3}))
    assert [m["content"] for m in first["messages"]] == ["m0", "m1", "m2"]
    assert first["has_more"] is True

    rest = ok(call(token, "get_session", {"session_id": str(s.id), "limit": 10, "cursor": first["next_cursor"]}))
    assert [m["content"] for m in rest["messages"]] == ["m3", "m4", "m5", "m6"]
    assert rest["has_more"] is False


@pytest.mark.django_db
def test_get_session_truncates_long_message_text(signer: Signer) -> None:
    tenant = member()
    s = session(tenant)
    say(s, "user", "가" * 5000)

    msg = ok(call(readonly_token(signer), "get_session", {"session_id": str(s.id)}))["messages"][0]
    assert len(msg["content"]) < 5000 and msg["truncated"] is True


@pytest.mark.django_db
def test_get_session_refuses_other_tenant_and_test_sessions(signer: Signer) -> None:
    mine, other = member("acme"), make_tenant("other")
    stranger = session(other)
    tester = session(mine, is_test=True)
    token = readonly_token(signer)

    assert "session_not_found" in err(call(token, "get_session", {"session_id": str(stranger.id)}))
    assert "session_not_found" in err(call(token, "get_session", {"session_id": str(tester.id)}))
    assert "session_not_found" in err(call(token, "get_session", {"session_id": str(uuid.uuid4())}))
    assert "session_not_found" in err(call(token, "get_session", {"session_id": "not-a-uuid"}))


# ── 공통: flow.read만으로 동작, 테넌트 인자 ───────────────────────────────────


@pytest.mark.django_db
def test_every_read_tool_works_with_flow_read_scope_only(signer: Signer) -> None:
    tenant = member()
    s = session(tenant)
    token = readonly_token(signer)
    calls = {
        "list_documents": {},
        "search_knowledge": {"query": "환불"},
        "list_sessions": {},
        "get_session": {"session_id": str(s.id)},
    }
    for tool, args in calls.items():
        assert not call(token, tool, args)["result"].get("isError"), tool


@pytest.mark.django_db
def test_read_tools_require_membership_in_the_named_tenant(signer: Signer) -> None:
    member("acme")
    make_tenant("other")  # 멤버십 없음
    token = readonly_token(signer)
    for tool, args in {"list_documents": {}, "list_sessions": {}, "search_knowledge": {"query": "x"}}.items():
        assert call(token, tool, {**args, "tenant": "other"})["result"]["isError"], tool
