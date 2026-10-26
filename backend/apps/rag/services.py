"""지식 읽기 서비스 — 테넌트 API·에이전트 노드·MCP 읽기 도구가 같은 쿼리를 쓴다(#77).

모든 함수 첫 인자는 ``tenant_id``(범위 강제). 읽기 전용.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from apps.rag.models import Document


def list_documents(tenant_id: uuid.UUID | str, *, offset: int = 0, limit: int | None = None) -> list[Document]:
    """테넌트 문서(오래된 순 — 목록 순서가 쪽마다 흔들리지 않게 id로 한 번 더 고정)."""
    qs = Document.objects.filter(tenant_id=tenant_id).order_by("created_at", "id")
    return list(qs[offset:] if limit is None else qs[offset : offset + limit])


def graph_freshness(tenant_id: uuid.UUID | str) -> str:
    """Tenant Knowledge Graph 신선도(fresh/stale/rebuilding)."""
    from apps.rag.graph_store import GraphStore

    return GraphStore(str(tenant_id)).get_freshness()


@dataclass
class EntityHit:
    """질의에 맞은 엔티티 하나와 아직 나오지 않은 1홉 관계(엔티티마다 중복 없이)."""

    name: str
    entity_type: str | None
    description: str | None
    source_document_id: str | None
    relations: list[dict[str, str | None]] = field(default_factory=list)


def local_search(tenant_id: uuid.UUID | str, query: str, *, top_k: int = 5) -> list[EntityHit]:
    """엔티티 중심 근거 — 질의로 resolved Entity를 찾고 그 이웃 관계를 모은다(ADR-0010)."""
    from apps.rag.graph_store import GraphStore

    gs = GraphStore.for_search(tenant_id)  # 체험 테넌트는 도서관의 자기 예제 묶음만(#134)
    hits: list[EntityHit] = []
    seen_entities: set[str] = set()
    seen_edges: set[tuple[str, str, str | None]] = set()
    for ent in gs.search_entities(query, top_k=top_k):
        name = ent["name"]
        if name in seen_entities:
            continue
        seen_entities.add(name)
        hit = EntityHit(name, ent.get("entity_type"), ent.get("description"), ent.get("source_document_id"))
        for edge in gs.neighbors(name)["edges"]:
            key = (edge["source"], edge["target"], edge.get("description"))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            hit.relations.append({"source": edge["source"], "target": edge["target"], "description": edge.get("description")})
        hits.append(hit)
    return hits


def hits_as_chunks(hits: list[EntityHit]) -> list[str]:
    """에이전트 프롬프트용 근거 문자열(엔티티 설명 다음에 그 관계)."""
    chunks: list[str] = []
    for hit in hits:
        chunks.append(f"{hit.name}: {hit.description}".strip(": ") if hit.description else hit.name)
        chunks.extend(f"{r['source']} —{r.get('description') or ''}→ {r['target']}" for r in hit.relations)
    return chunks


def search_subgraph(tenant_id: uuid.UUID | str, query: str) -> dict[str, list]:
    """Knowledge Graph 인스펙터 — 이름/설명 매칭 엔티티 + 각 1홉 이웃을 {nodes, edges}로."""
    from apps.rag.graph_store import GraphStore

    gs = GraphStore.for_search(tenant_id)  # 체험 테넌트는 도서관의 자기 예제 묶음만(#134)
    nodes_by_name: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for ent in gs.search_entities(query):
        sub = gs.neighbors(ent["name"])
        for n in sub["nodes"]:
            nodes_by_name[n["name"]] = n
        for e in sub["edges"]:
            key = (e["source"], e["target"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(e)
    return {"nodes": list(nodes_by_name.values()), "edges": edges}


# ── 쓰기(#214) — 테넌트 API·MCP 쓰기 도구가 같은 함수를 쓴다 ────────────────────


def ensure_upload_allowed(tenant_id: uuid.UUID | str) -> None:
    """체험 테넌트(#134)는 문서를 들이지 않는다. 막히면 ``try_tenant.TryRestricted``."""
    from apps.tenants import try_tenant

    try_tenant.ensure_allowed(uuid.UUID(str(tenant_id)), "upload")


def register_url_document(tenant_id: uuid.UUID | str, url: str) -> Document:
    """URL 하나를 Document로 등록하고 가져오기·인제스션을 큐에 넣는다(재귀 크롤 아님)."""
    from apps.rag.tasks import ingest_document

    doc = Document.objects.create(
        tenant_id=tenant_id,
        name=url,
        mime_type="text/html",
        source_type=Document.SOURCE_URL,
        source_url=url,
    )
    ingest_document.delay(str(doc.id), str(tenant_id), "text/html")
    return doc


def refetch_url_document(doc: Document) -> Document:
    """웹 Document를 다시 가져온다: 기존 그래프 기여분을 지우고 pending으로 돌려 다시 인제스션(교체)."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.tasks import ingest_document

    GraphStore(str(doc.tenant_id)).delete_document(str(doc.id))
    doc.status = Document.STATUS_PENDING
    doc.save()
    ingest_document.delay(str(doc.id), str(doc.tenant_id), "text/html")
    return doc


def delete_document(doc: Document) -> None:
    """지식그래프 정리(출처에서 제거 후 고아 prune, 공유 Entity 보존) 뒤 문서 행을 지운다."""
    from apps.rag.graph_store import GraphStore

    GraphStore(str(doc.tenant_id)).delete_document(str(doc.id))
    doc.delete()


def start_graph_rebuild(tenant_id: uuid.UUID | str) -> None:
    """Tenant Knowledge Graph의 Community 재구축을 큐에 넣는다(진행 중이어도 HTTP와 같이 그대로 넣는다)."""
    from apps.rag.tasks import rebuild_graph_communities

    rebuild_graph_communities.delay(str(tenant_id))


def chunk_count(doc: Document) -> int:
    from apps.rag.graph_store import GraphStore

    return len(GraphStore(str(doc.tenant_id)).query_text_units(str(doc.id)))
