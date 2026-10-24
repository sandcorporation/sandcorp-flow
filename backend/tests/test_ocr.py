"""OCR 포트 — 엔진 주입 + 페이지 상한 (issue 157).

포트는 결정적 Fake 백엔드로 엔진-무관 행동을 검증한다(이미지/스캔PDF가 주입된 백엔드로 전사되는가,
상한이 호출 수를 통제하는가). 업로드→인제스션→VisionOCR 통합 흐름은 test_rag.py(이미지/스캔PDF 업로드,
transcribe_image Fake)가 커버한다.
"""
import fitz
import pytest


class _StubOCR:
    """주입된 OCR 백엔드가 호출되는지/몇 번 호출되는지 보는 결정적 스텁."""

    def __init__(self, text="STUB OCR TEXT"):
        self.text = text
        self.calls = 0

    def transcribe(self, image_bytes, mime_type="image/png"):
        self.calls += 1
        return self.text


def _png_bytes(text="scanned text") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=600, height=120)
    page.insert_text((20, 80), text, fontsize=28)
    return page.get_pixmap(dpi=150).tobytes(output="png")


def _image_only_pdf(n_pages: int, text="scanned page content") -> bytes:
    png = _png_bytes(text)
    out = fitz.open()
    for _ in range(n_pages):
        p = out.new_page(width=600, height=120)
        p.insert_image(p.rect, stream=png)
    return out.tobytes()


def test_image_ingester_uses_injected_ocr_backend():
    """이미지 ingester는 주입된 OCR 백엔드로 전사한다(엔진 무관 포트)."""
    from apps.rag.ingesters import get_ingester

    stub = _StubOCR("HELLO FROM OCR")
    out = get_ingester("image/png").extract_text(_png_bytes(), stub)
    assert out == "HELLO FROM OCR"
    assert stub.calls == 1


def test_pdf_scanned_fallback_uses_injected_ocr_backend():
    """텍스트 레이어 없는 스캔 PDF는 주입된 OCR 백엔드로 페이지 폴백한다."""
    from apps.rag.ingesters import get_ingester

    stub = _StubOCR("PAGE TEXT")
    out = get_ingester("application/pdf").extract_text(_image_only_pdf(2), stub)
    assert "PAGE TEXT" in out
    assert stub.calls == 2  # 2 페이지 각각 OCR


def test_pdf_ocr_fallback_respects_page_cap(settings):
    """OCR_MAX_PAGES가 거대한 스캔 PDF의 OCR 호출 수를 상한한다(비용 통제)."""
    settings.OCR_MAX_PAGES = 2
    from apps.rag.ingesters import get_ingester

    stub = _StubOCR("PAGE")
    get_ingester("application/pdf").extract_text(_image_only_pdf(5), stub)
    assert stub.calls == 2  # 5페이지지만 상한 2까지만


@pytest.mark.parametrize("platform_defaults", [True, False], ids=["dev", "prod"])
def test_get_ocr_backend_unconfigured_errors_only_when_ocr_needed(settings, platform_defaults):
    """OCR 미설정이면 dev·prod 구분 없이 폴백 엔진이 없다 — 팩토리는 통과하고(TXT 등은 인제스션 가능),
    실제 전사를 요청하는 순간 설정 안내 오류를 낸다(ADR-0020 보충)."""
    from apps.rag.ocr import get_ocr_backend, OCRNotConfiguredError

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = platform_defaults
    backend = get_ocr_backend(None)
    with pytest.raises(OCRNotConfiguredError, match="OCR Provider가 설정되지 않았습니다"):
        backend.transcribe(_png_bytes())


# ── issue 158: Vision OCR 경계 + VisionOCR + ocr_provider 리졸버 ──────────────


@pytest.mark.django_db
def test_ocr_provider_resolves_configured_vision_provider(tenant_with_key):
    """OCR Provider 설정 시 ocr_provider가 LLMProvider로 해석한다(anthropic 포함, 키 복호화)."""
    from apps.agent.providers import ocr_provider
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.ocr_provider_type = "anthropic"
    config.ocr_model = "claude-vision-x"
    config.ocr_api_key = encrypt_secret("sk-ocr-123")
    config.save()

    p = ocr_provider(config)
    assert p.type == "anthropic"
    assert p.model == "claude-vision-x"
    assert p.api_key == "sk-ocr-123"


@pytest.mark.django_db
def test_get_ocr_backend_uses_vision_when_configured(tenant_with_key):
    """OCR Provider가 설정된 테넌트는 VisionOCR 백엔드를 받는다."""
    from apps.rag.ocr import get_ocr_backend, VisionOCR
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.ocr_provider_type = "openai"
    config.ocr_model = "gpt-4o"
    config.save()

    assert isinstance(get_ocr_backend(config), VisionOCR)


def test_vision_ocr_transcribes_via_boundary(monkeypatch):
    """VisionOCR.transcribe는 transcribe_image 경계를 provider+이미지+마임으로 호출한다."""
    from apps.rag.ocr import VisionOCR
    from apps.agent.providers import LLMProvider

    captured = {}
    def fake_transcribe_image(provider, image_bytes, mime_type="image/png"):
        captured.update(provider=provider, image=image_bytes, mime=mime_type)
        return "VISION TRANSCRIBED"
    monkeypatch.setattr("apps.agent.llm.transcribe_image", fake_transcribe_image)

    prov = LLMProvider(type="openai", model="gpt-4o", api_key="k")
    out = VisionOCR(prov).transcribe(b"IMGBYTES", "image/jpeg")

    assert out == "VISION TRANSCRIBED"
    assert captured["provider"] is prov
    assert captured["image"] == b"IMGBYTES"
    assert captured["mime"] == "image/jpeg"


def test_transcribe_image_builds_vision_message_with_guardrail(monkeypatch):
    """transcribe_image는 가드레일 프롬프트 + base64 이미지 블록을 만들어 클라이언트에 보낸다."""
    import base64
    from types import SimpleNamespace
    from apps.agent import llm as llm_mod
    from apps.agent.providers import LLMProvider

    captured = {}
    class _Client:
        def bind(self, **kwargs):
            captured["bound"] = kwargs
            return self
        def invoke(self, messages, **kwargs):
            captured["messages"] = messages
            return SimpleNamespace(content="OUT")
    monkeypatch.setattr(llm_mod, "build_llm_client", lambda provider: _Client())

    out = llm_mod.transcribe_image(LLMProvider(type="openai", model="gpt-4o"), b"PNGDATA", "image/png")

    assert out == "OUT"
    flat = str(captured["messages"])
    assert base64.b64encode(b"PNGDATA").decode() in flat   # base64 이미지 블록
    assert "전사" in flat                                   # 전사 가드레일 프롬프트
    assert captured.get("bound", {}).get("temperature") == 0  # 결정성(temp 0)


@pytest.mark.django_db
def test_get_ocr_backend_unconfigured_tenant_config_errors_on_transcribe(tenant_with_key):
    """OCR 미설정 TenantConfig도 같은 경로 — 전사 시 명확한 오류(Paddle 폴백 없음)."""
    from apps.rag.ocr import get_ocr_backend, OCRNotConfiguredError
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)  # ocr 미설정

    with pytest.raises(OCRNotConfiguredError):
        get_ocr_backend(config).transcribe(_png_bytes())
