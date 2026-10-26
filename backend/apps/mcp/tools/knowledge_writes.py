"""지식·방문자 쓰기 도구(#214, 우산 #207) — 모두 2단계, 권한 비트 없음(Member도; HTTP가 지금 그렇다).

- ``add_document_url``·``refetch_document``(``flow.write``), ``delete_document``·``rebuild_graph``·``delete_visitor_memory``(``flow.admin``).
- 실행은 테넌트 API와 같은 서비스(``apps.rag.services``·``apps.memory.manager``)를 쓴다. 가져오기·임베딩·재빌드는 큐로 가므로
  응답은 「접수됨」이고 결과는 ``list_documents``(문서 상태·``graph_freshness``)로 본다.
- ``add_document_url``은 미리보기·실행 모두 URL 가드(스킴·IP 리터럴·userinfo·포트, 웹 문서 가져오기와 같은 정책)를 먼저 건다.
  미리보기에서는 URL을 가져오지 않는다(해석도 하지 않는다 — 해석·IP 검사는 가져올 때 홉마다 한다).
- 파일 업로드는 MCP에 넣지 않는다.
"""

from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.core.safe_http import SafeHttpError, check_tenant_url, user_message
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.memory import manager as memory_manager
from apps.memory.models import VisitorMemory
from apps.rag import services
from apps.rag.models import Document
from apps.rag.web import WEB_SCHEMES
from apps.tenants import try_tenant

MEMORY_SUMMARY_CHARS = 100
CHECK_HINT = "list_documents로 문서 status(pending|processing|ready|failed)와 graph_freshness를 확인하세요."


class AddUrlArgs(TenantArgs):
    url: str = Field(min_length=1, max_length=2000, description="가져올 웹 주소(http·https, 한 개)")


class DocumentArgs(TenantArgs):
    document_id: str = Field(description="문서 id(list_documents로 확인)")


class RebuildArgs(TenantArgs):
    pass


class VisitorMemoryArgs(TenantArgs):
    visitor_id: str = Field(min_length=1, description="방문자 id")
    memory_id: str = Field(description="기억 항목 id(방문자 기억 목록의 id)")


def _check_upload_allowed(tenant_id: uuid.UUID) -> None:
    try:
        services.ensure_upload_allowed(tenant_id)
    except try_tenant.TryRestricted as exc:
        raise ToolError(exc.code, "체험에서는 문서를 올릴 수 없습니다.") from exc


def _checked_url(raw: str) -> str:
    url = raw.strip()
    try:
        check_tenant_url(url, WEB_SCHEMES)
    except SafeHttpError as exc:
        raise ToolError("url_not_allowed", f"이 주소는 쓸 수 없습니다: {user_message(exc, WEB_SCHEMES)}") from exc
    return url


def _document(tenant_id: uuid.UUID, document_id: str) -> Document:
    try:
        doc = Document.objects.filter(id=uuid.UUID(document_id), tenant_id=tenant_id).first()
    except ValueError:
        doc = None
    if doc is None:
        raise ToolError("document_not_found", "그런 문서가 없습니다. list_documents로 id를 확인하세요.")
    return doc


def _url_document(tenant_id: uuid.UUID, document_id: str) -> Document:
    doc = _document(tenant_id, document_id)
    if doc.source_type != Document.SOURCE_URL:
        raise ToolError("not_url_document", "URL Document만 재-fetch할 수 있습니다")
    return doc


def _row(doc: Document) -> JSONObject:
    doc.refresh_from_db()
    return {"id": str(doc.id), "name": doc.name, "status": doc.status, "error": doc.error_message}


# ── add_document_url ─────────────────────────────────────────────────────────


async def _preview_add_url(ctx: ToolContext, args: AddUrlArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def load() -> JSONObject:
        _check_upload_allowed(tenant_id)
        url = _checked_url(args.url)
        return {
            "url": url,
            "already_registered": Document.objects.filter(tenant_id=tenant_id, source_url=url).exists(),
            "cost": "가져오기·임베딩 비용 발생",
            "note": "미리보기에서는 주소를 가져오지 않았습니다. 확정하면 문서로 등록하고 가져오기를 큐에 넣습니다.",
        }

    return await sync_to_async(load)()


@register(
    "add_document_url",
    "웹 주소 한 개를 지식 문서로 등록하고 가져오기·임베딩을 시작한다(재귀 크롤 아님; 권한 비트 없음, Member도). "
    "미리보기는 주소와 「가져오기·임베딩 비용 발생」(이미 같은 주소가 있으면 already_registered). 미리보기에서는 주소를 가져오지 않는다. "
    "응답은 접수된 문서 {id,name,status}와 확인 방법. 인자: url. 실패: url_not_allowed(스킴·사설 IP 등)·try_restricted.",
    AddUrlArgs,
    scope="flow.write",
    permission=None,
    writes=True,
    preview=_preview_add_url,
)
async def add_document_url(ctx: ToolContext, args: AddUrlArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def run() -> JSONObject:
        _check_upload_allowed(tenant_id)
        doc = services.register_url_document(tenant_id, _checked_url(args.url))
        return {"accepted": True, "document": _row(doc), "check": CHECK_HINT}

    return await sync_to_async(run)()


# ── refetch_document ─────────────────────────────────────────────────────────


async def _preview_refetch(ctx: ToolContext, args: DocumentArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def load() -> JSONObject:
        doc = _url_document(tenant_id, args.document_id)
        return {
            "document_id": str(doc.id),
            "name": doc.name,
            "source_url": doc.source_url,
            "last_collected_at": doc.updated_at.isoformat(),
            "status": doc.status,
            "effect": "기존 그래프 기여분을 지우고 같은 주소를 다시 가져와 교체한다(임베딩 비용 발생)",
        }

    return await sync_to_async(load)()


@register(
    "refetch_document",
    "URL 문서를 다시 가져온다: 기존 그래프 기여분을 지우고 다시 인제스션(교체). 권한 비트 없음(Member도). "
    "미리보기는 문서 이름·출처 URL·마지막 수집 시각(updated_at). 인자: document_id. 실패: document_not_found·not_url_document.",
    DocumentArgs,
    scope="flow.write",
    permission=None,
    writes=True,
    preview=_preview_refetch,
)
async def refetch_document(ctx: ToolContext, args: DocumentArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def run() -> JSONObject:
        doc = services.refetch_url_document(_url_document(tenant_id, args.document_id))
        return {"accepted": True, "document": _row(doc), "check": CHECK_HINT}

    return await sync_to_async(run)()


# ── delete_document ──────────────────────────────────────────────────────────


async def _preview_delete(ctx: ToolContext, args: DocumentArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def load() -> JSONObject:
        doc = _document(tenant_id, args.document_id)
        return {
            "document_id": str(doc.id),
            "name": doc.name,
            "chunks": services.chunk_count(doc),
            "irreversible": "복구 불가 — 문서와 그 청크·그래프 기여분이 지워진다",
        }

    return await sync_to_async(load)()


@register(
    "delete_document",
    "지식 문서를 지운다(청크·지식그래프 기여분 포함, 복구 불가; Admin 전용). 미리보기는 문서 이름·청크 수·「복구 불가」. "
    "인자: document_id. 실패: document_not_found.",
    DocumentArgs,
    scope="flow.admin",
    permission=None,
    writes=True,
    preview=_preview_delete,
)
async def delete_document(ctx: ToolContext, args: DocumentArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def run() -> JSONObject:
        doc = _document(tenant_id, args.document_id)
        name = doc.name
        services.delete_document(doc)
        return {"deleted": True, "document_id": args.document_id, "name": name}

    return await sync_to_async(run)()


# ── rebuild_graph ────────────────────────────────────────────────────────────


async def _preview_rebuild(ctx: ToolContext, args: RebuildArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def load() -> JSONObject:
        freshness = services.graph_freshness(tenant_id)
        preview: JSONObject = {
            "documents": Document.objects.filter(tenant_id=tenant_id).count(),
            "graph_freshness": freshness,
            "cost": "LLM 비용 발생",
        }
        if freshness == "rebuilding":
            preview["note"] = "이미 재빌드 중입니다. 확정해도 HTTP와 같이 한 번 더 큐에 넣습니다."
        return preview

    return await sync_to_async(load)()


@register(
    "rebuild_graph",
    "지식그래프 커뮤니티를 다시 만든다(Admin 전용; 큐로 접수). 미리보기는 문서 수·현재 graph_freshness·「LLM 비용 발생」. "
    "이미 재빌드 중이면 미리보기에 note(HTTP와 같이 막지는 않음). 인자 없음.",
    RebuildArgs,
    scope="flow.admin",
    permission=None,
    writes=True,
    preview=_preview_rebuild,
)
async def rebuild_graph(ctx: ToolContext, args: RebuildArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    await sync_to_async(services.start_graph_rebuild)(tenant_id)
    return {"accepted": True, "status": "rebuilding", "check": CHECK_HINT}


# ── delete_visitor_memory ────────────────────────────────────────────────────


def _memory(tenant_id: uuid.UUID, args: VisitorMemoryArgs) -> VisitorMemory:
    try:
        found = VisitorMemory.objects.filter(
            id=uuid.UUID(args.memory_id), tenant_id=tenant_id, visitor_id=args.visitor_id
        ).first()
    except ValueError:
        found = None
    if found is None:
        raise ToolError("memory_not_found", "그런 방문자 기억이 없습니다.")
    return found


async def _preview_delete_memory(ctx: ToolContext, args: VisitorMemoryArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def load() -> JSONObject:
        memory = _memory(tenant_id, args)
        value = memory.value
        summary = value if len(value) <= MEMORY_SUMMARY_CHARS else value[:MEMORY_SUMMARY_CHARS] + "…"
        return {
            "visitor_id": args.visitor_id,
            "memory_id": str(memory.id),
            "key": memory.key,
            "value_summary": summary,
            "irreversible": "복구 불가",
        }

    return await sync_to_async(load)()


@register(
    "delete_visitor_memory",
    "방문자 기억 항목 하나를 지운다(Admin 전용, 복구 불가). 미리보기는 기억 key와 내용 요약(앞 100자). "
    "인자: visitor_id, memory_id. 실패: memory_not_found.",
    VisitorMemoryArgs,
    scope="flow.admin",
    permission=None,
    writes=True,
    preview=_preview_delete_memory,
)
async def delete_visitor_memory(ctx: ToolContext, args: VisitorMemoryArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id

    def run() -> JSONObject:
        memory = _memory(tenant_id, args)
        memory_manager.delete_memory(str(tenant_id), args.visitor_id, str(memory.id))
        return {"deleted": True, "visitor_id": args.visitor_id, "memory_id": str(memory.id)}

    return await sync_to_async(run)()
