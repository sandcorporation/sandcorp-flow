"""지식 읽기 도구(#77, 스펙 #74 결정 6·7) — ``list_documents``·``search_knowledge``.

테넌트 API와 같은 쿼리를 ``apps/rag/services.py``에서 가져다 쓴다. 응답은 쪽(커서)과 상한으로 자른다.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.mcp.access import TenantArgs
from apps.mcp.pagination import decode_cursor, encode_cursor
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.rag import services

MAX_RELATIONS_PER_ENTITY = 20
MAX_TEXT = 1000


class ListDocumentsArgs(TenantArgs):
    limit: int = Field(default=50, ge=1, le=100, description="한 쪽 건수(최대 100)")
    cursor: str | None = Field(default=None, description="앞 결과의 next_cursor")


class SearchKnowledgeArgs(TenantArgs):
    query: str = Field(min_length=1, max_length=500, description="찾을 말(엔티티 이름·설명에 부분 일치 + 의미 검색)")
    top_k: int = Field(default=5, ge=1, le=20, description="엔티티 최대 개수(최대 20)")


def _clip(text: str | None) -> str | None:
    return text if text is None or len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


@register(
    "list_documents",
    "테넌트 지식 문서 목록(오래된 순): id·이름·종류·상태(pending|processing|ready|failed)·오류·갱신 시각, "
    "그리고 지식그래프 신선도 graph_freshness(fresh|stale|rebuilding). 인자: limit?, cursor?(next_cursor). "
    "다음 쪽이 있으면 next_cursor가 문자열, 없으면 null.",
    ListDocumentsArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_documents(ctx: ToolContext, args: ListDocumentsArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant.id
    offset = decode_cursor(args.cursor)

    def load():
        docs = services.list_documents(tenant_id, offset=offset, limit=args.limit + 1)
        return docs, services.graph_freshness(tenant_id)

    docs, freshness = await sync_to_async(load)()
    page = docs[: args.limit]
    return {
        "documents": [
            {
                "id": str(d.id),
                "name": d.name,
                "source": d.source_type,
                "status": d.status,
                "error": d.error_message,
                "updated_at": d.updated_at.isoformat(),
            }
            for d in page
        ],
        "graph_freshness": freshness,
        "next_cursor": encode_cursor(offset + args.limit) if len(docs) > args.limit else None,
    }


@register(
    "search_knowledge",
    "지식그래프 조회(챗봇이 답할 때 쓰는 Local Search와 같은 검색): 질의에 맞는 엔티티(name·type·description)와 "
    "각 엔티티의 1홉 관계(source→target, description). 엔티티당 관계는 20개까지, 넘으면 more_relations=true. "
    "인자: query, top_k?(기본 5, 최대 20). 맞는 것이 없으면 entities가 빈 목록.",
    SearchKnowledgeArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def search_knowledge(ctx: ToolContext, args: SearchKnowledgeArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant.id
    try:
        hits = await sync_to_async(services.local_search)(tenant_id, args.query, top_k=args.top_k)
    except Exception as exc:  # 그래프 저장소·임베딩 경계 실패는 도구 오류로 알린다
        raise ToolError("search_failed", f"지식그래프 검색에 실패했습니다: {type(exc).__name__}") from exc
    return {
        "entities": [
            {
                "name": h.name,
                "type": h.entity_type,
                "description": _clip(h.description),
                "source_document_id": h.source_document_id,
                "relations": [dict(r) for r in h.relations[:MAX_RELATIONS_PER_ENTITY]],
                "more_relations": len(h.relations) > MAX_RELATIONS_PER_ENTITY,
            }
            for h in hits[: args.top_k]
        ]
    }
