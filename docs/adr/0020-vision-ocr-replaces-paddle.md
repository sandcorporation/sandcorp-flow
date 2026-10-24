# ADR-0020: OCR을 per-Tenant Vision Provider로 (Paddle는 dev/test 격하)

## Status
Accepted · **Amended 2026-10-24** — Paddle dev/test 폴백도 제거(아래 「보충」, issue #46)

## Context
문서 인제스션의 OCR(이미지 + 스캔/깨진 PDF 폴백)이 자체호스팅 PaddleOCR(GPU 서비스)에 묶여 있었다.
prod에서 GPU 컨테이너를 추가로 떠야 하고(운영·VRAM 경합 — GPU 스택 flakiness를 반복적으로 겪음),
테넌트가 자기 LLM provider의 vision 능력을 OCR에 쓸 수 없었다. 한편 langchain `ChatOpenAI`/
`ChatAnthropic`는 이미지 content block을 지원해, 기존 per-Tenant provider 추상화(ADR-0012)로
vendor별 코드 없이 vision OCR이 가능하다.

## Decision
OCR을 단일 포트(`OCRBackend.transcribe`) 뒤로 숨기고, 엔진을 두 어댑터로 둔다:
- **VisionOCR** — per-Tenant **독립** OCR(Vision) Provider(LLM-shaped: openai/anthropic/custom).
  전사 전용 프롬프트 + temperature 0(ADR-0009 가드레일)로 환각을 통제. **prod 경로.**
- **PaddleOCR** — 기존 Paddle HTTP를 같은 포트로 래핑. **dev/test 폴백 전용.**

엔진 선택은 embedding_provider와 동일한 폴백: OCR Provider 설정됨 → Vision / dev·test 미설정 →
Paddle / prod 미설정 → 에러. prod compose에서 paddle-ocr 서비스를 제거하고, dev/test compose에만 유지한다.

## Considered Options
- **Paddle을 prod에도 유지**: 기각. GPU 운영 부담·경합이 이 전환의 핵심 동기. per-Tenant 품질 선택도 불가.
- **OCR을 LLM Provider(챗)에 종속(별도 필드 없음)**: 기각. 챗 provider가 text-only일 수 있어(Embedding을 분리한 이유와 동일) 깨진다. → 독립 OCR Provider 필드(ocr_*).
- **OCR을 extraction_model처럼 같은 provider+모델만 분리**: 기각. vendor 분리 요구(챗=Claude, OCR=Gemini)가 현실적이라 독립 provider로 둔다.
- **비-LLM 전용 OCR(Google Vision·Textract·Azure DI·Mistral OCR)**: 보류. API 형태가 chat과 달라 vendor별 어댑터 필요(YAGNI — 필요 시 후속, OCRBackend 포트로 확장).
- **Paddle도 prod에서 제거하고 dev/test도 vision Fake**: 기각. dev/test에서 결정적 로컬 OCR(실제 객체)로 통합 흐름을 검증하는 가치가 큼(CLAUDE.md mocking 원칙).

## Consequences
- prod에서 GPU OCR 서비스가 사라진다(운영·VRAM 이득). 대신 OCR 비용은 테넌트의 vision provider 호출로 발생 — (1) OCR은 이미지·스캔/깨진 PDF에만 발동, (2) 테넌트가 저렴한 vision 모델 선택 가능, (3) `OCR_MAX_PAGES` 상한으로 통제.
- prod 테넌트는 이미지·스캔 PDF를 올리려면 OCR Provider를 설정해야 한다(미설정 시 명확한 에러).
- vision은 비결정 외부 경계 → 테스트는 전용 `transcribe_image` Fake로 격리하고, Paddle 통합은 dev/test 실제 객체로 검증.
- ADR-0009의 "Text Unit은 LLM 생성물 불가" 규약은 유지(픽셀 OCR은 생성물 아님 — ADR-0009 Amendment 참조).

## 보충 (2026-10-24): Paddle dev/test 폴백도 제거

### 결정
PaddleOCR을 dev/test에서도 걷어낸다. OCR 엔진은 **per-Tenant OCR(Vision) Provider 하나**이고 dev·test·prod가
같은 경로를 탄다.
- 엔진 선택: OCR Provider 설정됨 → `VisionOCR` / 미설정 → `UnconfiguredOCR`(환경 구분 없음, 플랫폼 기본 OCR 없음).
- 미설정 테넌트는 OCR이 **실제로 필요할 때만** `OCRNotConfiguredError`(설정 안내 문구)로 실패한다 — 이미지, 텍스트
  레이어가 비었거나(스캔) 깨진 PDF. TXT·Excel·웹·정상 PDF는 그대로 인제스션되고, 단어 수만 적은 정상 PDF는
  텍스트 레이어를 그대로 쓴다. (이전엔 prod에서 팩토리가 즉시 `ValueError`를 던져, OCR 미설정 테넌트는
  TXT·PDF까지 인제스션이 실패했다 — 함께 바로잡음.)
- 삭제: `paddle_service/`, compose의 `paddle-ocr`·`paddle-ocr-test` 서비스·의존·볼륨, `PADDLE_OCR_URL`/
  `PADDLE_OCR_TIMEOUT` 설정, `apps/rag/ocr.py`의 `PaddleOCR` 어댑터와 dev/test 폴백 분기.
- 비용: OCR 호출은 테넌트 키(BYOK)로 과금된다. 플랫폼 자체 테넌트(브랜딩 페이지)는 운영자가 서버 설정으로
  자기 OpenAI 키를 OCR Provider에 넣는다(값은 저장소·문서에 두지 않는다).

### 위에서 기각했던 「dev/test도 vision Fake」를 이제 채택하는 이유
- **GPU 없는 개발 호스트**: Paddle 서비스는 GPU·nvidia 런타임 전제라, GPU 없는 호스트에서 `scripts/test.sh`
  풀스택이 뜨지 못하고 OCR 시험이 실패했다(#46). CPU 대체 엔진(EasyOCR)은 2.5GB 이미지·별도 엔진 유지비가 든다.
- **운영과 같은 경로**: 운영 OCR은 이미 vision이다. dev/test만 다른 엔진을 쓰면 시험은 운영이 타지 않는
  경로(Paddle HTTP)를 검증하고, 운영 경로(OCR 설정 → VisionOCR → `transcribe_image`)는 덜 검증된다.
- **결정성은 Fake가 준다**: vision 모델은 비결정 외부 경계라 CLAUDE.md 예외 조항대로 `transcribe_image`를
  결정적 Fake로 바꾼다(conftest `vision_ocr` 픽스처). 업로드→인제스션→OCR 포트→`VisionOCR`→경계까지는 실제
  코드가 돈다. 실제 vision 전사 품질은 bring-up에서 실제 provider로 확인한다.

### 결과
- 시험 인프라에 OCR 컨테이너가 없다. GPU는 필수가 아니다(ollama는 CPU로 돌고, GPU가 있으면
  `docker-compose.test.gpu.yml`로 겹친다).
- dev에서 이미지·스캔 PDF를 시험하려면 테넌트에 OCR Provider(OpenAI 등, 또는 Custom OpenAI 호환)를 설정한다.
