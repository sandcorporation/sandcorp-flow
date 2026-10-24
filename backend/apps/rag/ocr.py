"""OCR 포트(deep module) — 이미지/스캔 PDF에서 텍스트를 전사한다.

엔진(어댑터)을 단일 인터페이스 `transcribe(image_bytes, mime_type) -> str` 뒤로 숨긴다:
- VisionOCR — per-Tenant OCR(Vision) Provider(issue 158). dev·test·prod 모두 이 경로 하나다(ADR-0020 보충).
- UnconfiguredOCR — OCR Provider 미설정. OCR이 실제로 필요할 때(이미지·스캔/깨진 PDF) 명확한 오류를 낸다.

팩토리 `get_ocr_backend(config)`가 tenant config로 엔진을 고른다. 미설정이어도 팩토리는 실패하지 않는다 —
TXT·정상 PDF처럼 OCR이 필요 없는 문서는 그대로 인제스션된다.
"""
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from apps.agent.providers import LLMProvider


class OCRBackend(Protocol):
    """엔진-무관 OCR 포트. 이미지 바이트를 받아 보이는 텍스트를 전사해 돌려준다."""

    def transcribe(self, image_bytes: bytes, mime_type: str = "image/png") -> str: ...


class OCRNotConfiguredError(ValueError):
    """OCR이 필요한 문서인데 테넌트 OCR Provider가 설정되지 않았다."""


OCR_NOT_CONFIGURED_MESSAGE = (
    "OCR Provider가 설정되지 않았습니다. 이미지·스캔 PDF를 올리려면 "
    "테넌트 설정에서 OCR(Vision) Provider를 지정하세요."
)


class UnconfiguredOCR:
    """OCR Provider 미설정 테넌트용. 전사를 요청받는 순간 명확한 오류를 낸다."""

    def transcribe(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        raise OCRNotConfiguredError(OCR_NOT_CONFIGURED_MESSAGE)


class VisionOCR:
    """per-Tenant vision 모델 어댑터. transcribe_image 경계로 이미지를 전사한다."""

    def __init__(self, provider: "LLMProvider"):
        self.provider = provider

    def transcribe(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        from apps.agent import llm

        return llm.transcribe_image(self.provider, image_bytes, mime_type)


def get_ocr_backend(config=None) -> OCRBackend:
    """tenant config로 OCR 백엔드를 고른다: OCR Provider 설정됨 → VisionOCR / 미설정 → UnconfiguredOCR.

    dev·test·prod 구분 없이 같다(플랫폼 기본 OCR 폴백 없음 — ADR-0020 보충).
    """
    if config is not None and getattr(config, "ocr_provider_type", ""):
        from apps.agent.providers import ocr_provider

        return VisionOCR(ocr_provider(config))
    return UnconfiguredOCR()
