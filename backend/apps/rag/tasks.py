import httpx

from config.celery import app

# 배치 태스크 공통 옵션 — 무중단 배포(docker rollout)로 worker가 SIGKILL돼도 유실 0.
# acks_late: 태스크 완료 후에 ack → 중간에 죽으면 재배달. reject_on_worker_lost: worker 유실 시
# 메시지를 재큐. 안전 전제는 멱등성 — 이 태스크들은 결정적 ID + GraphStore upsert라 재실행해도
# 중복되지 않는다(ADR-0015 Q4, test_ingest_to_graph_is_idempotent_on_rerun). chat 태스크는
# 반대로 acks_late=False(재배달 시 중복 응답 방지)라 여기 묶지 않는다.
_BATCH = dict(
    bind=True,
    max_retries=3,
    autoretry_for=(httpx.ReadTimeout, httpx.ConnectError),
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
)


@app.task(**_BATCH)
def ingest_document(self, document_id: str, tenant_id: str, mime_type: str):
    import os
    from django.conf import settings
    from apps.rag.ingesters import get_ingester
    from apps.rag.models import Document
    from apps.rag.graph_ingester import ingest_to_graph

    # GraphRAG 단일 인제스션: 텍스트 추출 → Knowledge Graph 구축 (벡터 청크 없음)
    doc = Document.objects.get(id=document_id)
    doc.status = Document.STATUS_PROCESSING
    doc.save()
    try:
        if doc.source_type == Document.SOURCE_URL:
            from apps.rag.web import fetch_html, extract_main_content, extract_title
            html = fetch_html(doc.source_url)
            text = extract_main_content(html)
            # Document Label 기본값: 페이지 title (없으면 URL 유지)
            if doc.name == doc.source_url:
                title = extract_title(html)
                if title:
                    doc.name = title
        else:
            from apps.rag.ocr import get_ocr_backend
            from apps.tenants.models import TenantConfig

            file_path = os.path.join(settings.MEDIA_ROOT, "documents", document_id)
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            # OCR 백엔드는 tenant config로 해석한다(설정됨=vision, 미설정=OCR이 필요할 때 명확한 오류).
            config = TenantConfig.objects.filter(tenant_id=tenant_id).first()
            ocr = get_ocr_backend(config)
            text = get_ingester(mime_type).extract_text(file_bytes, ocr)
        ingest_to_graph(text, tenant_id, document_id, doc.name)
        doc.status = Document.STATUS_READY
        doc.save()
    except Exception as e:
        doc.status = Document.STATUS_FAILED
        doc.error_message = str(e)
        doc.save()
        raise


@app.task(**_BATCH)
def rebuild_graph_communities(self, tenant_id: str):
    from apps.rag.community_builder import rebuild_communities

    rebuild_communities(tenant_id)


@app.task(**_BATCH)
def reembed_tenant_task(self, tenant_id: str):
    """Embedding Provider 변경 시 재임베딩 재구축(구조 보존, 무중단 swap)."""
    from apps.rag.reembed import reembed_tenant

    reembed_tenant(tenant_id)
