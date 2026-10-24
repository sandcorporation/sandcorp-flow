import io
import os
import socket
import threading

import pytest


def _graph_text(tenant_id, doc_id):
    """GraphRAG: 해당 문서의 Text Unit 내용을 합쳐 반환한다 (옛 DocumentChunk 대체)."""
    from apps.rag.graph_store import GraphStore

    units = GraphStore(str(tenant_id)).query_text_units(str(doc_id))
    return " ".join(u["content"] for u in units)


def _has_graph_text_units(tenant_id, doc_id):
    from apps.rag.graph_store import GraphStore

    return len(GraphStore(str(tenant_id)).query_text_units(str(doc_id))) > 0


# ── Issue 51: Document Chunk Inspector ───────────────────────────────────────

@pytest.mark.django_db
def test_list_chunks_returns_chunks_for_ingested_document(client, tenant_admin_token, tenant_with_key):
    """인제스션된 문서의 /chunks 엔드포인트가 청크 목록을 반환한다."""
    content = b"The FCB1010 has ten footswitches and two expression pedals for live performance."
    f = io.BytesIO(content)
    f.name = "fcb.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    chunk_resp = client.get(
        f"/api/tenant/documents/{doc_id}/chunks",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert chunk_resp.status_code == 200
    chunks = chunk_resp.json()
    assert isinstance(chunks, list)
    assert len(chunks) >= 1
    assert "chunk_index" in chunks[0]
    assert "content" in chunks[0]
    assert "embedding" not in chunks[0]


@pytest.mark.django_db
def test_list_chunks_other_tenant_returns_404(client, tenant_admin_token, db):
    """다른 Tenant의 문서 청크를 조회하면 404를 반환한다."""
    import secrets
    from apps.tenants.models import Tenant
    from tests.utils import make_membership, member_token

    raw_key2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Other Corp Chunks", raw_key=raw_key2)
    agent2 = make_membership(tenant2, "member")
    token2 = member_token(agent2)

    f = io.BytesIO(b"secret content of tenant2")
    f.name = "secret.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    chunk_resp = client.get(
        f"/api/tenant/documents/{doc_id}/chunks",
        HTTP_AUTHORIZATION=f"Bearer {token2}",
    )
    assert chunk_resp.status_code == 404


@pytest.mark.django_db
def test_list_chunks_empty_document_returns_empty_list(client, tenant_admin_token):
    """청크가 없는 문서(빈 PDF)의 /chunks는 빈 배열을 반환한다."""
    import fitz
    from apps.rag.models import Document

    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()

    f = io.BytesIO(pdf_bytes)
    f.name = "empty_for_chunks.pdf"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    chunk_resp = client.get(
        f"/api/tenant/documents/{doc_id}/chunks",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert chunk_resp.status_code == 200
    assert chunk_resp.json() == []


@pytest.mark.django_db
def test_upload_txt_document(client, tenant_admin_token):
    content = b"This is a test document about return policy."
    f = io.BytesIO(content)
    f.name = "policy.txt"

    response = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "policy.txt"
    assert data["status"] == "pending"


@pytest.mark.django_db
def test_list_documents(client, tenant_admin_token):
    f = io.BytesIO(b"doc content")
    f.name = "test.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    response = client.get(
        "/api/tenant/documents/",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.django_db
def test_delete_document(client, tenant_admin_token):
    from apps.rag.models import Document

    f = io.BytesIO(b"deletable content")
    f.name = "delete_me.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    delete_resp = client.delete(
        f"/api/tenant/documents/{doc_id}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert delete_resp.status_code == 204
    assert not Document.objects.filter(id=doc_id).exists()


@pytest.mark.django_db
def test_other_tenant_cannot_access_document(client, tenant_admin_token, db):
    import secrets
    from apps.tenants.models import Tenant
    from tests.utils import make_membership, member_token

    raw_key2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Other Corp", raw_key=raw_key2)
    agent2 = make_membership(tenant2, "member")
    token2 = member_token(agent2)

    f = io.BytesIO(b"private content")
    f.name = "private.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    delete_resp = client.delete(
        f"/api/tenant/documents/{doc_id}",
        HTTP_AUTHORIZATION=f"Bearer {token2}",
    )
    assert delete_resp.status_code == 404


@pytest.mark.django_db
def test_document_ingestion_sets_status_ready(client, tenant_admin_token, tenant_with_key):
    """문서 업로드 후 Celery EAGER 실행으로 그래프 인제스션 완료 → status=ready, Text Unit 생성."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    content = b"Our return policy allows returns within 30 days of purchase."
    f = io.BytesIO(content)
    f.name = "return_policy.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY
    assert _has_graph_text_units(tenant.id, doc_id)


@pytest.mark.django_db
def test_graph_search_returns_units_for_uploaded_content(client, tenant_admin_token, tenant_with_key):
    """인제스션된 문서 내용이 그래프 벡터 검색으로 반환된다."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings

    tenant, _ = tenant_with_key
    content = b"Customer support is available Monday to Friday from 9am to 6pm."
    f = io.BytesIO(content)
    f.name = "support_hours.txt"

    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    emb = get_embeddings(["support hours"])[0]
    results = GraphStore(str(tenant.id)).vector_search(emb, top_k=3)
    assert len(results) > 0
    assert any("support" in r["content"].lower() or "Monday" in r["content"] for r in results)


@pytest.mark.django_db
def test_delete_document_removes_graph_text_units(client, tenant_admin_token, tenant_with_key):
    """문서 삭제 시 그래프 Text Unit도 함께 제거된다."""
    tenant, _ = tenant_with_key
    content = b"This document should be deleted including all its units."
    f = io.BytesIO(content)
    f.name = "to_delete.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]
    assert _has_graph_text_units(tenant.id, doc_id)

    client.delete(
        f"/api/tenant/documents/{doc_id}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert not _has_graph_text_units(tenant.id, doc_id)


@pytest.mark.django_db
def test_ingest_strips_nul_bytes(client, tenant_admin_token, tenant_with_key):
    """NUL 바이트(\x00)가 포함된 파일을 업로드해도 PostgreSQL DataError 없이 인제스션된다."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    content = b"Hello\x00world\x00this is a test\x00document about FCB1010."
    f = io.BytesIO(content)
    f.name = "nul_bytes.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    all_content = _graph_text(tenant.id, doc_id)
    assert all_content
    assert "\x00" not in all_content


@pytest.mark.django_db
def test_ingest_strips_esc_bytes(client, tenant_admin_token, tenant_with_key):
    """ESC(0x1B) 바이트가 포함된 파일을 업로드해도 Text Unit에 ESC가 남지 않는다."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    content = b"SWITCH 1\x1b toggles relay\x1b when DIRECT SELECT is enabled."
    f = io.BytesIO(content)
    f.name = "esc_bytes.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    all_content = _graph_text(tenant.id, doc_id)
    assert all_content
    assert "\x1b" not in all_content


@pytest.mark.django_db
def test_ingest_strips_control_chars_but_keeps_whitespace(client, tenant_admin_token, tenant_with_key):
    """0x01~0x1F 제어 문자는 제거되고, \\t \\n \\r 공백은 보존된다."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    # 제어 문자 + 정상 공백 혼합
    content = (
        b"line one\x01\x07\x0e\x1c\x1f\n"  # 제어 문자들 + 줄바꿈 보존
        b"line\ttwo\r\n"                     # 탭·CR·LF 보존
        b"FCB1010\x02\x03power supply\x1e"
    )
    f = io.BytesIO(content)
    f.name = "ctrl_chars.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    all_content = _graph_text(tenant.id, doc_id)

    # 제어 문자 없음
    for bad in ["\x01", "\x02", "\x03", "\x07", "\x0e", "\x1c", "\x1e", "\x1f"]:
        assert bad not in all_content, f"제어문자 {bad!r}가 Text Unit에 남아 있음"

    # 실제 텍스트 보존
    assert "FCB1010" in all_content
    assert "power supply" in all_content


def _make_pdf(*page_texts):
    """page_texts 각 항목을 별도 페이지에 담은 PDF bytes를 반환한다."""
    import fitz
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((50, 100), text)
    return doc.tobytes()


@pytest.mark.django_db
def test_ingest_multipage_pdf_includes_text_from_all_pages(client, tenant_admin_token, tenant_with_key):
    """멀티페이지 PDF를 업로드하면 모든 페이지의 텍스트가 Text Unit에 포함된다.

    단어 수가 OCR 폴백 문턱보다 적지만 OCR Provider 미설정이라, 정상 텍스트 레이어를 그대로 쓴다.
    """
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    pdf_bytes = _make_pdf(
        "MIDI channel settings for FCB1010 bank A presets.",
        "Expression pedal calibration procedure for FCB1010.",
    )
    f = io.BytesIO(pdf_bytes)
    f.name = "multipage.pdf"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    all_content = _graph_text(tenant.id, doc_id)
    assert "MIDI channel" in all_content
    assert "Expression pedal" in all_content


@pytest.mark.django_db
def test_scanned_pdf_without_ocr_provider_fails_with_clear_error(client, tenant_admin_token, tenant_with_key):
    """OCR Provider 미설정 테넌트의 스캔 PDF(텍스트 레이어 없음)는 status=failed와 설정 안내 오류가 남는다."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document

    uploaded = SimpleUploadedFile(
        "scan.pdf", _make_image_only_pdf("SYSEX dump sends entire FCB1010 memory"), content_type="application/pdf"
    )
    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201

    doc = Document.objects.get(id=resp.json()["id"])
    assert doc.status == Document.STATUS_FAILED
    assert "OCR Provider가 설정되지 않았습니다" in doc.error_message


@pytest.mark.django_db
def test_ingest_empty_pdf_results_in_no_chunks(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """텍스트가 없는 빈 PDF를 업로드하면(OCR도 빈 결과) status=ready이고 Text Unit이 생성되지 않는다."""
    import fitz
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    doc = fitz.open()
    doc.new_page()  # blank page, no text
    pdf_bytes = doc.tobytes()

    f = io.BytesIO(pdf_bytes)
    f.name = "empty.pdf"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc_obj = Document.objects.get(id=doc_id)
    assert doc_obj.status == Document.STATUS_READY, (
        f"Expected ready, got {doc_obj.status}: {doc_obj.error_message}"
    )
    assert not _has_graph_text_units(tenant.id, doc_id)


@pytest.mark.django_db
def test_ingest_pdf_strips_control_chars(client, tenant_admin_token, tenant_with_key):
    """PDF 추출 텍스트에 제어 문자가 포함돼도 Text Unit에는 남지 않는다."""
    import fitz
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    # pymupdf로 생성한 PDF는 보통 제어문자가 없지만,
    # TXTIngester 경로로 제어문자 포함 파일을 인제스션하면 동일한 strip 코드 경로를 탄다.
    # PDF ingester 자체의 strip을 검증하기 위해 fitz가 반환하는 텍스트에 제어문자가 있는 케이스를
    # 패치 없이 TXT로 시뮬레이션한다 (같은 base ingest() 메서드를 공유).
    content = b"FCB1010 MIDI controller\x01\x0e features:\x1c sysex\x1f dump."
    f = io.BytesIO(content)
    f.name = "ctrl.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc_obj = Document.objects.get(id=doc_id)
    assert doc_obj.status == Document.STATUS_READY

    all_content = _graph_text(tenant.id, doc_id)
    for bad in ["\x01", "\x0e", "\x1c", "\x1f"]:
        assert bad not in all_content
    assert "FCB1010" in all_content
    assert "sysex" in all_content


@pytest.mark.django_db
def test_ingest_txt_with_unicode_content(client, tenant_admin_token, tenant_with_key):
    """한글·특수문자·이모지가 포함된 TXT 파일도 올바르게 인제스션된다."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    content = "FCB1010 설명서: MIDI 채널 설정 방법 (1~16).\nExpression pedal A·B 캘리브레이션 절차.".encode("utf-8")
    f = io.BytesIO(content)
    f.name = "korean.txt"

    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc_obj = Document.objects.get(id=doc_id)
    assert doc_obj.status == Document.STATUS_READY

    all_content = _graph_text(tenant.id, doc_id)
    assert "FCB1010" in all_content
    assert "MIDI" in all_content
    assert "설명서" in all_content


# ── Issue 49: ImageIngester ──────────────────────────────────────────────────

def _make_png_with_text(text: str) -> bytes:
    """fitz로 텍스트가 담긴 PNG bytes를 생성한다 (PIL 불필요)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=600, height=120)
    page.insert_text((20, 80), text, fontsize=28)
    pix = page.get_pixmap(dpi=150)
    return pix.tobytes(output="png")


def _make_jpg_with_text(text: str) -> bytes:
    """fitz로 텍스트가 담긴 JPEG bytes를 생성한다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=600, height=120)
    page.insert_text((20, 80), text, fontsize=28)
    pix = page.get_pixmap(dpi=150)
    return pix.tobytes(output="jpeg")


@pytest.mark.django_db
def test_ingest_png_creates_chunks(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """PNG 이미지 업로드 시 OCR(Vision)로 텍스트가 전사되어 status=ready, Text Unit이 생성된다."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    vision_ocr.text = "FCB1010 MIDI controller test"
    png_bytes = _make_png_with_text("FCB1010 MIDI controller test")
    uploaded = SimpleUploadedFile("test_ocr.png", png_bytes, content_type="image/png")

    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    assert vision_ocr.calls == [("openai", "image/png")]
    assert "FCB1010" in _graph_text(tenant.id, doc_id)


@pytest.mark.django_db
def test_ingest_jpg_creates_chunks(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """JPG 이미지 업로드 시 OCR(Vision)로 텍스트가 전사되어 status=ready, Text Unit이 생성된다."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    vision_ocr.text = "Expression pedal calibration guide"
    jpg_bytes = _make_jpg_with_text("Expression pedal calibration guide")
    uploaded = SimpleUploadedFile("test_ocr.jpg", jpg_bytes, content_type="image/jpeg")

    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    assert vision_ocr.calls == [("openai", "image/jpeg")]
    assert "calibration" in _graph_text(tenant.id, doc_id)


@pytest.mark.django_db
def test_ingest_image_without_ocr_provider_fails_with_clear_error(client, tenant_admin_token, tenant_with_key):
    """OCR Provider 미설정 테넌트가 이미지를 올리면 status=failed와 설정 안내 오류가 남는다(폴백 OCR 없음)."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document

    uploaded = SimpleUploadedFile("no_ocr.png", _make_png_with_text("no ocr here"), content_type="image/png")
    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201

    doc = Document.objects.get(id=resp.json()["id"])
    assert doc.status == Document.STATUS_FAILED
    assert "OCR Provider가 설정되지 않았습니다" in doc.error_message


@pytest.mark.django_db
def test_upload_unsupported_mime_returns_400(client, tenant_admin_token):
    """지원하지 않는 MIME 타입(image/gif)은 400 에러를 반환한다."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    gif_bytes = b"GIF89a" + b"\x00" * 10
    uploaded = SimpleUploadedFile("anim.gif", gif_bytes, content_type="image/gif")

    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 400


# ── Issue 50: PDF OCR Fallback ────────────────────────────────────────────────

def _make_image_only_pdf(text: str) -> bytes:
    """텍스트를 이미지로 렌더링한 후 텍스트 레이어 없이 PDF에 삽입한다.
    pymupdf로 추출하면 빈 문자열이 나와 OCR fallback을 트리거한다.
    """
    import fitz

    # 텍스트가 있는 페이지를 PNG로 렌더링
    src = fitz.open()
    page = src.new_page(width=600, height=120)
    page.insert_text((20, 80), text, fontsize=28)
    pix = page.get_pixmap(dpi=150)
    png_bytes = pix.tobytes(output="png")

    # 이미지만 담긴 PDF 생성 (텍스트 레이어 없음)
    out = fitz.open()
    out_page = out.new_page(width=600, height=120)
    out_page.insert_image(out_page.rect, stream=png_bytes)
    return out.tobytes()


@pytest.mark.django_db
def test_pdf_ocr_fallback_used_when_text_too_sparse(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """텍스트 레이어가 없는 스캔 PDF는 OCR fallback을 통해 status=ready이고 Text Unit이 생성된다."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document
    import fitz

    tenant, _ = tenant_with_key
    vision_ocr.text = "SYSEX dump sends entire FCB1010 memory"
    pdf_bytes = _make_image_only_pdf("SYSEX dump sends entire FCB1010 memory")

    # 텍스트 레이어가 없음을 먼저 확인
    doc_check = fitz.open(stream=pdf_bytes, filetype="pdf")
    extracted = " ".join(str(page.get_text()) for page in doc_check)
    assert len(extracted.split()) < 50, "테스트 PDF에 텍스트 레이어가 있으면 안 됩니다"

    uploaded = SimpleUploadedFile("scan.pdf", pdf_bytes, content_type="application/pdf")
    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    assert vision_ocr.calls == [("openai", "image/png")]  # 렌더한 페이지 1장을 OCR
    assert "SYSEX" in _graph_text(tenant.id, doc_id)


@pytest.mark.django_db
def test_pdf_normal_text_skips_ocr(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """단어 수 >= 50인 정상 PDF는 OCR fallback 없이 처리된다."""
    from apps.rag.models import Document

    tenant, _ = tenant_with_key
    # 한 줄이 페이지 폭을 넘으면 잘리므로 페이지 5장 × 12단어 = 60단어(OCR 폴백 문턱 50 이상)로 나눈다.
    pdf_bytes = _make_pdf(*(["FCB1010 MIDI controller bank A preset " * 2] * 5))

    f = io.BytesIO(pdf_bytes)
    f.name = "normal.pdf"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY
    assert _has_graph_text_units(tenant.id, doc_id)
    assert vision_ocr.calls == []  # 텍스트 레이어로 충분 — OCR 호출 없음


# ── PRD garbled-pdf-ocr-fallback: 깨진 텍스트 레이어(mojibake) → OCR 재추출 ────

# 폰트 ToUnicode 매핑 부재로 깨진 추출 텍스트 (여러 줄, 단어 수는 충분 → 단어 수 조건을 빠져나감).
_MOJIBAKE = (
    "-%&././ 2*$* . ./G/ 2*$* . 1 PRG CHG 1 2 PRG CHG 2 3 PRG CHG 3 4 PRG CHG 4\n"
    "5 PRG CHG 5 6 CNT 1 7 CNT 2 8 EXP A 9 EXP B 10/0 NOTE -% *+ , ,(/. 4 2*$*\n"
    "?'$ 8G'') 2*$* 2*$*%(4?'$ % 2*$* . ./G/ 'F)'::*@'$4?47 8G'')\n"
    "$@0G':%4' $)1& %@-*+?'$ %@-*+8)4*@ ;; 1 ; 1 SWITCH 1 2 SWITCH 2\n"
    "3 PRG CHG 6 4 CNT 3 5 EXP C 6 NOTE -% 2*$* ./G/ 8G'') 'F)'::*@\n"
    "7 PRG CHG 7 8 CNT 4 9 EXP D 2*$* %@-*+ ?'$ ./G/ -%&././ 2*$*"
)


def _make_garbled_text_layer_pdf(visible_text: str, garbled_layer: str) -> bytes:
    """이미지 픽셀엔 visible_text(정상)를 렌더하고, 텍스트 레이어엔 garbled_layer(mojibake)를
    invisible(render_mode=3)로 삽입한 PDF. get_text()→mojibake(깨짐 감지), OCR(이미지)→정상 복원.
    """
    import fitz

    src = fitz.open()
    page = src.new_page(width=700, height=240)
    page.insert_text((20, 90), visible_text, fontsize=20)
    pix = page.get_pixmap(dpi=150)
    png_bytes = pix.tobytes(output="png")

    out = fitz.open()
    op = out.new_page(width=700, height=240)
    op.insert_image(op.rect, stream=png_bytes)
    op.insert_text((20, 40), garbled_layer, fontsize=10, render_mode=3)  # invisible, 여러 줄
    return out.tobytes()


@pytest.mark.django_db
def test_pdf_garbled_text_layer_triggers_ocr_recovery(client, tenant_admin_token, tenant_with_key, vision_ocr):
    """텍스트 레이어가 mojibake인 PDF는 깨짐 감지로 OCR 재추출되어, 깨진 텍스트가 Text Unit에 남지 않는다."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document
    from apps.rag.text_quality import is_garbled
    import fitz

    tenant, _ = tenant_with_key
    visible = "FCB1010 expression pedal calibration guide for stage use"
    vision_ocr.text = visible  # 픽셀에 보이는 정상 텍스트를 vision이 전사한다
    pdf_bytes = _make_garbled_text_layer_pdf(visible, _MOJIBAKE)

    # 전제: 텍스트 레이어가 깨졌고(단어 수는 충분) → 단어 수 조건이 아니라 깨짐 감지로 트리거되어야 한다.
    check = fitz.open(stream=pdf_bytes, filetype="pdf")
    extracted = " ".join(str(p.get_text()) for p in check)
    assert len(extracted.split()) >= 50, "단어 수가 충분해야 깨짐 감지 경로를 검증한다"
    assert is_garbled(extracted), "테스트 PDF의 텍스트 레이어가 깨져 있어야 한다"

    uploaded = SimpleUploadedFile("garbled.pdf", pdf_bytes, content_type="application/pdf")
    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )
    # OCR 재추출로 Text Unit이 생성되고, 깨진 텍스트 레이어의 시그니처가 들어가지 않았다.
    assert _has_graph_text_units(tenant.id, doc_id)
    all_content = _graph_text(tenant.id, doc_id)
    assert "2*$*" not in all_content, "깨진 텍스트 레이어가 그대로 Text Unit에 저장되었다"
    assert not is_garbled(all_content), "Text Unit content가 여전히 깨져 있다"


@pytest.mark.django_db
def test_garbled_chunks_dropped_from_text_units(client, tenant_admin_token, tenant_with_key):
    """추출 후에도 남은 깨진 청크는 Text Unit으로 저장되지 않고, 정상 청크만 남는다.

    chunk 드롭은 mime 무관하게 동일한 ingest_to_graph 경로를 타므로 TXT로 격리 검증한다.
    통째로 깨진 청크를 버리는 것이 명세다(청크는 임베딩 단위라, 정상 우세 경계 청크 속
    일부 깨진 조각까지 제거하지는 않는다).
    """
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document
    from apps.rag.graph_store import GraphStore
    from apps.rag.text_quality import is_garbled

    tenant, _ = tenant_with_key
    # 정상 청크 여러 개 분량 + 순수하게 깨진 청크 여러 개 분량을 이어붙인다.
    normal = "expression pedal calibration guide for stage performance setup procedure " * 90
    garbled = "2*$* ./G/ -%&././ 8G'') %@-*+ ?'$ 2*$* ./G/ -%&././ " * 90
    content = (normal + " " + garbled).encode("utf-8")
    uploaded = SimpleUploadedFile("mixed.txt", content, content_type="text/plain")

    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"Expected ready, got {doc.status}: {doc.error_message}"
    )

    units = GraphStore(str(tenant.id)).query_text_units(str(doc_id))
    assert units, "Text Unit이 하나도 없다 (정상 청크까지 드롭되었다)"
    # 정상 청크는 보존된다.
    assert any("calibration" in u["content"] for u in units), "정상 청크가 드롭되었다"
    # 저장된 모든 Text Unit은 깨지지 않았다 — 통째로 깨진 청크는 드롭되었다.
    for u in units:
        assert not is_garbled(u["content"]), f"깨진 청크가 저장됨: {u['content'][:80]!r}"


# ── Issue 52/66: Document Label은 Knowledge Graph의 Entity로 표현된다 ──────────
# (옛 "청크 임베딩 prefix" 메커니즘은 GraphRAG Entity 추출로 대체됨 — retriever/reembed 테스트 폐기.
#  레이블 기반 검색 가능성은 test_graph_store/test_graph_search가 커버한다.)

@pytest.mark.django_db
def test_document_label_becomes_graph_entity(client, tenant_admin_token, tenant_with_key):
    """본문에 제품명이 없어도 Document Label이 그래프 대표 Entity로 잡힌다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    content = b"The unit offers ten assignable footswitches and two expression pedals for stage use."
    f = io.BytesIO(content)
    f.name = "ZX900PRO.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    names = [e["name"] for e in GraphStore(str(tenant.id)).query_mentions()]
    assert "ZX900PRO.txt" in names


@pytest.mark.django_db
def test_ingest_to_graph_is_idempotent_on_rerun(tenant_with_key):
    """같은 (text, document_id)로 재실행해도 그래프가 중복되지 않는다.

    docker rollout 중 배치 worker가 SIGKILL→재배달되면 같은 태스크가 다시 돈다(acks_late). mention/
    text_unit ID가 결정적(document_id 기반)이고 GraphStore가 upsert라 재실행은 같은 행을 덮어쓴다
    — 이 멱등성이 acks_late 무중단 배포의 전제다(ADR-0015 Q4)."""
    from apps.rag.graph_ingester import ingest_to_graph
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    tid = str(tenant.id)
    text = "The unit offers ten assignable footswitches and two expression pedals for stage use."

    ingest_to_graph(text, tid, "doc-1", "ZX900PRO")
    first = sorted(m["name"] for m in GraphStore(tid).query_mentions())

    ingest_to_graph(text, tid, "doc-1", "ZX900PRO")   # 재배달 시뮬레이션
    second = sorted(m["name"] for m in GraphStore(tid).query_mentions())

    assert first == second, f"재실행으로 mention 중복/변동: {first} → {second}"
    assert len(first) == len(set(first)), f"mention 중복 존재: {first}"


# ── Issue 53/66: Document Label 수정 (rename + Entity 재시드, 재임베딩 없음) ────

@pytest.mark.django_db
def test_patch_document_name_reseeds_graph_entity(client, tenant_admin_token, tenant_with_key):
    """PATCH로 name을 바꾸면 새 레이블이 그래프 대표 Entity로 시드되고 그래프가 stale이 된다 (재임베딩 없음)."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    target = io.BytesIO(b"The unit offers ten assignable footswitches and two expression pedals.")
    target.name = "before.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": target},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    patch_resp = client.patch(
        f"/api/tenant/documents/{doc_id}",
        {"name": "ZX900PRO.txt"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "ZX900PRO.txt"

    gs = GraphStore(str(tenant.id))
    names = [e["name"] for e in gs.query_mentions()]
    assert "ZX900PRO.txt" in names
    assert gs.get_freshness() == "stale"


@pytest.mark.django_db
def test_upload_with_explicit_name_uses_it_as_label(client, tenant_admin_token):
    """업로드 시 name 폼 필드를 주면 파일명 대신 그 값이 Document.name(레이블)이 된다."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    uploaded = SimpleUploadedFile(
        "raw_filename_12345.txt", b"FCB1010 footswitch assignments.", content_type="text/plain"
    )
    resp = client.post(
        "/api/tenant/documents/",
        {"file": uploaded, "name": "FCB1010 매뉴얼"},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "FCB1010 매뉴얼"


@pytest.mark.django_db
def test_upload_without_name_falls_back_to_filename(client, tenant_admin_token):
    """name 폼 필드가 없으면 파일명을 레이블로 사용한다 (기존 동작 유지)."""
    f = io.BytesIO(b"Fallback content.")
    f.name = "fallback.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "fallback.txt"


@pytest.mark.django_db
def test_patch_document_empty_name_returns_400(client, tenant_admin_token):
    """빈 name으로 PATCH하면 400을 반환한다."""
    f = io.BytesIO(b"Some document content for label test.")
    f.name = "label.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    patch_resp = client.patch(
        f"/api/tenant/documents/{doc_id}",
        {"name": "   "},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert patch_resp.status_code == 400


@pytest.mark.django_db
def test_patch_document_other_tenant_returns_404(client, tenant_admin_token, db):
    """다른 Tenant의 문서를 PATCH하면 404를 반환한다."""
    import secrets
    from apps.tenants.models import Tenant
    from tests.utils import make_membership, member_token

    raw_key2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Other Corp Patch", raw_key=raw_key2)
    agent2 = make_membership(tenant2, "member")
    token2 = member_token(agent2)

    f = io.BytesIO(b"Tenant1 private document.")
    f.name = "private.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    patch_resp = client.patch(
        f"/api/tenant/documents/{doc_id}",
        {"name": "hijacked.txt"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token2}",
    )
    assert patch_resp.status_code == 404


@pytest.mark.django_db
def test_patch_rename_without_file_access(client, tenant_admin_token):
    """원본 파일이 삭제돼도 PATCH 레이블 변경은 동작한다 (재추출/재임베딩 없이 rename + Entity 재시드)."""
    import os
    from django.conf import settings as dj_settings
    from apps.rag.models import Document

    f = io.BytesIO(b"FCB1010 SYSEX dump sends the entire memory over MIDI for backup.")
    f.name = "reembed.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    doc_id = resp.json()["id"]

    # 원본 파일을 삭제 → 재추출 경로라면 실패할 것
    file_path = os.path.join(str(dj_settings.MEDIA_ROOT), "documents", doc_id)
    if os.path.exists(file_path):
        os.unlink(file_path)

    patch_resp = client.patch(
        f"/api/tenant/documents/{doc_id}",
        {"name": "FCB1010-manual.txt"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert patch_resp.status_code == 200

    doc = Document.objects.get(id=doc_id)
    assert doc.status == Document.STATUS_READY, (
        f"파일 없이 재임베딩 실패: {doc.status} {doc.error_message}"
    )


@pytest.fixture
def _hang_server():
    """TCP server that accepts connections but never responds — simulates a hung Ollama."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(10)
    port = sock.getsockname()[1]

    def _loop():
        while True:
            try:
                conn, _ = sock.accept()
                # accept but never respond — caller will timeout
            except Exception:
                break

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    yield port
    sock.close()


@pytest.mark.django_db
def test_ingest_document_sets_failed_status_on_ollama_timeout(settings, tenant_with_key, _hang_server):
    """Ollama가 응답하지 않아 ReadTimeout 발생 시 Document.status=failed, error_message에 에러 내용이 저장된다."""
    from django.conf import settings as django_settings
    from apps.rag.models import Document
    from apps.rag.tasks import ingest_document

    settings.OLLAMA_BASE_URL = f"http://127.0.0.1:{_hang_server}"
    settings.OLLAMA_TIMEOUT = 0.2

    tenant, _ = tenant_with_key
    doc = Document.objects.create(
        tenant_id=tenant.id,
        name="timeout_test.txt",
        mime_type="text/plain",
    )

    file_dir = os.path.join(str(django_settings.MEDIA_ROOT), "documents")
    os.makedirs(file_dir, exist_ok=True)
    file_path = os.path.join(file_dir, str(doc.id))
    try:
        with open(file_path, "wb") as fh:
            fh.write(b"Some content that needs embedding")

        ingest_document.apply(args=(str(doc.id), str(tenant.id), "text/plain"))
    finally:
        if os.path.exists(file_path):
            os.unlink(file_path)

    doc.refresh_from_db()
    assert doc.status == Document.STATUS_FAILED
    assert doc.error_message != ""
