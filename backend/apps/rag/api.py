import os
import uuid
from typing import List, Optional
from ninja import Router, Schema, File, Form, UploadedFile
from ninja.errors import HttpError
from django.conf import settings
from django.shortcuts import get_object_or_404
from apps.tenants.auth import member_auth
from apps.rag.models import Document
from apps.rag import services
from apps.rag.ingesters import MIME_TO_INGESTER

rag_router = Router(tags=["rag"], auth=member_auth)


class DocumentOut(Schema):
    id: str
    name: str
    status: str
    error_message: str


def _refuse_try_upload(tenant_id: uuid.UUID) -> None:
    """체험 테넌트(#134)는 문서를 들이지 않는다 — 지식은 체험 도서관의 예제 묶음만."""
    from apps.tenants import try_tenant

    try:
        try_tenant.ensure_allowed(tenant_id, "upload")
    except try_tenant.TryRestricted as exc:
        raise HttpError(403, f"{exc.code}: 체험에서는 문서를 올릴 수 없습니다.") from exc


class UrlsIn(Schema):
    urls: List[str]


@rag_router.post("/", response={201: DocumentOut})
def upload_document(request, file: UploadedFile = File(...), name: str = Form(None)):  # pyright: ignore[reportCallIssue]
    tenant = request.auth.tenant
    _refuse_try_upload(tenant.id)
    mime_type = file.content_type or "text/plain"

    if mime_type not in MIME_TO_INGESTER:
        raise HttpError(400, f"Unsupported file type: {mime_type}")

    label = (name or "").strip() or file.name
    doc = Document.objects.create(
        tenant_id=tenant.id,
        name=label,
        mime_type=mime_type,
    )

    os.makedirs(os.path.join(settings.MEDIA_ROOT, "documents"), exist_ok=True)
    file_path = os.path.join(settings.MEDIA_ROOT, "documents", str(doc.id))
    with open(file_path, "wb") as f:
        for chunk in file.chunks():
            f.write(chunk)

    from apps.rag.tasks import ingest_document
    ingest_document.delay(str(doc.id), str(tenant.id), mime_type)

    return 201, {
        "id": str(doc.id),
        "name": doc.name,
        "status": doc.status,
        "error_message": doc.error_message,
    }


@rag_router.post("/url", response={201: List[DocumentOut]})
def add_url_documents(request, body: UrlsIn):
    """명시적 URL(들)을 각각 Document로 등록하고 fetch·인제스션을 트리거한다(재귀 크롤 아님)."""
    tenant = request.auth.tenant
    _refuse_try_upload(tenant.id)
    out = []
    for raw in body.urls:
        url = raw.strip()
        if not url:
            continue
        doc = services.register_url_document(tenant.id, url)
        out.append({
            "id": str(doc.id), "name": doc.name,
            "status": doc.status, "error_message": doc.error_message,
        })
    return 201, out


@rag_router.post("/{document_id}/refetch", response={200: DocumentOut})
def refetch_url_document(request, document_id: str):
    """웹 Document를 수동 재-fetch한다: 기존 그래프 기여분을 지우고 다시 인제스션(교체)."""
    tenant = request.auth.tenant
    doc = get_object_or_404(Document, id=document_id, tenant_id=tenant.id)
    if doc.source_type != Document.SOURCE_URL:
        raise HttpError(400, "URL Document만 재-fetch할 수 있습니다")

    services.refetch_url_document(doc)
    return 200, {
        "id": str(doc.id), "name": doc.name,
        "status": doc.status, "error_message": doc.error_message,
    }


@rag_router.get("/", response=List[DocumentOut])
def list_documents(request):
    docs = services.list_documents(request.auth.tenant.id)
    return [
        {"id": str(d.id), "name": d.name, "status": d.status, "error_message": d.error_message}
        for d in docs
    ]


class GraphNode(Schema):
    name: str
    entity_type: Optional[str] = None
    description: Optional[str] = None
    source_document_id: Optional[str] = None


class GraphEdge(Schema):
    source: str
    target: str
    description: Optional[str] = None


class GraphOut(Schema):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class GraphStatusOut(Schema):
    freshness: str


class RebuildOut(Schema):
    status: str


@rag_router.get("/graph/search", response=GraphOut)
def graph_search(request, q: str):
    """Knowledge Graph 인스펙터 — 이름/설명 매칭 엔티티 + 각 1홉 이웃을 {nodes, edges}로 반환."""
    return services.search_subgraph(request.auth.tenant.id, q)


@rag_router.get("/graph/neighbors", response=GraphOut)
def graph_neighbors(request, entity: str):
    """선택 엔티티의 1홉 이웃 서브그래프 {nodes, edges} (노드 클릭 확장용)."""
    from apps.rag.graph_store import GraphStore

    tenant = request.auth.tenant
    return GraphStore(str(tenant.id)).neighbors(entity)


class DocumentPatchIn(Schema):
    name: str


@rag_router.patch("/{document_id}", response=DocumentOut)
def update_document(request, document_id: str, body: DocumentPatchIn):
    tenant = request.auth.tenant
    doc = get_object_or_404(Document, id=document_id, tenant_id=tenant.id)

    name = body.name.strip()
    if not name:
        raise HttpError(400, "name must not be empty")

    from apps.rag.graph_store import GraphStore

    doc.name = name
    doc.error_message = ""
    doc.save()

    # GraphRAG: 레이블 변경은 대표 Entity 재시드 + 그래프 stale (청크 재임베딩 없음)
    GraphStore(str(tenant.id)).reseed_document_label(str(doc.id), name)

    return {
        "id": str(doc.id),
        "name": doc.name,
        "status": doc.status,
        "error_message": doc.error_message,
    }


@rag_router.delete("/{document_id}", response={204: None})
def delete_document(request, document_id: str):
    tenant = request.auth.tenant
    doc = get_object_or_404(Document, id=document_id, tenant_id=tenant.id)
    services.delete_document(doc)
    return 204, None


class ChunkOut(Schema):
    chunk_index: int
    content: str


@rag_router.get("/graph/status", response=GraphStatusOut)
def graph_status(request):
    """Tenant Knowledge Graph의 신선도(fresh/stale/rebuilding)를 반환한다."""
    return {"freshness": services.graph_freshness(request.auth.tenant.id)}


@rag_router.post("/graph/rebuild", response={202: RebuildOut})
def rebuild_graph(request):
    """Tenant Knowledge Graph의 Community 재구축을 트리거한다 (어드민 수동)."""
    services.start_graph_rebuild(request.auth.tenant.id)
    return 202, {"status": "rebuilding"}


@rag_router.get("/{document_id}/chunks", response=List[ChunkOut])
def list_chunks(request, document_id: str):
    """청크 인스펙터 — Knowledge Graph의 Text Unit을 보여준다 (GraphRAG)."""
    from apps.rag.graph_store import GraphStore

    tenant = request.auth.tenant
    get_object_or_404(Document, id=document_id, tenant_id=tenant.id)
    units = GraphStore(str(tenant.id)).query_text_units(document_id)
    return [{"chunk_index": u["chunk_index"], "content": u["content"]} for u in units]
