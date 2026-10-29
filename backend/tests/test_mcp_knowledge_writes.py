"""MCP 지식·방문자 쓰기 도구(#214): 모두 2단계, 권한 비트 없음(Member도).

큐(Celery ``delay``)는 비동기 경계라 적재 여부만 세는 기록기로 바꾼다 — 가져오기·LLM을 부르지 않는다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest

from apps.mcp.models import McpAuditEvent
from apps.memory.models import VisitorMemory
from apps.rag import tasks
from apps.rag.models import Document
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — 픽스처
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

ADMIN_SCOPES = "flow.read flow.write flow.admin"
ADMIN_TOOLS = ["delete_document", "rebuild_graph", "delete_visitor_memory"]


def ok(body: dict) -> dict:
    assert not body["result"].get("isError"), tool_text(body)
    return body["result"]["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def step1(body: dict) -> dict:
    """미리보기 필드에 confirm_token을 합친 모양."""
    out = ok(body)
    assert out["confirm_required"] is True
    return {**out["preview"], "confirm_token": out["confirm_token"]}


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, tuple[str, ...]]]]:
    """큐에 들어간 (작업, 인자) 기록."""
    seen: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: seen.append(("ingest", a)))
    monkeypatch.setattr(tasks.rebuild_graph_communities, "delay", lambda *a: seen.append(("rebuild", a)))
    yield seen


def tenant_as(role: str, slug: str = "acme"):
    tenant = make_tenant(slug)
    member_of(tenant, role)
    return tenant


def url_doc(tenant, url: str = "https://example.com/faq") -> Document:
    return Document.objects.create(
        tenant_id=tenant.id, name="FAQ", mime_type="text/html", source_type=Document.SOURCE_URL, source_url=url,
        status=Document.STATUS_READY,
    )


def file_doc(tenant) -> Document:
    return Document.objects.create(tenant_id=tenant.id, name="규정.pdf", mime_type="application/pdf", status=Document.STATUS_READY)


def memory_of(tenant, value: str = "단골 손님", visitor: str = "v1") -> VisitorMemory:
    return VisitorMemory.objects.create(tenant_id=tenant.id, visitor_id=visitor, key="메모", value=value)


# ── add_document_url ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_add_document_url_member_two_step(signer: Signer, queued) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    token = mcp_token(signer)
    args = {"url": "https://example.com/faq"}

    first = step1(call(token, "add_document_url", args))
    assert first["url"] == "https://example.com/faq" and "비용" in first["cost"] and first["already_registered"] is False
    assert Document.objects.filter(tenant_id=tenant.id).count() == 0 and queued == []  # 미리보기: 부작용 0

    out = ok(call(token, "add_document_url", {**args, "confirm_token": first["confirm_token"]}))
    doc = Document.objects.get(tenant_id=tenant.id)
    assert out["accepted"] is True and out["document"]["id"] == str(doc.id) and "list_documents" in out["check"]
    assert doc.source_type == Document.SOURCE_URL and doc.source_url == args["url"] and doc.name == args["url"]
    assert queued == [("ingest", (str(doc.id), str(tenant.id), "text/html"))]
    stages = list(McpAuditEvent.objects.filter(tool="add_document_url").order_by("id").values_list("confirm_stage", flat=True))
    assert stages == ["preview", "confirm"]


@pytest.mark.django_db
def test_add_document_url_flags_duplicate_in_preview(signer: Signer, queued) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    url_doc(tenant)
    assert step1(call(mcp_token(signer), "add_document_url", {"url": "https://example.com/faq"}))["already_registered"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.5/admin", "https://127.0.0.1/x", "https://169.254.169.254/latest", "ftp://example.com/a", "https://user:pw@example.com/", "not a url"],
)
def test_add_document_url_refuses_guarded_urls_without_a_token(signer: Signer, settings, queued, url: str) -> None:
    settings.OUTBOUND_PRIVATE_ALLOWED = False  # 운영과 같은 정책
    tenant = tenant_as(Membership.ROLE_MEMBER)
    body = call(mcp_token(signer), "add_document_url", {"url": url})
    assert "url_not_allowed" in err(body)
    assert "confirm_token" not in json.dumps(body)
    assert Document.objects.filter(tenant_id=tenant.id).count() == 0 and queued == []


# ── refetch_document ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_refetch_document_member_two_step(signer: Signer, queued) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    doc = url_doc(tenant)
    token = mcp_token(signer)
    args = {"document_id": str(doc.id)}

    first = step1(call(token, "refetch_document", args))
    assert first["name"] == "FAQ" and first["source_url"] == doc.source_url and first["last_collected_at"]
    doc.refresh_from_db()
    assert doc.status == Document.STATUS_READY and queued == []

    out = ok(call(token, "refetch_document", {**args, "confirm_token": first["confirm_token"]}))
    doc.refresh_from_db()
    assert out["accepted"] is True and doc.status == Document.STATUS_PENDING
    assert queued == [("ingest", (str(doc.id), str(tenant.id), "text/html"))]


@pytest.mark.django_db
def test_refetch_document_refuses_file_documents(signer: Signer, queued) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    doc = file_doc(tenant)
    assert "not_url_document" in err(call(mcp_token(signer), "refetch_document", {"document_id": str(doc.id)}))
    assert queued == []


# ── delete_document ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_delete_document_admin_two_step(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    doc = file_doc(tenant)
    token = mcp_token(signer, scope=ADMIN_SCOPES)
    args = {"document_id": str(doc.id)}

    first = step1(call(token, "delete_document", args))
    assert first["name"] == "규정.pdf" and first["chunks"] == 0 and "복구 불가" in first["irreversible"]
    assert Document.objects.filter(pk=doc.pk).exists()

    out = ok(call(token, "delete_document", {**args, "confirm_token": first["confirm_token"]}))
    assert out["deleted"] is True and not Document.objects.filter(pk=doc.pk).exists()


# ── rebuild_graph ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_rebuild_graph_two_step(signer: Signer, queued) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    file_doc(tenant)
    url_doc(tenant)
    token = mcp_token(signer, scope=ADMIN_SCOPES)

    first = step1(call(token, "rebuild_graph", {}))
    assert first["documents"] == 2 and "LLM" in first["cost"] and first["graph_freshness"] in {"fresh", "stale", "rebuilding"}
    assert queued == []

    out = ok(call(token, "rebuild_graph", {"confirm_token": first["confirm_token"]}))
    assert out["accepted"] is True and "list_documents" in out["check"]
    assert queued == [("rebuild", (str(tenant.id),))]


@pytest.mark.django_db
def test_rebuild_graph_preview_notes_when_already_rebuilding(signer: Signer, queued) -> None:
    from django.db import connection

    tenant = tenant_as(Membership.ROLE_MEMBER)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO kg_graph_meta (tenant_id, freshness) VALUES (%s, 'rebuilding') "
            "ON CONFLICT (tenant_id) DO UPDATE SET freshness='rebuilding'",
            [str(tenant.id)],
        )
    first = step1(call(mcp_token(signer, scope=ADMIN_SCOPES), "rebuild_graph", {}))
    assert first["graph_freshness"] == "rebuilding" and "이미 재빌드 중" in first["note"]


# ── delete_visitor_memory ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_delete_visitor_memory_two_step_with_clipped_summary(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    memory = memory_of(tenant, "가" * 300)
    token = mcp_token(signer, scope=ADMIN_SCOPES)
    args = {"visitor_id": "v1", "memory_id": str(memory.id)}

    first = step1(call(token, "delete_visitor_memory", args))
    assert first["value_summary"] == "가" * 100 + "…" and first["key"] == "메모"
    assert VisitorMemory.objects.filter(pk=memory.pk).exists()

    out = ok(call(token, "delete_visitor_memory", {**args, "confirm_token": first["confirm_token"]}))
    assert out["deleted"] is True and not VisitorMemory.objects.filter(pk=memory.pk).exists()


@pytest.mark.django_db
def test_delete_visitor_memory_wrong_visitor_is_not_found(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    memory = memory_of(tenant)
    token = mcp_token(signer, scope=ADMIN_SCOPES)
    assert "memory_not_found" in err(call(token, "delete_visitor_memory", {"visitor_id": "other", "memory_id": str(memory.id)}))
    assert "memory_not_found" in err(call(token, "delete_visitor_memory", {"visitor_id": "v1", "memory_id": "not-a-uuid"}))


# ── 공통: 스코프·남의 테넌트 ────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("tool", ADMIN_TOOLS)
def test_flow_write_only_token_is_403_asking_for_flow_admin(signer: Signer, tool: str) -> None:
    tenant_as(Membership.ROLE_MEMBER)
    response = post(rpc("tools/call", {"name": tool, "arguments": {}}), mcp_token(signer, scope="flow.read flow.write"))
    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.admin"'


@pytest.mark.django_db
@pytest.mark.parametrize("tool", ["add_document_url", "refetch_document"])
def test_write_tools_need_flow_write(signer: Signer, tool: str) -> None:
    tenant_as(Membership.ROLE_MEMBER)
    response = post(rpc("tools/call", {"name": tool, "arguments": {}}), mcp_token(signer, scope="flow.read"))
    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.write"'


@pytest.mark.django_db
def test_other_tenants_documents_and_memories_are_not_found(signer: Signer, queued) -> None:
    tenant_as(Membership.ROLE_MEMBER, "mine")
    other = make_tenant("theirs")
    doc, url = file_doc(other), url_doc(other)
    memory = memory_of(other)
    token = mcp_token(signer, scope=ADMIN_SCOPES)

    assert "document_not_found" in err(call(token, "delete_document", {"document_id": str(doc.id)}))
    assert "document_not_found" in err(call(token, "refetch_document", {"document_id": str(url.id)}))
    assert "memory_not_found" in err(call(token, "delete_visitor_memory", {"visitor_id": "v1", "memory_id": str(memory.id)}))
    assert "document_not_found" in err(call(token, "delete_document", {"document_id": str(uuid.uuid4())}))
    assert Document.objects.filter(tenant_id=other.id).count() == 2 and VisitorMemory.objects.filter(pk=memory.pk).exists()
    assert queued == []


@pytest.mark.django_db
@pytest.mark.parametrize("tool", ADMIN_TOOLS + ["add_document_url", "refetch_document"])
def test_every_tool_is_listed_as_destructive_with_confirm_token(signer: Signer, tool: str) -> None:
    tenant_as(Membership.ROLE_MEMBER)
    tools = post(rpc("tools/list"), mcp_token(signer, scope=ADMIN_SCOPES)).json()["result"]["tools"]
    entry = next(t for t in tools if t["name"] == tool)
    assert entry["annotations"]["destructiveHint"] is True and "confirm_token" in entry["inputSchema"]["properties"]
