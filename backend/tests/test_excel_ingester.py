# pyright: reportArgumentType=false
import io

import pytest


def _xlsx(sheets: dict) -> bytes:
    """{시트명: [행, ...]} → xlsx bytes."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Issue 90: ExcelIngester — 헤더-키 행별 텍스트 ─────────────────────────────

def test_excel_header_keyed_rows():
    """첫 행을 헤더로, 각 데이터 행을 `헤더: 값` 쌍으로 평탄화한다."""
    from apps.rag.ingesters import ExcelIngester

    data = _xlsx({"Products": [
        ["Product", "Type", "Power"],
        ["FCB1010", "foot controller", "9V"],
        ["ZX900", "pedal", "12V"],
    ]})
    text = ExcelIngester().extract_text(data)

    assert "Product: FCB1010" in text
    assert "Type: foot controller" in text
    assert "Power: 9V" in text
    assert "ZX900" in text
    assert "Products" in text  # 시트명이 섹션으로


def test_excel_multi_sheet_and_empty_cells():
    """여러 시트는 섹션으로 구분되고, 빈 셀은 해당 키를 건너뛴다."""
    from apps.rag.ingesters import ExcelIngester

    data = _xlsx({
        "Specs": [["Name", "Color"], ["A", None]],   # Color 빈 셀 → 스킵
        "Prices": [["Item", "Cost"], ["B", "100"]],
    })
    text = ExcelIngester().extract_text(data)

    assert "Specs" in text and "Prices" in text
    assert "Name: A" in text
    assert "Color:" not in text  # 빈 셀 키는 들어가지 않음
    assert "Item: B" in text and "Cost: 100" in text


@pytest.mark.django_db
def test_xlsx_upload_ingested_to_graph(client, tenant_admin_token, tenant_with_key):
    """.xlsx 업로드가 ExcelIngester를 거쳐 그래프 인제스션까지 도달한다(status ready)."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.rag.models import Document

    data = _xlsx({"Catalog": [["Product", "Type"], ["FCB1010", "MIDI foot controller"]]})
    upload = SimpleUploadedFile(
        "catalog.xlsx",
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp = client.post(
        "/api/tenant/documents/",
        {"file": upload},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201

    doc = Document.objects.get(id=resp.json()["id"])
    assert doc.status == Document.STATUS_READY, doc.error_message
