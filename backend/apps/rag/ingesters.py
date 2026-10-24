from abc import ABC, abstractmethod
from typing import List


CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks


def get_embeddings(texts: List[str], provider=None) -> List[List[float]]:
    """OpenAI-호환 /v1/embeddings로 임베딩을 얻는다(ADR-0012).

    provider 미지정 시 플랫폼 기본(로컬 ollama /v1)을 쓴다. prod에선 Tenant가
    Embedding Provider를 설정해야 한다(폴백 없음 — embedding_provider 참조).
    """
    import httpx
    from django.conf import settings

    if provider is None:
        base_url = f"{settings.OLLAMA_BASE_URL}/v1"
        model = settings.OLLAMA_EMBED_MODEL
        api_key = "ollama"
    else:
        base_url, model, api_key = provider.base_url, provider.model, (provider.api_key or "x")

    resp = httpx.post(
        f"{base_url}/embeddings",
        json={"model": model, "input": texts},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=getattr(settings, "OLLAMA_TIMEOUT", 60.0),
    )
    resp.raise_for_status()
    body = resp.json()

    # 임베딩은 httpx 직접 호출이라 langchain 콜백이 못 잡는다 — 두 sink(TokenUsage DB + Langfuse
    # generation)를 응답 usage에서 수동 기록한다(LLM이 콜백으로 둘 다 다는 것과 대칭, issue 203).
    from apps.usage.context import get_usage_context
    from apps.usage.recording import record_embedding_usage
    from apps.usage.langfuse_client import record_embedding_langfuse
    ctx = get_usage_context()
    if ctx and ctx.tenant_id:
        record_embedding_usage(body, ctx.tenant_id, model)
        record_embedding_langfuse(body, ctx.tenant_id, model, texts, session_id=ctx.session_id)

    return [d["embedding"] for d in body["data"]]


class DocumentIngester(ABC):
    """문서에서 텍스트를 추출하는 인터페이스. (GraphRAG: 추출 텍스트는 GraphIngester가 그래프로 변환)

    OCR이 필요한 ingester(이미지·스캔PDF)는 주입된 `ocr`(OCRBackend 포트)로 전사한다. mime_type은
    생성자로 받는다(이미지 ingester가 vision OCR에 마임을 넘겨야 하므로 — issue 158).
    """

    def __init__(self, mime_type: str = ""):
        self.mime_type = mime_type

    @abstractmethod
    def extract_text(self, file_bytes: bytes, ocr=None) -> str:
        pass


PDF_OCR_FALLBACK_MIN_WORDS = 50


def _ocr_pdf(file_bytes: bytes, ocr) -> str:
    import fitz
    from django.conf import settings

    max_pages = getattr(settings, "OCR_MAX_PAGES", 30)
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    texts = []
    for i, page in enumerate(doc):
        if i >= max_pages:  # 거대한 스캔 PDF의 OCR 비용 통제(초과 페이지 생략)
            break
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes(output="png")
        page_text = ocr.transcribe(png_bytes, "image/png")  # 렌더 페이지는 항상 PNG
        if page_text:
            texts.append(page_text)
    return "\n".join(texts)


class PDFIngester(DocumentIngester):
    def extract_text(self, file_bytes: bytes, ocr=None) -> str:
        import fitz  # pymupdf
        from apps.rag.text_quality import is_garbled

        from apps.rag.ocr import OCRNotConfiguredError

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = "\n".join(str(page.get_text()) for page in doc)
        garbled = is_garbled(text)
        # 텍스트 레이어가 희소(스캔)하거나 폰트 인코딩이 깨져(mojibake) 추출되면 OCR로 재추출한다.
        if len(text.split()) < PDF_OCR_FALLBACK_MIN_WORDS or garbled:
            try:
                text = _ocr_pdf(file_bytes, ocr)
            except OCRNotConfiguredError:
                # OCR 미설정: 짧지만 정상인 텍스트 레이어는 그대로 쓴다. 비었거나(스캔) 깨졌으면
                # OCR 없이는 내용을 얻을 수 없으니 설정 안내 오류를 그대로 올린다.
                if garbled or not text.strip():
                    raise
        return text


class TXTIngester(DocumentIngester):
    def extract_text(self, file_bytes: bytes, ocr=None) -> str:
        return file_bytes.decode("utf-8", errors="replace")


class ImageIngester(DocumentIngester):
    def extract_text(self, file_bytes: bytes, ocr=None) -> str:
        return ocr.transcribe(file_bytes, self.mime_type or "image/png")


def _sheet_text(name: str, rows: list) -> str:
    """첫 행=헤더, 각 데이터 행을 `헤더: 값`으로 평탄화. 빈 셀은 스킵."""
    rows = [r for r in rows]
    if not rows:
        return ""
    headers = [str(h) if h not in (None, "") else f"col{i}" for i, h in enumerate(rows[0])]
    lines = [f"# {name}"]
    for row in rows[1:]:
        pairs = [
            f"{h}: {v}"
            for h, v in zip(headers, row)
            if v is not None and str(v).strip() != ""
        ]
        if pairs:
            lines.append(", ".join(pairs))
    return "\n".join(lines) if len(lines) > 1 else ""


class ExcelIngester(DocumentIngester):
    """Excel(xlsx·xls)을 시트별 헤더-키 행별 텍스트로 평탄화한다(행 단위 Entity 추출 적합)."""

    def extract_text(self, file_bytes: bytes, ocr=None) -> str:
        # 매직 바이트로 포맷 판별: xlsx=zip(PK), xls=OLE(D0CF11E0)
        if file_bytes[:2] == b"PK":
            sections = self._from_xlsx(file_bytes)
        else:
            sections = self._from_xls(file_bytes)
        return "\n\n".join(s for s in sections if s)

    def _from_xlsx(self, file_bytes: bytes) -> list:
        import io
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        return [_sheet_text(ws.title, list(ws.iter_rows(values_only=True))) for ws in wb.worksheets]

    def _from_xls(self, file_bytes: bytes) -> list:
        import xlrd

        book = xlrd.open_workbook(file_contents=file_bytes)
        sections = []
        for sheet in book.sheets():
            rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
            sections.append(_sheet_text(sheet.name, rows))
        return sections


MIME_TO_INGESTER = {
    "application/pdf": PDFIngester,
    "text/plain": TXTIngester,
    "image/png": ImageIngester,
    "image/jpeg": ImageIngester,
    "image/webp": ImageIngester,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ExcelIngester,
    "application/vnd.ms-excel": ExcelIngester,
}


def get_ingester(mime_type: str) -> DocumentIngester:
    cls = MIME_TO_INGESTER.get(mime_type)
    if not cls:
        raise ValueError(f"Unsupported mime type: {mime_type}")
    return cls(mime_type)
