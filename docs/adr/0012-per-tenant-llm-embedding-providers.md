# ADR-0012: Tenant 부담 멀티 Provider(LLM·Embedding) + per-Tenant 가변차원 인덱스 + 재임베딩 재구축

## Status
Accepted (구현은 후속 — REQUEST.md "C")

## Context
현재 모든 LLM 호출은 **플랫폼 단일 키**(`OPEN_ROUTER_API_KEY`)로 과금되고, Tenant는 `model_id` 문자열만 고른다. 임베딩은 **플랫폼 로컬 ollama bge-m3**(GPU)로 처리된다. 프로덕션 타깃은 **Oracle A1(Ampere ARM, GPU 없음)** 이라 로컬 GPU 임베딩이 불가능하고, 임베딩 비용을 플랫폼이 흡수하기도 부담스럽다. 목표는 **Tenant가 자기 LLM 비용을 부담**하는 것.

임베딩 벡터는 **임베딩 모델이 정의한 벡터 공간**에 살며, 유사도 검색은 질의·저장 벡터가 같은 공간일 때만 동작한다. 트리거는 "차원 변경"이 아니라 **임베딩 모델 정체성 변경**이다(같은 1024라도 다른 모델이면 다른 공간 → 검색이 조용히 깨짐).

## Decision
**per-Tenant 멀티 Provider를 도입하고, Tenant가 자기 키로 비용을 부담한다.**

- **독립 두 Provider**: LLM provider(챗+추출)와 Embedding provider는 **별개 설정**. Anthropic은 임베딩 API가 없으므로 강제로 분리된다(Claude 챗 + OpenAI/Custom 임베딩).
- **Provider 타입 선택**: `{type, base_url(custom만), api_key, model}`.
  - LLM 타입: **OpenAI · Claude · Custom**. OpenAI/Custom → OpenAI-호환 클라이언트, **Claude → Anthropic 네이티브 클라이언트**(OpenAI-호환 아님).
  - Embedding 타입: **OpenAI · Custom**만(Claude 제외 — 임베딩 없음).
  - Custom = OpenAI-호환 `base_url`+크레덴셜(OpenRouter·Deepinfra·Together·로컬 커버).
- **비용 귀속**: Tenant 키로 호출 → provider가 Tenant에게 직접 과금. 플랫폼 미터링 불필요.
- **재구축 트리거 분리**:
  - **Embedding provider 변경 → 재임베딩 재구축**(그래프 구조 보존, Text Unit·Mention 벡터만 새 모델로). LLM provider(챗/추출) 변경은 **재구축 없음**.
  - 추출 LLM 변경은 신규 문서에만 적용(기존 그래프 유지, 원하면 수동 재인제스션).
- **per-Tenant 가변차원 벡터 인덱스**: Neo4j 벡터 인덱스는 (label, property)당 하나·고정 차원이라, 단일 전역 인덱스가 섞인 차원을 담을 수 없다 → **인덱스를 per-Tenant로 격리**(구현은 per-Tenant 라벨/프로퍼티).
- **무중단 swap 재구축**: 옛 인덱스/벡터가 서빙되는 동안 새 인덱스에 재임베딩 → 원자적 swap → 옛것 폐기. `Graph Freshness=rebuilding`. 비동기(Celery). dual-write 전환 패턴 재사용.
- **키 보안**: 저장 시 암호화(Fernet 등), API write-only(조회 시 마스킹). A1의 TENANT_KEY(HMAC용)와 별개.
- **프로덕션 기본 폴백 없음**: Oracle A1엔 플랫폼 기본 임베딩이 없으므로 Tenant가 Embedding provider를 설정해야 인제스션·검색 가능 → **온보딩 필수 단계**. dev는 Custom 타입 기본을 로컬 컨테이너(fake-llm + ollama, OpenAI-호환)로 향하게 해 키 없이 동작.

## Considered Options
- **플랫폼 흡수 로컬 임베딩 유지**: 기각. Oracle A1엔 GPU 없음 + 비용 부담.
- **임베딩 모델 핀 고정(bge-m3) + Tenant 키**: 기각. provider별 임베딩 모델이 제각각이라 모두에게 같은 임베딩 키를 강요하는 게 부자연스럽고, Tenant의 자유 선택을 막는다.
- **단일 전역 벡터 인덱스 유지**: 기각. 섞인 차원을 담을 수 없다.
- **다운타임 재구축**: 기각. 재구축 중 Local Search 불능.
- **전부 OpenAI-호환 통일(Claude도 게이트웨이 경유)**: 기각. Tenant가 가진 raw Anthropic 키를 네이티브 클라이언트로 존중하는 게 맞다.

## Consequences
- **LLM 경계 리팩터**: 전역 settings 대신 per-Tenant provider 설정(타입·base_url·키·model)을 받아 OpenAI vs Anthropic 클라이언트로 분기. 챗·추출·메모리 추출 호출 전부에 걸침.
- **`get_embeddings`**: ollama 네이티브 `/api/embed` → OpenAI-호환 `/v1/embeddings`, provider-설정 가능.
- **벡터 인덱스 모델**: 전역 → per-Tenant. search/ingest가 Tenant 인덱스를 타깃.
- **재구축 플로우(Celery)**: Embedding provider 변경 시 재임베딩 + 무중단 swap.
- **온보딩**: prod에선 provider 설정이 Tenant 필수(폴백 없음).
- **플랫폼 기본 폴백 게이트(통합)**: 플랫폼 기본 Provider(OpenRouter LLM + ollama 임베딩) 폴백을 단일 플래그 `PLATFORM_DEFAULT_PROVIDERS_ENABLED`로 묶어 **dev만 True / prod False**(`dev.py`/`prod.py` 명시). 미설정 + 플래그 off면 LLM·임베딩 둘 다 `ValueError`로 거부(기존 임베딩 전용 게이트를 LLM까지 확장). 서버가 이 플래그를 config GET에 노출해 **어드민 ConfigTab이 prod에선 "기본" Provider 옵션을 숨긴다**(UI 게이팅 + 백엔드 거부 이중).
  - 보충(#41, ADR-0026): prod에서 Provider가 없는 테넌트는 거부 대신 **체험 한도 안에서만** 플랫폼 OpenAI 키(`PLATFORM_OPENAI_API_KEY`)로 돈다. 이 플래그가 True인 dev는 그 경로를 타지 않는다.
- `TenantConfig.model_id` → LLM provider의 `chat_model`로 흡수.
- A1의 레이트리밋(A1-Q7)이 이제 **Tenant 키를 공개 URL 남용으로부터 보호**하는 역할도 겸한다.
