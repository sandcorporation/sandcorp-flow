# Sandcorp Flow — 상담 흐름을 편집·배포하는 멀티테넌트 챗봇 플랫폼

> **Sandcorp Flow**(`flow.sandcorp.co.kr`)는 옛 「Embed Chat」을 넓힌 셀프 가입 SaaS입니다. 테넌트가 상담 **흐름(Flow)** 을 편집·시험·배포하고, 위젯에서 방문자가 그 흐름과 대화합니다([ADR-0026](./docs/adr/0026-sandcorp-flow-saas-pivot.md)). 아래 설명은 Embed Chat 시절부터 이어진 엔진(GraphRAG·HITL·위젯·Admin) 기준입니다. 이름 변경에 따라 compose 프로젝트는 `sandcorp-flow`(dev는 `-dev`, 테스트는 `-test`, 운영은 `-prod`), GHCR 이미지는 `sandcorp-flow-{api,admin,widget}`, 기본 DB 이름은 `sandcorp_flow`입니다.

Sandcorp Flow는 타사 웹사이트에 iframe으로 삽입하는 챗봇을 제공하는 멀티테넌트 SaaS 플랫폼입니다. **Operator**(운영자)가 플랫폼을 관리하고 **Tenant**(고객사)를 생성하면, Tenant는 자기 문서·Excel·웹 URL을 올려 **지식그래프(Knowledge Graph) 기반 RAG**로 답하는 챗봇을 **공개 URL `/chatbot/{slug}/`**(토큰 발급 없이)로 자기 사이트의 **Visitor**에게 노출합니다. Tenant는 **자기 LLM·임베딩 Provider를 설정해 비용을 직접 부담**할 수 있고, **HITL(Human-in-the-Loop)** 사용 여부도 선택합니다 — 켜진 경우 AI가 불확실하거나 방문자가 상담원을 찾으면 사람 상담원에게 넘어갑니다.

용어는 [`CONTEXT.md`](./CONTEXT.md)(도메인 글로서리), 설계 결정은 [`docs/adr/`](./docs/adr/)를 참조하세요.

---

## 1. 시스템 구성

| 컴포넌트 | 역할 |
|----------|------|
| **Django API** (`/api/`) | 인증, 채팅(SSE), RAG/지식그래프, Visitor Memory, HITL, Provider 설정 |
| **Celery Worker** (배치) | 문서 인제스션(그래프 구축), 엔티티 해소(SAME_AS) 재구축, 재임베딩, 메모리 추출 |
| **taskiq worker-chat** (chat 전용) | Visitor chat 1턴 **async** 실행 — api(uvicorn) 프로세스에서 분리·격리(배치가 chat을 굶기지 않게). [ADR-0022](./docs/adr/0022-full-async-uvicorn-taskiq.md)/[0024](./docs/adr/0024-taskiq-chat-celery-batch-coexist.md) |
| **relay + 이벤트 소비자** (Event 파이프라인) | HITL/세션 라이프사이클의 **Transactional Outbox** 발행(relay 싱글톤) + 디커플링된 소비자(webhook·visitor-bridge·console-bridge·presence-bridge). → [event-driven-architecture.md](./docs/event-driven-architecture.md) |
| **Knowledge Graph** | **Postgres + pgvector**에 흡수 — Entity Mention·관계(메타데이터) + **per-Tenant 가변차원 HNSW**(Text Unit/Mention 임베딩, 차원별 vec 테이블). 과거 Neo4j에서 이전([ADR-0021](./docs/adr/0021-graphstore-pgvector-replaces-neo4j.md)) |
| **PostgreSQL** | Django 모델(Tenant·Document·ChatMessage 등) + **LangGraph Checkpoint**(대화 state) |
| **Ollama** | dev 기본 임베딩 `bge-m3`(다국어, 1024차원). prod에선 Tenant Embedding Provider가 대체 |
| **OCR** | 이미지/스캔 PDF의 텍스트 추출 — dev/test/prod 모두 **per-Tenant Vision Provider**(GPT-4o·Claude·Gemini 등, BYOK) 하나. 미설정 테넌트는 이미지·스캔 PDF 업로드가 명확한 오류로 실패 ([ADR-0020](./docs/adr/0020-vision-ocr-replaces-paddle.md)) |
| **Redis** | SSE pub/sub + **taskiq(chat)·Celery(배치) 브로커** + **EventBus(Streams)** + relay wake + 레이트리밋·세션 락 |
| **Widget** (`/chatbot/{slug}/`) | Visitor용 채팅 위젯 (React) — 토큰 없이 slug로 접근 |
| **Landing** (`/`) | 공개 소개 페이지 — 실제 `ChatWidget`을 mock 스트리밍으로 구동하는 라이브 챗봇 데모 + 지식그래프(react-force-graph) 데모 + 연락처. widget 레포의 별도 멀티페이지 엔트리(무거운 데모 의존은 landing 청크에만 → 위젯 챗봇 번들은 경량 유지) |
| **Admin UI** (`/admin-ui/`) | Operator·Tenant 관리 화면 (React + Tailwind/shadcn) — **좌측 사이드바 내비 + 자원별 URL 라우트**(ADR-0017). 문서·**지식그래프 인스펙터**·Visitors·설정(Provider 포함)·팀원·HITL·**토큰 사용량**(테넌트=자기 것/오퍼레이터=전체, recharts) 섹션 |
| **Nginx** | 리버스 프록시, 정적 파일 서빙 |

> **저장소 역할 분담**: **PostgreSQL이 관계형 데이터 + LangGraph 대화 체크포인트 + RAG 지식그래프·임베딩(pgvector)** 을 모두 담당합니다. GraphRAG의 엔티티/관계 모델은 그대로이며 저장 엔진만 Neo4j→pgvector로 이전했습니다([ADR-0021](./docs/adr/0021-graphstore-pgvector-replaces-neo4j.md)) — 단일 DB로 통합해 박스 부담을 줄이고 관리형 Postgres 이식을 단순화. (GraphRAG 채택 자체는 [ADR-0007](./docs/adr/0007-graphrag-neo4j-replaces-2step-vector-rag.md).)

> 📐 **전체 아키텍처 다이어그램**(런타임 토폴로지 · 챗 요청 흐름 · 인제스션·RAG · 배포 CI/CD)은 [docs/architecture.md](./docs/architecture.md)에 mermaid로 정리되어 있습니다.

---

## 2. 지식그래프 기반 RAG 원리

기존의 2-step 벡터 RAG(쿼리 임베딩 → 최근접 청크 → LLM)는 (a) 본문에 제품명이 없는 엔티티 질의, (b) 여러 문서를 가로지르는 멀티홉/요약 질의에 약했습니다. Sandcorp Flow는 이를 **Microsoft GraphRAG식 지식그래프**로 대체했습니다.

### 2.1 그래프 구조

Tenant마다 하나의 지식그래프가 Neo4j에 저장됩니다. 모든 노드/관계는 `tenant_id` 속성으로 격리됩니다(아래 2.5).

- **Entity Mention** — 문서에서 LLM이 추출한 개별 언급(`name`+그 문맥의 `description`+출처 Document). **같은 표기라도 맥락이 다르면 별개 노드**입니다(한강 "다리" vs 신체 "다리"). `(tenant_id, mention_id)`로 식별되며 이름으로 병합하지 **않습니다** — 정체성은 이름이 아니라 맥락이기 때문([ADR-0010](./docs/adr/0010-entity-resolution-mention-entity-context-equivalence.md)).
- **Entity (동치 클러스터)** — 같은 실세계 대상을 가리키는 Mention들이 **`SAME_AS` 비파괴 동치 엣지**로 묶인 정체. 동치는 이름 유사도가 아니라 **맥락(name+description 임베딩) 정합**으로 판별합니다 — 표기변이(`FCB1010`=`FCB-1010`)는 묶고, 동음이의는 분리. 노드를 물리적으로 합치지 않아 잘못 묶이면 엣지만 끊어 되돌립니다.
- **관계(RELATED)** — Mention 간 엣지(`description` 보유). 추출된 (subject, relation, object) 트리플에서 생성됩니다.
- **Document(레이블) Mention** — 업로드 소스를 대표하는 Mention. 그 문서에서 추출된 모든 Mention과 `mentions` 관계로 연결되어, **본문에 제품명이 없어도** 문서를 통해 내부 엔티티에 도달할 수 있습니다.
- **Text Unit** — 문서를 일정 크기로 나눈 텍스트 조각 노드. 임베딩을 가지며 Local Search의 원문 폴백 근거(citation)로 쓰입니다. citation은 추출 원문에 충실하며 LLM 정제물을 담지 않습니다.

### 2.2 인제스션 파이프라인 (업로드 → 그래프)

문서를 업로드하면 Celery 태스크가 실행됩니다:

```
업로드 → 텍스트 추출 → LLM Entity/관계 추출 → 그래프 기여 → Text Unit 임베딩 → Graph Freshness=stale
```

소스는 **파일 업로드**(PDF·TXT·이미지·**Excel xlsx/xls**)와 **웹 URL**(명시적, 재귀 크롤 아님) 두 가지입니다(Document Source로 분기).

1. **텍스트 추출**: PDF는 PyMuPDF로 추출하되 **단어 수 부족 또는 깨진 추출(Garbled Extraction, mojibake) 감지 시 OCR로 재추출**([ADR-0009](./docs/adr/0009-garbled-extraction-ocr-not-llm-cleanup.md) — LLM 정제가 아니라 픽셀 재인식). OCR 엔진은 **OCRBackend 포트** 뒤의 per-Tenant Vision Provider(전사 가드레일+temp 0) 하나다 — 미설정이면 OCR이 필요한 순간 설정 안내 오류(짧지만 정상인 PDF 텍스트 레이어는 그대로 사용)([ADR-0020](./docs/adr/0020-vision-ocr-replaces-paddle.md)). 이미지는 OCR, TXT는 그대로, **Excel은 시트별 헤더-키 행별 텍스트로 평탄화**, **웹은 URL을 fetch해 메인 콘텐츠 추출**(보일러플레이트 제거).
2. **Entity/관계 추출**: 추출 텍스트를 **추출 LLM**(Tenant Provider 또는 플랫폼 기본)에 구조화 출력으로 보내 `(entities, relations)`를 받습니다. 추출은 **해당 문서 내부만** 봅니다. 문서 레이블 Mention을 시드하고, 추출된 각 Mention을 `mentions` 관계로 연결합니다(이름 병합 없이 — 동치는 재구축 단계에서 `SAME_AS`로).
3. **임베딩**: Mention(`name+description`)과 Text Unit을 **Tenant Embedding Provider**(미설정 시 dev=ollama `bge-m3`)로 배치 임베딩해 **per-Tenant 벡터 인덱스**에 저장합니다. 인덱스 차원은 Tenant의 임베딩 모델에 맞춰지고, Tenant마다 라벨·인덱스가 격리됩니다([ADR-0012](./docs/adr/0012-per-tenant-llm-embedding-providers.md)).
4. **신선도 표시**: 그래프가 바뀌었으므로 `stale`로 표시됩니다. **Entity Resolution(SAME_AS)**은 전역 연산이라 업로드마다 돌리지 않고 **배치/트리거**로 재구축합니다([ADR-0008](./docs/adr/0008-incremental-ingest-batched-community-rebuild.md), [ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md)).

### 2.3 검색: Local + 원문(TextUnit) 폴백

채팅 그래프(LangGraph)는 항상 **Local Search**로 가고, 그래프-only로 답을 못 내면 원문으로 보강합니다([ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md) — Global Search 제거):

```
local_search ─▶ call_llm ──(context_sufficient=False)──▶ source_search ─▶ call_llm(재호출)
```

- **Local Search** — Entity 중심 근거. 질의로 resolved Entity를 찾고 그 이웃 관계를 구조화 근거로 모읍니다([ADR-0010](./docs/adr/0010-entity-resolution-mention-entity-context-equivalence.md)).
- **원문 폴백(source_search)** — LLM이 "제공된 근거로 답할 수 없다"(`context_sufficient=False`)고 표시하면, 질의 임베딩으로 최근접 **Text Unit 원문**을 `vector_search`해 근거에 보강하고 한 번 더 호출합니다(top_k 캡). 추출이 버린 스펙·수치·표를 원문으로 회복합니다([ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md)). 그래프로 답한 경우엔 호출되지 않아 토큰을 통제합니다.
- **Global Search는 제거됨** — 커뮤니티 요약이 이름-only로 공허하고 지원 봇 질의는 대부분 특정형이라, local+원문 폴백으로 대체했습니다(ADR-0016).

### 2.4 엔티티 의미 검색 (다국어 하이브리드)

지식그래프 인스펙터(어드민 "🕸️ 지식그래프" 탭)와 검색은 **하이브리드**로 동작합니다:

- **어휘(lexical)** — 이름/설명 부분일치. 정확 이름·문서 레이블 검색을 보장.
- **의미(semantic)** — `bge-m3`가 다국어라, 한↔영·동의어 질의를 임베딩 공간에서 매칭. 예: **"메뉴" → "OSD Menu"** 검색이 가능합니다.

두 결과를 이름 키로 dedup해 합칩니다. (관계는 임베딩하지 않고, 엔티티에서 이웃 확장으로 도달합니다.)

### 2.5 멀티테넌시 격리

단일 Neo4j 그래프에 모든 Tenant가 공존하되, **모든 노드/관계에 `tenant_id` 속성**을 두고 **`GraphStore` 경계 모듈**이 모든 쿼리에 `tenant_id`를 강제로 주입합니다. tenant_id 없이는 그래프에 접근할 수 없어 테넌트 간 누수를 구조적으로 막습니다. (Enterprise 멀티 DB 대신 Community + 속성 격리 — 운영 단순성 우선.)

### 2.6 그래프 신선도와 재구축

- **Graph Freshness**: `fresh`(엔티티 해소 최신) / `stale`(문서 추가·삭제로 재구축 필요) / `rebuilding`.
- 문서 추가/삭제 시 `stale`. 어드민의 **"재구축" 버튼** 또는 자동 트리거로 **Entity Resolution(SAME_AS)**을 재수행하고, 임베딩이 없는 기존 Entity도 이때 백필합니다.
- 문서 삭제 시 노드/관계의 출처 집합에서 그 문서를 제거하고, **출처가 빈 것만** prune합니다(여러 문서가 공유하는 Entity는 보존).

---

## 3. 채팅 & 대화 메모리

채팅은 LangGraph로 구현됩니다:

```
START → local_search → call_llm ─(context_sufficient=False)─→ source_search → call_llm
                           │
                           ├─(needs_hitl?)─→ create_escalation → END
                           └──────────────→ save_messages     → END
```

채팅 그래프는 **Tenant의 `hitl_enabled` 토글 + 영업시간에 따라 다르게 컴파일**됩니다(`effective_hitl = hitl_enabled AND business_hours.is_open(now)`). HITL-OFF(또는 상담시간 외)면 `call_llm`이 `needs_hitl` 필드 없는 response-only 스키마를 쓰고 `save_messages`로 직행해, escalation 분기 자체가 없습니다(지키지 못할 전환 멘트 누수를 구조적으로 차단).

- **실행 격리**: 런타임은 전면 async입니다([ADR-0022](./docs/adr/0022-full-async-uvicorn-taskiq.md)) — 웹은 **uvicorn ASGI**, chat 1턴은 **전용 taskiq `worker-chat`**에서 async로 실행되어 블로킹이 SSE 서빙을 얼리지 않습니다(gevent 블로킹-허브 병 원천 제거). 노드가 async라 한 이벤트루프가 동시 다수 chat을 IO 대기 중 yield하며 처리합니다. 동일 세션 동시 실행은 Redis 락으로 직렬화하며, 공개 URL 남용은 (tenant, visitor)당 레이트리밋으로 막습니다(at-most-once — taskiq `ListQueueBroker`는 pop=소비라 재배달 없음). 무거운 배치(인제스션·OCR·메모리)는 별도 **Celery prefork worker**로 공존합니다([ADR-0024](./docs/adr/0024-taskiq-chat-celery-batch-coexist.md)).
- **Conversation Memory**: 단일 ChatSession 내 히스토리는 **LangGraph Checkpoint**(PostgreSQL)로 관리됩니다. `thread_id = session_id`라 수동 로드 없이 이전 state가 자동 복원됩니다. 턴마다 새로 조립되는 검색 산출물(RAG 청크·원문 폴백)·휘발성 컨텍스트는 종단 노드에서 비워, 체크포인트엔 **대화만** 남고 비대해지지 않습니다.
- **Visitor Memory**: ChatSession을 넘어 축적되는 장기 기억. 대화 중 LLM이 자동 추출하며 어드민에서 조회·수정·삭제할 수 있습니다.
- **프롬프트 하드닝**(인젝션 방어): 비신뢰 입력(RAG·메모리·Visitor 메시지)을 `UNTRUSTED_DATA` 구역으로 격리·라벨링하고, 플랫폼이 anti-disclosure 지침을 항상 주입합니다. 테넌트 스코프 RAG와 무도구 에이전트가 크로스테넌트·행동 위험을 이미 차단합니다.
- **주제범위 제어**(opt-in): Tenant가 어드민에서 토글을 켜고 **응대 범위**를 한 줄 적으면, 봇이 범위 밖 질문(예: 일반 상식 "파란색 설명")을 정중히 거절합니다. 구조화 출력의 `in_scope` 신호를 프롬프트로 유도하고 **코드 백스톱**이 거절을 강제해(모델이 무시해도 누출 차단), 인사·메타 턴은 통과시키고 범위 밖만 거절합니다. 토글 OFF(기본)면 현행 개방. 거절 문구는 표준(범위 인용) 또는 테넌트 커스텀.
- **프롬프트 캐싱 친화 구조**: 테넌트-불변 prefix(구조화 출력 tool 스키마 + Base System Prompt + 보안 지침)를 안정 prefix로 고정하고, 매 턴 바뀌는 휘발성(RAG·메모리·운영 안내)은 마지막 사용자 턴으로 내려 모든 세션·턴에서 prefix가 재사용되게 합니다. Anthropic provider는 안정 블록에 `cache_control` 분기점 1개를 주입(boundary에서 provider-aware), OpenAI·OpenRouter는 자동 prefix 캐싱을 탑니다.
- **스트리밍**: 토큰·HITL 이벤트는 Redis pub/sub(`session:{id}` 채널)을 통해 SSE로 전달됩니다([ADR-0001](./docs/adr/0001-sse-redis-pubsub.md)). 다중 API 인스턴스에서도 스트리밍이 보장됩니다. AI 답변은 **토큰 델타로 실시간** 흘러(첫 토큰까지 대기 최소화) 위젯이 타이핑되듯 누적 렌더합니다. 구조화 출력의 라우팅 신호(`context_sufficient`)를 응답보다 **먼저** 받도록 스키마를 배치해, 원문 폴백(비-종단 패스)에선 스트리밍을 억제하고(중복 출력 방지) 종단 패스만 흘립니다. provider가 부분 구조화 스트리밍을 못 하면 자동으로 현행 one-shot으로 저하하며, `CHAT_STREAMING_ENABLED=false`로 전면 끌 수 있습니다.
- **토큰 사용량 추적 · 관찰성**: 모든 LLM·임베딩 호출의 토큰을 **테넌트·call_type별로** 집계합니다. LLM 경계(`apps/agent/llm.py`)의 langchain 콜백 + 임베딩 응답 usage를 한 곳에서 캡처해, 우리 DB 롤업(`TokenUsage`, 인앱 사용량 화면용)과 **Langfuse**(오퍼레이터 트레이스·디버깅, A1 셀프호스트·env 미설정 시 no-op)에 동시 기록합니다. 테넌트는 자기 사용량을, 오퍼레이터는 전체를 봅니다. **임베딩은 httpx 직접 호출이라 langchain 콜백이 못 잡으므로**, 단일 경계(`get_embeddings`)에서 Langfuse generation을 수동 발행해 LLM과 대칭으로 트레이스에 띄웁니다(Provider 검증 프로브 임베딩 포함 — OpenAI 과금분 누락 0). **per-tenant 1급 필터**: LLM·임베딩 트레이스에 `tenant:{id}` 태그 + native sessionId(=ChatSession)를 달아 Langfuse 대시보드에서 테넌트별·세션별로 바로 거릅니다. **GraphRAG 검색 관찰성**: 오퍼레이터는 `local_search`·`source_search`를 Langfuse retrieval span으로 봅니다(어떤 청크가 검색됐는지). **테넌트**는 Langfuse 접근이 없으므로, 어드민 콘솔 세션 상세의 **대화 내역에서 질문↔답변 사이에 턴별 실행 노드 흐름(local_search→call_llm→source_search→…)과 검색 근거를 접이식으로**(질문→검색→답변 순으로 원인-결과 추적) 직접 봅니다. 노드 흐름은 메타데이터에 노드명이 없어 체크포인트의 `versions_seen`(노드별 채널 소비 버전)을 스텝 간 비교해 역산하므로, **기존 세션에도 소급 적용**됩니다 — 휴지 체크포인트는 `_clear_transient`로 `rag_chunks`를 비워 슬림 유지하지만(prefix 캐시 설계와 정합 — RAG는 캐시 분기점 뒤 trailing 턴에만 실림), 검색 결과는 **체크포인트 히스토리의 중간 단계에 남아 있어** 새 저장 없이 그걸 복원해 노출합니다. 모든 Langfuse 발행은 가드형·예외안전이라 미설정/장애 시 챗·임베딩을 절대 막지 않습니다.
- **마크다운 챗버블**: AI(assistant) 메시지는 위젯·admin 모두 마크다운으로 렌더합니다(굵게·리스트·코드블록·표·링크, GFM). 스트리밍 중에도 라이브로 서식이 입혀집니다(react-markdown). 방문자 입력·상담원·시스템 안내는 평문 유지. 보안: raw HTML 미파싱·이미지 비활성·링크는 새 탭 + `rel="noopener noreferrer nofollow"` + http/https/mailto만 허용(`javascript:`·`data:` 차단)으로 3자 사이트 iframe에서 AI 출력의 XSS를 막습니다.

### HITL (Human-in-the-Loop)

- **활성화**: Tenant가 `hitl_enabled`로 켜고 끕니다(기본 켜짐). 끄면 AI 전용으로 운영되며 escalation이 발생하지 않습니다.
- **트리거**: (1) LLM이 구조화 출력으로 `needs_hitl: true` 반환(불확실 판단 + 상담원 요청 키워드 통합), (2) 방문자의 명시적 상담원 요청.
- **상태**: Escalation `pending` → `claimed` → `resolved`. trigger_type은 `ai`/`visitor`/`agent`(수동 takeover).
- HITL 모드에서는 AI가 침묵하고, 팀원이 "수락하기"로 클레임한 뒤 메시지를 보냅니다. "AI에게 넘기기"로 다시 AI 모드로 복귀합니다.
- Escalation 발생 시 Slack/Discord/Generic **웹훅**으로 알림을 보냅니다.
- **상담 가능 시간(영업시간)**: Tenant가 타임존 + 요일별 시간창 + 휴일(예외 날짜)을 설정하면, 그 시간 외에는 그래프가 plain으로 컴파일돼 **AI 자동 escalation이 일어나지 않고** 운영 안내만 곁들여 AI가 계속 답합니다. 미설정 시 24/7(opt-in 하위호환). 수동 takeover는 시간과 무관하게 항상 가능합니다.
- **세션 콘솔 + 임의 takeover**: HITL 탭이 세션 콘솔로 진화 — 상단에 진행 중 상담(escalation) 카드, 하단에 **다른 세션 목록**(SSE 연결된 활성 세션 우선)을 보여줍니다. 팀원은 escalation이 없는 임의 세션도 **"내역 보기"로 takeover 없이 채팅 내역을 읽기 전용으로 펼쳐** 확인하거나, **"상담 시작"으로 직접 takeover**(자동-claimed `agent` escalation)할 수 있습니다. 활성 여부는 Redis presence(SSE keepalive로 갱신되는 TTL = 자가치유)로 판단하고, 연결/해제는 `hitl:{tenant}` 채널로 콘솔에 실시간 push됩니다. 위젯 새로고침처럼 옛/새 SSE 연결이 겹칠 때 콘솔이 "유휴"로 잘못 뒤집히지 않도록 **연결을 참조 계수**해 세션의 첫 연결(0→1)·마지막 종료(1→0) 전이에서만 콘솔 델타를 발행합니다. 델타는 ephemeral pub/sub이라 콘솔이 그 순간 미구독(탭 전환·재연결·배포)이면 놓칠 수 있으므로, **콘솔은 자가치유 진실원천(`active_sessions`, TTL 20s)을 주기적으로(15s) 재조정**해 놓친 disconnect로 "접속중"이 멈추는 일을 자동 보정합니다.
- **Event-Driven 파이프라인**: HITL 라이프사이클 전이(Escalated/Claimed/TakenOver/Resolved)는 **Transactional Outbox**로 상태 변경과 한 트랜잭션에 이벤트로 기록되고, **relay 싱글톤**(Redis pub/sub wake로 깨어 드레인)이 **EventBus**(Redis Streams, 추후 Kafka 교체 가능)로 발행합니다. webhook·방문자 SSE(hitl_start/end)·콘솔 델타·presence가 **디커플링된 소비자**로 이 이벤트에서 파생되어, dual-write 유실 없이 at-least-once+멱등+DLQ로 동작합니다. 설계·원리: **[docs/event-driven-architecture.md](docs/event-driven-architecture.md)** · 운영(프로세스·dead-letter 리플레이): [docs/event-pipeline.md](docs/event-pipeline.md).

### Visitor 접근 & 신원 (공개 Slug URL)

옛 EmbedToken(per-session 서명 토큰) 방식은 폐지되고, **공개 `/chatbot/{slug}/` URL**로 대체됐습니다([ADR-0011](./docs/adr/0011-public-slug-url-replaces-embed-token.md)). Tenant는 발급 단계 없이 iframe만 박으면 됩니다.

- **Tenant Slug**: 표시명과 분리된 고유·URL-safe 공개 식별자. Tenant가 어드민에서 설정. **완성형 한글**(예: `/chatbot/우리가게/`)·영문(대소문자)·숫자·하이픈 허용. NFC 정규화 + 대소문자 무시로 조회·중복을 처리합니다(`MyStore`↔`mystore`).
- **Visitor 신원 — 계층형**:
  - *익명*: 위젯이 생성·localStorage에 저장하는 **Anonymous Visitor ID**(세션 넘어 지속).
  - *식별 기본*: `?visitor_id=` 평문(마찰 0).
  - *식별 보안(opt-in)*: Tenant가 "신원검증 요구"를 켜면, `HMAC(tenant secret, visitor_id)` 해시를 요구·검증해 위조를 막습니다. 해시는 안정값이라 유저당 1회 계산해 캐시하며, Operator의 HMAC API(`POST /api/chat/identity`, TENANT_KEY 인증)로 받거나 직접 계산합니다.

---

### 흐름(Flow) 모델 — 실행기 전환 중 (ADR-0027)

테넌트가 그리는 상담 흐름은 `backend/apps/flows/`에 있습니다. **Flow**(테넌트당 하나를 「공개 흐름」으로 지정)는 고칠 수 있는 **초안** 하나와 **바뀌지 않는 Flow Version**들을 가지며, 배포 중 버전 하나를 가리킵니다. Flow Version은 만들 때 흐름 JSON을 검증하고 정규화 JSON의 SHA-256 해시를 남기며, 저장 뒤 수정은 예외입니다. 새 **ChatSession**은 시작 때 공개 흐름의 배포 중 버전에 고정됩니다(`flow_version`, 배포가 진행 중 대화를 바꾸지 않음). 흐름 JSON 형식·검증 오류 코드·순환 규칙(순환은 방문자 답을 기다리는 노드를 지나야 함)은 [docs/flow-json.md](./docs/flow-json.md). **실행기**(`apps/flows/executor.py`, #23)는 흐름 JSON을 LangGraph StateGraph로 짓고(흐름 해시로 캐시) 한 턴을 돌립니다. 노드 종류마다 핸들러(`apps/flows/handlers.py`, 계약은 `contract.py`)가 상태를 바꾸고 갈래를 고르며, 한 턴 노드 상한은 50입니다. 지금 핸들러는 시작·지식 검색·AI 답변·사람 넘김 넷이고, 기존 챗 노드 함수(local search·원문 폴백·call_llm·escalation)를 그대로 불러 씁니다 — LLM Provider는 기존 `chat_provider`가 고릅니다. **기본 흐름**(시작 → 지식 검색 → AI 답변 →[AI가 넘김 판단 시] 사람 넘김, `apps/flows/defaults.py`)은 위 코드 그래프와 같은 동작이며, 흐름 없는 테넌트에 배포 중·공개로 시드됩니다(데이터 마이그레이션 `flows/0002`, 온보딩·Operator 테넌트 생성). **운영 챗은 아직 위 코드 그래프로 돕니다** — 실행기로 갈아 끼우는 일은 #72입니다.

## 4. 기술 사양

| 영역 | 사양 |
|------|------|
| 백엔드 | Python 3.12, Django 5 + Django-Ninja (전면 async), **uvicorn ASGI**, **taskiq**(chat 워커) + Celery 5(배치 워커) |
| LLM 오케스트레이션 | LangChain + LangGraph (PostgresSaver 체크포인트) |
| LLM Provider | **per-Tenant** — OpenAI / Claude(Anthropic 네이티브) / Custom(OpenAI-호환). 미설정 시 플랫폼 기본(OpenRouter). 챗·추출 공용, 키 암호화 저장 |
| Embedding Provider | **per-Tenant**(LLM과 독립) — OpenAI/Custom, OpenAI-호환 `/v1/embeddings`. dev 기본 `bge-m3`(Ollama, 1024차원) |
| Knowledge Graph | **Postgres + pgvector**(차원별 HNSW, **per-Tenant 가변차원**) — 메타데이터와 임베딩 분리 |
| OCR | per-Tenant Vision LLM(GPT-4o·Claude·Gemini 등) — dev/test/prod 같은 경로. 이미지/스캔 PDF·깨진 추출 |
| 관계형 DB | PostgreSQL 16 (Django 모델 + LangGraph 체크포인트) |
| 메시징 | Redis 7 (SSE pub/sub + Celery 브로커 + 레이트리밋·세션 락) |
| 프론트 | React 18 + Vite. 그래프 시각화: `react-force-graph-2d` |
| 인증 | Operator: sandcorp-auth Resource Token(EdDSA, PyJWT — ADR-0028) + 옛 Operator JWT(python-jose, #21에서 걷어냄). 고객(멤버): 같은 sandcorp-auth 로그인 → Membership으로 Flow 테넌트 세션(JWT, python-jose — #40). Visitor는 공개 slug URL + opt-in HMAC 신원검증. TENANT_KEY는 HMAC API·break-glass Admin 초대용 |

> **GPU**: 필수 아님. GPU 호스트에선 Ollama(bge-m3)가 GPU를 씁니다(시험 인프라는 GPU 없이도 돈다 — 9장). Docker Desktop에서는 `runtime: nvidia` + `NVIDIA_VISIBLE_DEVICES=all`로 패스스루합니다. bge-m3가 GPU flash-attention에서 NaN 임베딩을 내는 이슈가 있어 `OLLAMA_FLASH_ATTENTION=0`을 설정합니다.

---

## 5. 환경 변수 (.env)

| 변수 | 설명 | 예시 |
|------|------|------|
| `SECRET_KEY` | Django 서명 키 | 임의의 긴 문자열 |
| `DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT` | PostgreSQL 접속 정보 | |
| `REDIS_URL` | Redis URL | `redis://redis:6379/0` |
| `NEO4J_URI` | Neo4j Bolt URL | `bolt://neo4j:7687` |
| `NEO4J_USER / NEO4J_PASSWORD` | Neo4j 자격 | `neo4j` / `...` |
| `OPEN_ROUTER_API_KEY` | 플랫폼 기본 LLM 키(Tenant Provider 미설정 시) | `sk-or-v1-...` |
| `OPEN_ROUTER_DEFAULT_MODEL` | 플랫폼 기본 Chat LLM | `openrouter/owl-alpha` |
| `OPEN_ROUTER_BASE_URL` | 플랫폼 기본 LLM endpoint | `https://openrouter.ai/api/v1` |
| `GRAPH_EXTRACTION_MODEL` | 플랫폼 기본 추출 모델 | (미지정 시 기본 모델) |
| `OLLAMA_BASE_URL` | Ollama URL(dev 기본 임베딩) | `http://ollama:11434` |
| `OLLAMA_EMBED_MODEL` | dev 기본 임베딩 모델 | `bge-m3` |
| `PLATFORM_DEFAULT_PROVIDERS_ENABLED` | 플랫폼 기본 Provider 폴백(OpenRouter LLM + ollama 임베딩). **dev만 `true`**, prod(GPU 없음)는 `false` → Tenant가 LLM·Embedding Provider 설정 필수. `dev.py`/`prod.py`에서 명시(env 아님) | `True`(dev)/`False`(prod) |
| `PLATFORM_OPENAI_API_KEY` | 플랫폼 OpenAI 키(체험 한도, #41·ADR-0026). 자기 LLM Provider가 없는 prod 테넌트가 남은 체험 턴 동안 쓴다. 비면 그 테넌트의 챗은 방문자에게 「상담 준비 중」으로 멈춘다 | (운영 env에만) |
| `PLATFORM_LLM_MODEL` / `PLATFORM_EMBED_MODEL` | 체험 테넌트의 챗·추출 모델 / 임베딩(OpenAI `dimensions`로 `embed_dim`에 맞춤) | `gpt-4o-mini` / `text-embedding-3-small`(기본) |
| `TRIAL_TURNS` | 새 조직의 체험 턴(1턴 = 방문자 메시지 하나) | `200`(기본) |
| `TENANT_CREATE_LIMIT_PER_USER` / `ONBOARDING_RATE_LIMIT_PER_HOUR` / `TRUSTED_PROXY_COUNT` | 온보딩 남용 방지: 계정당 조직 수 / IP당 시간당 시도 / XFF 프록시 단수 | `3` / `10` / `1`(prod `2`) |
| `CHAT_RATE_LIMIT_PER_VISITOR` / `CHAT_RATE_LIMIT_PER_TENANT` | 공개 URL 레이트리밋(분당) | `20` / `300` |
| `OCR_MAX_PAGES` | 스캔 PDF의 vision OCR 페이지 상한(비용 통제) | `30` |
| `DEBUG` | 디버그 모드 | `False`(운영) |
| `AUTH_ISSUER` | sandcorp-auth 발급자(`iss` 정확히 일치) | `https://auth.sandcorp.co.kr`(기본) |
| `AUTH_JWKS_URL` | 공개 키 JWKS(600초 캐시) | `https://auth.sandcorp.co.kr/.well-known/jwks.json`(기본) |
| `AUTH_RESOURCE` | 이 서비스의 리소스(`aud` 정확히 일치). 원점이 401 `resource_metadata` 주소가 된다 | `https://flow.sandcorp.co.kr/api`(기본) |
| `AUTH_MCP_RESOURCE` | MCP 서버(`/mcp`)의 리소스(`aud`). API 리소스와 따로 둔다(#74) | `https://flow.sandcorp.co.kr/mcp`(기본) |
| `AUTH_OWNER_SUB` | 대표 계정 username(A1). **비우면 대표 토큰이 모두 거부**된다 | `farrar142` |
| `AUTH_CLIENT_ID` / `AUTH_CLIENT_SECRET` | 관리 화면 로그인용 sandcorp-auth 기밀 클라이언트(#21). 비우면 로그인 버튼이 「설정되지 않음」 거절 화면 | (sandcorp-auth에서 발급) |
| `AUTH_REDIRECT_URI` | 로그인 콜백 주소. sandcorp-auth 등록값과 문자 하나까지 일치 | `https://flow.sandcorp.co.kr/api/auth/callback`(기본) |
| `AUTH_AUTHORIZE_URL` / `AUTH_TOKEN_URL` | sandcorp-auth 인가·토큰 엔드포인트 | `<AUTH_ISSUER>/authorize`·`<AUTH_ISSUER>/token`(기본) |
| `AUTH_WEBHOOK_SECRET` | 탈퇴 웹훅(`/api/auth/webhook`) 서명 비밀. 비면 웹훅을 모두 503으로 거부 | (sandcorp-auth `clients webhook`으로 발급) |
| `AUTH_LOGIN_REDIRECT` | 콜백 뒤 돌아갈 관리 화면(거절이면 `?login_error=<사유>`) | `/admin-ui/operator`(기본) |

---

## 6. 빠른 시작 (개발)

```bash
# 1. 환경 변수
cp .env.example .env
# .env에 DB/Redis/Neo4j/OpenRouter 키 입력

# 2. 인프라 + 앱 기동 (dev 스택: db, redis, ollama(+bge-m3),
#    api(uvicorn), worker(Celery 배치), worker-chat(taskiq chat), widget, admin)
docker compose -f docker-compose.dev.yml up --build

# 3. 최초 1회: 마이그레이션
docker compose exec api python manage.py migrate
```

Operator 비밀번호 로그인은 없다(#21). 관리 화면 Operator 로그인은 sandcorp-auth 대표 계정으로 한다 — dev용 기밀 클라이언트를 등록하고 `.env`에 `AUTH_CLIENT_ID`·`AUTH_CLIENT_SECRET`·`AUTH_REDIRECT_URI`(admin dev 서버 주소의 `/api/auth/callback`)·`AUTH_OWNER_SUB`를 채운다. sandcorp-auth 없이 화면만 볼 때는 `docker compose exec api python manage.py seed_e2e_operator --token-file /tmp/op_token`으로 대표 Operator와 세션 토큰을 만들고, 그 값을 브라우저 sessionStorage `op_access`에 넣는다(e2e가 쓰는 방식). 고객(멤버) 화면도 같다 — `seed_e2e_member --token-file …`이 조직·Admin 멤버십과 세션 토큰을 만들고(sessionStorage `agent_access`), `--accept-invite-from <파일>`은 그 파일에 떨어진 초대 원문을 콜백처럼 수락하는 sandcorp-auth 대역이다(e2e `member-team.spec.js`). `--serve-tenants-from <디렉터리>`는 거기에 떨어진 `<id>.req`(조직 이름)마다 새 조직+Admin 멤버십을 만들고 `<id>.token`을 쓰는 대역이다 — e2e 스펙이 자기 조직을 받는 방법(`provisionTenant`, `e2e/tests/memberToken.js`).

접근 경로:

| 경로 | 용도 |
|------|------|
| `http://localhost:5174/admin-ui/` | Operator·Tenant 관리 화면 |
| `http://localhost:8000/api/docs` | Django-Ninja 자동 API 문서 |
| `http://localhost:5173/chatbot/{slug}/` | Visitor 챗봇 위젯 (토큰 없이 slug로) |
| `http://localhost:7474` | Neo4j Browser (그래프 직접 조회, 개발용) |

## 프로덕션 배포

실서버(**oracle A1**, arm64·CPU 전용)는 **무중단 롤링**으로 배포한다 — sub에서 GitHub Actions가
arm64 이미지를 빌드해 GHCR에 올리면, A1의 Jenkins가 `release` 브랜치 웹훅을 받아 `docker rollout`으로
교체한다. NPM(Nginx Proxy Manager)이 공개 TLS를 종단한다. 운영 주소는 `https://flow.sandcorp.co.kr`,
배포 경로는 Jenkins 잡 `sandcorp-flow`의 워크스페이스(호스트 `/var/jenkins_home/workspace/sandcorp-flow`), compose 프로젝트는 `sandcorp-flow-prod`다. 이 스택은 공유 박스에서
도므로 서비스마다 메모리 상한을 둔다(합계 약 3.2G, 표는 deployment.md). 서버 쪽 설정(DNS·NPM·Jenkins
잡·운영 `.env`)은 deployment.md의 「서버 쪽 할 일」 체크리스트를 따른다.

전체 흐름·1회 설정·환경변수·롤백·트러블슈팅은 **[docs/deployment.md](docs/deployment.md)** 참조
(설계 근거: [ADR-0015](docs/adr/0015-oracle-zero-downtime-rolling-deploy.md)).

> ⚠️ prod `SECRET_KEY`는 테넌트 provider 키 암호화 키의 파생원이라 **로테이트 금지**(바꾸면 저장된
> 키 복호화 불가). prod는 `PLATFORM_DEFAULT_PROVIDERS_ENABLED=False`라 테넌트가 자기 Provider를
> 설정해야 한다.

---

## 7. 사용 흐름

### 고객 로그인·Tenant 발급 (#40)
- **로그인**: 고객은 sandcorp-auth 계정으로 로그인합니다(`/admin-ui/tenant` → **sandcorp 계정으로 로그인**). Flow에는 비밀번호가 없습니다. 콜백은 속한 조직(Membership)이 하나면 바로 그 조직 화면을, 여럿이면 **조직 선택**을, 없으면 **조직 만들기**(#41)(`TenantOnboarding`, 조직 만들기 온보딩은 #41)를 보여줍니다. 콜백 거절(`?login_error=`)은 사유별 문구로 안내하고, 상단 바에는 조직 이름·내 이름·**내 계정**(`https://auth.sandcorp.co.kr/account`)·**조직 바꾸기**가 있습니다.
- **조직 만들기(온보딩, #41)**: 속한 조직이 없는 사람은 조직 이름과 공개 주소(`/chatbot/<slug>`, 이름에서 제안)를 넣어 조직을 만들고, 첫 관리자로 바로 들어갑니다. 계정당 조직 수(`TENANT_CREATE_LIMIT_PER_USER`)와 IP당 시도 수에 상한이 있습니다. 새 조직은 기존 챗 설정 그대로 시작하고, 기본 흐름(시작 → 지식 검색 → AI 답변 → 사람 넘김)이 배포 중·공개로 만들어집니다(#23).
- **체험 한도(#41, ADR-0026)**: 자기 LLM 키가 없는 조직은 플랫폼 OpenAI 키로 방문자 대화 `TRIAL_TURNS`(기본 200)턴을 체험합니다(문서 임베딩·자료 정리 포함, 스캔 OCR 제외). 관리 화면 상단 배너가 남은 턴을 보이고, 다 쓰면 「설정에서 LLM 키를 넣어 주세요」로 바뀝니다. 방문자에게는 한도·키 이야기 없이 「상담 준비 중」 문구만 갑니다. 키를 넣으면 한도와 무관하게 자기 키(BYOK)로 돕니다. 설정의 **요금** 탭은 「요금제 준비 중」과 체험 상태를 보입니다.
- **Operator 프로비저닝**: Operator로 로그인 → **Tenant 추가** → `TENANT_KEY`와 **첫 Admin 초대 링크**가 **1회** 표시. 링크를 고객에게 직접 전달하면, 고객이 그 링크로 로그인해 Admin이 됩니다. `TENANT_KEY`는 해시만 저장(분실 시 재발급), HMAC 신원검증 API·break-glass에만 사용(브라우저 비노출). 마지막 Admin이 탈퇴한 Tenant는 목록에 **「주인 없음」** 배지(활성 Admin 0)가 붙고, 「주인 없음만 보기」 필터와 행의 **Admin 초대 링크 만들기**(7일·1회용)로 복구합니다.

### 권한 (Tenant Admin / Tenant Member)
- 멤버(Membership)는 **Admin / Member** 역할을 가집니다(권한 비트 기반, ADR-0025). **Admin은 전 권한**, **Member는 팀원 관리·`TENANT_KEY` 회전·Slug 변경만 제외한 전부**(문서·HITL·프롬프트/HITL 설정·Provider 키·조회 등 일상 운영은 다 가능).
- **팀원 탭**: Admin이 **초대 링크**(역할 지정·7일·1회용, 이메일 발송 없음)를 만들고(만료 시각 표시·복사) 직접 전달합니다. 아직 쓰이지 않은 초대는 **대기 중인 초대**에서 취소하고, 팀원은 이름 → 이메일 → sub 끝 6자리로 보이며 승격/강등·**내보내기**(비활성화)를 합니다. **마지막 활성 Admin은 비활성화·강등 불가**(lockout 방지). Admin 전체가 사라지는 사고는 `TENANT_KEY`로 `POST /api/tenant/invites/ {"role":"admin"}` — Admin 초대 링크를 발급해 복구(break-glass). Member에겐 팀원 관리·키 재발급·Slug 변경 컨트롤이 숨겨집니다.

### Tenant 운영 (멤버)
1. 어드민 UI 로그인 → **설정 탭**에서 Tenant Slug, (선택) LLM·Embedding Provider 키, HITL 토글, 브랜드 텍스트, 신원검증 등을 설정.
2. **문서 탭**: PDF/TXT/이미지/**Excel** 업로드 또는 **웹 URL** 추가(레이블 지정 가능) → 그래프 자동 구축.
3. **🕸️ 지식그래프 탭**: 엔티티 검색(다국어 의미 검색) → 노드-엣지 그래프로 탐색, 노드 클릭으로 이웃 확장.
4. 그래프 신선도 확인 후 **재구축** 트리거. **HITL 탭**에서 에스컬레이션 수락·응대.

### MCP — claude.ai에서 흐름 다루기 (#74)
- **연결**: claude.ai → 설정 → 커넥터 → 사용자 지정 커넥터 추가 → URL `https://flow.sandcorp.co.kr/mcp`. 커넥터가 `/.well-known/oauth-protected-resource/mcp`를 읽고 sandcorp-auth로 로그인시킨다(일반 사용자 member 토큰, `aud`=`AUTH_MCP_RESOURCE`, 스코프 `flow.read`·`flow.write`). auth 쪽 등록은 [docs/deployment.md](./docs/deployment.md) 「서버 쪽 할 일」 11번.
- **권한**: 스코프는 상한이고 실제 판정은 멤버십 역할 — 테넌트 **Admin만 편집·배포**, Member는 읽기. 대표 계정도 멤버십이 있는 테넌트만 보인다(Operator 기능 없음).
- **테넌트 고르기**: 테넌트 도구는 `tenant`(slug) 인자를 받고, 멤버십이 하나면 생략한다. `list_tenants`가 내 테넌트·역할을 보여 준다.
- **도구**(지금): `list_tenants`, `get_flow_schema`(흐름 JSON 규칙 — 흐름을 고치기 전에 한 번 읽는다), 흐름 도구(#76) `list_flows`·`get_flow`(초안·검증 결과·버전 목록, `version`으로 버전 JSON) — 여기까지 읽기, `create_flow`·`update_flow`·`save_version`·`deploy_version`·`test_turn` — 쓰기(Admin). 문서·지식그래프·상담 읽기 도구는 #77.
- **읽기 도구**(#77, 모두 `flow.read`·모든 멤버): `list_documents`(문서 이름·상태·오류·갱신 시각 + 지식그래프 신선도, 커서 쪽), `search_knowledge`(질의 → 엔티티·1홉 관계, 챗봇 Local Search와 같은 `apps/rag/services.py`; `top_k` ≤ 20, 엔티티당 관계 20개까지), `list_sessions`(기간 `since`/`until`·`status` all|escalated|active, 커서 쪽), `get_session`(메시지·사람 넘김 이력·고정된 흐름 버전; 메시지 `limit` ≤ 200 + 커서, 메시지당 2000자까지). 시험 한 턴 세션(`is_test`)은 목록·읽기 모두에서 빠진다. 세션 콘솔과 같은 쿼리를 `apps/memory/services.py`에서 공유한다.
- **흐름 고치기**: `update_flow`는 연산 목록(`upsert_node`·`delete_node`·`connect`·`disconnect`)을 한 번에 **원자적으로** 적용한다(하나라도 못 하면 아무것도 안 바뀜). 초안은 틀려도 저장되고 결과에 `errors[{node_id, code, message}]`가 온다 — 도구 오류가 아니다. 버전 저장은 검증 통과한 초안만(`note` 선택 메모 200자 이내 — 만들 때만 붙고 못 바꾸며 `get_flow` 버전 목록에 `note`로 나온다), 배포는 실행기에 핸들러가 있는 노드만으로 된 버전만(되돌리기는 이전 번호 배포). `test_turn`은 초안으로 한 턴을 돌려 답·지나간 노드를 보여 준다 — 시험 세션이라 운영 대화 목록에 안 섞이고, 사람 넘김은 실제로 넘기지 않으며, 체험 테넌트면 체험 턴 1을 쓴다. 편집 규칙은 `backend/apps/flows/services.py`(흐름 API #28·편집기 #29도 이 층을 쓴다).
- **전송**: Streamable HTTP의 JSON 응답 모드(POST 한 번에 JSON 한 답, 세션·SSE 없음, 배치 거부, 본문 1 MiB). 코드는 `backend/apps/mcp/`(도구 등록 방법은 `registry.py` 머리말).

### Visitor
- Tenant 사이트에 임베드된 위젯이 **`/chatbot/{slug}/`**(필요 시 `?visitor_id=`·`hash`)로 연결 → 지식그래프 RAG로 응답, HITL이 켜져 있으면 필요 시 사람 전환.

---

## 8. API 개요 (`GET /api/docs`에 전체)

**Operator 인증**(`/api/operator/*`): `Authorization: Bearer <토큰>`으로 두 가지를 받는다. ① sandcorp-auth 액세스 토큰(Resource Token, `aud`=`AUTH_RESOURCE`) — 규칙은 sandcorp-auth `docs/resource-server-token-validation.md` 3~6·10절(T1~T10 → A1~A3 → S1, `apps/tenants/resource_token.py`). **대표 토큰(`acct` 없음, `sub`=`AUTH_OWNER_SUB`) + `admin` 스코프**일 때만 열린다. 일반 사용자 토큰(`acct=member`)은 검증을 통과해도 403. 실패는 401(토큰 없음/`invalid_token`)·403(`insufficient_scope`)에 `WWW-Authenticate` 헤더. ② Flow 세션 Access Token(30분, ADR-0013) — 관리 화면이 sandcorp-auth 로그인 콜백 뒤 `/api/operator/auth/refresh`로 받는다.

**관리 화면 Operator 로그인**(#21, `apps/tenants/operator_login.py`): `GET /api/auth/authorize`가 state·PKCE(S256)·nonce를 서명된 httpOnly 쿠키(SameSite=Lax, 10분)에 맡기고 sandcorp-auth `/authorize`로 보낸다(`scope=openid profile email login admin`, `resource=AUTH_RESOURCE`). `GET /api/auth/callback`은 state → 코드 교환(`client_secret_basic` + `code_verifier`) → 액세스 토큰(`login` 스코프·`client_id`) → ID 토큰(EdDSA·`iss`·`aud`=client_id·`exp`·`nonce`·같은 `sub`)을 확인하고, **대표(`acct` 없음, A1) + `admin`**이면 사용자 행 `User(iss, sub)`와 이어진 Operator에 Refresh Family를 열어 `/admin-ui/operator`로 돌려보낸다. 일반 사용자면 `?login_error=member`, 대표인데 `admin`이 없으면 `admin`, 그 밖에 `state|exchange|token|denied|config`. sandcorp-auth 토큰은 저장하지 않는다.

**고객(일반 사용자) 로그인**(#40, `apps/tenants/members.py`): `GET /api/auth/authorize/member`(`scope=openid profile email login` — admin 없음)로 시작하면 같은 콜백이 A1~A3을 통과한 `acct=member`를 받아 `User(iss, sub, acct="member")`를 갱신(이름·이메일 캐시)하고, `AUTH_MEMBER_REDIRECT`(기본 `/admin-ui/tenant`)로 돌려보낸다. 멤버십 1개 → 그 Membership의 Refresh Family(`member_refresh` 쿠키, `/api/tenant/members/auth`), 여럿 → `?select_tenant=1` + 10분 서명 표 `member_ticket`(→ `GET /api/auth/member/tenants`, `POST /api/auth/member/select`), 없음 → `?onboarding=1`(같은 표). 초대 링크(`/api/auth/authorize/member?invite=<토큰>`)로 시작한 로그인은 먼저 멤버십을 만들고, 실패하면 `?login_error=invite_expired|invite_used|invite_invalid`. 고객 진입에 대표 계정이면 `?login_error=owner`.

| 엔드포인트 | 인증 | 용도 |
|------------|------|------|
| `GET /api/auth/authorize` · `GET /api/auth/authorize/member[?invite=]` · `GET /api/auth/callback` | 없음(브라우저 이동) | 관리 화면 로그인(sandcorp-auth) — Operator 진입 / 고객 진입, 콜백 공통 |
| `GET /api/auth/member/tenants` · `POST /api/auth/member/select` | `member_ticket` 쿠키 | 콜백 뒤 조직 목록·선택(테넌트 세션 열기) |
| `POST /api/auth/member/tenants` | `member_ticket` 쿠키 | 온보딩: 조직 만들기 → 첫 Admin 세션(400 형식·409 중복/상한·429 IP 상한) |
| `GET /api/tenant/trial/` | 멤버 | 체험 상태 `byok`·`trial`·`exhausted`·`unavailable`·`platform_default` + 남은 턴 |
| `POST /api/auth/webhook` | HMAC 서명(`X-Sandcorp-Signature`) | sandcorp-auth 탈퇴 웹훅 `user.deleted` — 사용자·멤버십·세션 삭제(ADR-0028 「탈퇴」) |
| `POST /api/operator/auth/refresh` · `logout` · `logout-all` | Refresh 쿠키 / Operator | Flow 세션 회전·폐기 |
| `POST /api/operator/tenants/` | Operator | Tenant 생성(+ 첫 Admin 초대 링크) |
| `GET /.well-known/oauth-protected-resource` | 없음 | 보호 리소스 메타데이터(RFC 9728) |
| `POST /api/tenant/members/auth/refresh` · `logout` · `logout-all` | Refresh 쿠키 / 멤버 | 테넌트 세션 회전·폐기 |
| `GET /api/tenant/members/` · `PATCH …/{id}/role` · `PATCH …/{id}/deactivate` · `GET …/me` | 멤버(관리는 Admin) / TENANT_KEY | 팀원 목록·역할·비활성화(마지막 Admin 보호) |
| `GET·POST /api/tenant/invites/` · `DELETE …/{id}` | 멤버 Admin / TENANT_KEY | 팀원 초대 링크(7일·1회용). TENANT_KEY는 break-glass Admin 초대 |
| `POST /api/chat/identity` | TENANT_KEY | visitor_id 신원검증 HMAC 해시 발급 |
| `GET /api/chat/stream?slug=&visitor_id=&hash=` | 없음(slug 공개) | 방문자 SSE 연결(ChatSession) |
| `POST /api/chat/message` | 세션 | 방문자 메시지(레이트리밋, SSE로 응답) |
| `PATCH /api/tenant/slug/` | 멤버 세션 | Tenant Slug 설정 |
| `PATCH /api/tenant/config/` | 멤버 세션 | 설정(Provider·HITL·brand·신원검증 등). 임베딩 Provider 변경 시 재임베딩 트리거 |
| `POST /api/tenant/documents/` | 멤버 세션 | 파일 업로드(PDF·TXT·이미지·Excel) |
| `POST /api/tenant/documents/url` | 멤버 세션 | 웹 URL(들) 인제스션 |
| `POST /api/tenant/documents/{id}/refetch` | 멤버 세션 | 웹 Document 재-fetch |
| `GET /api/tenant/documents/graph/search?q=` | 멤버 세션 | 엔티티 하이브리드 검색 → 서브그래프 |
| `GET /api/tenant/documents/graph/status` | 멤버 세션 | Graph Freshness |
| `POST /api/tenant/documents/graph/rebuild` | 멤버 세션 | 엔티티 해소(SAME_AS) 재구축 트리거 |

---

## 9. 테스트

- **단위/통합**: `docker-compose.test.yml`의 `test` 서비스(pytest). **실제 Postgres(pgvector)·실제 bge-m3(Ollama)**를 사용(결정적). 비결정적 외부 경계인 **LLM·Vision OCR 호출만 결정적 Fake로 교체**합니다(`apps/agent/llm` 경계 + conftest Fake, OCR은 `vision_ocr` 픽스처가 `transcribe_image`를 대체) — 외부 모델 판단이 아니라 우리 코드 동작을 검증.
  - **실행**: `./scripts/test.ps1` (PowerShell) 또는 `bash scripts/test.sh` — 호스트 `backend/`를 마운트해 코드 변경을 즉시 반영하고, **종료 시 테스트 전용 인프라(db·redis·ollama)를 자동 `stop`** 하여 메모리(GPU면 VRAM)를 돌려줍니다(다음 실행은 `start`로 빠르게 재개). 인자로 특정 테스트 경로를 넘길 수 있습니다(예: `bash scripts/test.sh tests/test_rag.py`).
  - **GPU 없이 돈다**: 기본 `docker-compose.test.yml`은 CPU 구성(ollama CPU, OCR 컨테이너 없음)입니다. `docker info`에 nvidia 런타임이 있으면 스크립트가 `docker-compose.test.gpu.yml`을 겹쳐 ollama를 GPU로 돌립니다. `TEST_GPU=0|1`로 강제할 수 있습니다.
  - 리눅스에선 시험 컨테이너가 호스트 uid:gid로 돌아, 마운트한 `backend/`에 root 소유 파일을 남기지 않습니다.
- **E2E**: Playwright. 챗 LLM은 OpenAI 호환 **Fake LLM 서비스**(`fake_llm/`)로 결정화합니다.
- 인제스션·추출 프롬프트의 실제 타당성은 실제 OpenRouter로 bring-up 검증합니다.

---

## 10. 설계 결정(ADR)

- `0001` SSE + Redis pub/sub
- `0003` iframe 임베드 / ~~`0004` 서버사이드 EmbedToken~~ (0011로 supersede)
- `0005` Docker Compose 인프라
- **`0007` GraphRAG(Neo4j)가 2-step 벡터 RAG를 대체** (pgvector ADR-0002 supersede)
- **`0021` GraphStore 저장 엔진을 Neo4j→Postgres+pgvector로 이전** (그래프 모델 보존, 박스 부담↓·관리형 이식)
- **`0008` 증분 인제스션 + 배치 Community 재구축**
- **`0009` 깨진 추출(Garbled)은 LLM 정제가 아니라 OCR 재추출** (+ Amendment: 픽셀 vision OCR 허용)
- **`0020` OCR을 per-Tenant Vision Provider로 (Paddle는 dev/test 격하 → 2026-10-24 보충: dev/test에서도 제거)**
- **`0010` Entity 정체성은 이름이 아니라 맥락 — Mention/Entity 분리 + 비파괴 SAME_AS 동치**
- **`0011` 공개 Tenant Slug URL이 EmbedToken을 대체** + 계층형 Visitor 신원(opt-in HMAC)
- **`0012` Tenant 부담 멀티 Provider(LLM·Embedding) + per-Tenant 가변차원 인덱스 + 재임베딩 재구축**
- **`0013` 어드민 인증 access/refresh 토큰**
- **`0014` 어드민 HTTP 클라이언트 OpenAPI(orval) 코드젠 + 전체 TS 전환**
- **`0015` oracle 무중단(rolling) 배포 — docker-rollout + expand/contract + GHCR(arm64)** (제안)
- **`0016` Global Search 제거(Community 요약 폐기) — Local + 원문 폴백으로 대체, 엔티티 해소는 잔존**
- **`0017` 어드민 리디자인 — React 유지(Next.js 기각) + Tailwind/shadcn + 중첩 URL 라우트 + 사이드바 셸**
