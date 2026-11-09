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
| **relay + 이벤트 소비자** (Event 파이프라인) | HITL/세션 라이프사이클의 **Transactional Outbox** 발행(relay 싱글톤) + 디커플링된 소비자(webhook·visitor-bridge·console-bridge·presence-bridge). 운영은 `events` 컨테이너 하나(`run_event_pipeline`)가 relay와 소비자 4개를 한 프로세스에서 돌린다(#290). → [event-driven-architecture.md](./docs/event-driven-architecture.md) |
| **Knowledge Graph** | **Postgres + pgvector**에 흡수 — Entity Mention·관계(메타데이터) + **per-Tenant 가변차원 HNSW**(Text Unit/Mention 임베딩, 차원별 vec 테이블). 과거 Neo4j에서 이전([ADR-0021](./docs/adr/0021-graphstore-pgvector-replaces-neo4j.md)) |
| **PostgreSQL** | Django 모델(Tenant·Document·ChatMessage 등) + **LangGraph Checkpoint**(대화 state) |
| **Ollama** | 실제 임베딩 `bge-m3`(다국어, 1024차원). dev는 `--profile ollama`로 켜고(기본은 `fake_llm` 가짜 임베딩), 시험은 `real_embedding` 마커 시험에서만 쓴다. prod에선 Tenant Embedding Provider가 대체 |
| **OCR** | 이미지/스캔 PDF의 텍스트 추출 — dev/test/prod 모두 **per-Tenant Vision Provider**(GPT-4o·Claude·Gemini 등, BYOK) 하나. 미설정 테넌트는 이미지·스캔 PDF 업로드가 명확한 오류로 실패 ([ADR-0020](./docs/adr/0020-vision-ocr-replaces-paddle.md)) |
| **Redis** | SSE pub/sub + **taskiq(chat)·Celery(배치) 브로커** + **EventBus(Streams)** + relay wake + 레이트리밋·세션 락 |
| **Widget** (`/chatbot/{slug}/`, `/chatbot/{slug}/{embed_key}/`) | Visitor용 채팅 위젯 (React) — 토큰 없이 slug로 접근. 한 조각 주소는 기본 흐름, 두 조각 주소는 그 키의 흐름(#246, ADR-0034) |
| **Landing** (`/`, `/terms`, `/privacy`) | 공개 소개 페이지(#141) — 「흐름으로 만드는 상담 챗봇」 소개(흐름 편집기·시험 대화·판단 기록·HTTP 도구 등 실제 기능만) + `/try` 체험 CTA(짧은 주소, #165)(템플릿별 `?template=home\|shop\|academy` 링크) + 운영자·테넌트 로그인 + 샌드코프 홈 문의 링크(`https://sandcorp.co.kr/#home-inquiry`, 이메일 없음). sandcorp-design 토큰(흰·검정·네이비·hairline, 다크는 `prefers-color-scheme` + 수동 전환 `html.dark`/`html.light`, 저장 키 `admin_theme`을 admin과 공유)은 `widget/src/landing/landing.css`. 약관·처리방침은 본문 그대로 같은 톤(처리방침에 서비스 오류 기록 자동 수집 항목·90일 보관·공통 문서 링크, #312). widget의 별도 멀티페이지 엔트리(`landing.html`·`terms.html`·`privacy.html`)라 위젯 챗봇 번들과 섞이지 않는다. 시험: `widget` vitest + `e2e/tests/landing.spec.js`(링크·390폭 가로 넘침) |
| **Made by Sandcorp** (#288) | 공개 페이지(`/`·`/terms`·`/privacy`) 푸터 맨 아래 `Made by Sandcorp` → `https://sandcorp.co.kr/?utm_source=flow&utm_medium=footer`(rel 없음, 백링크 목적). React `SiteFooter`가 그리고, 크롤러용으로 `landing/privacy/terms.html`의 `#root` 안에도 같은 `<a>`를 미리 넣어 둔다(마운트 때 `createRoot`가 걷어 낸다). 챗봇 위젯(`index.html`)·admin 제외. 시험: vitest + `e2e/tests/landing.spec.js` |
| **Flow 아이콘** (#142) | sandcorp-design **v0.4.0**(cadc566) `assets/flow/*`를 값 복사 방식으로 `widget/public/flow/`·`admin/public/flow/`에 둔다(favicon.svg 다크 대응·favicon.ico·favicon-32.png·apple-touch-icon.png·icon-192/512.png(maskable)·site.webmanifest·icon.svg; manifest의 name·start_url만 Flow에 맞게 고침, 수정 금지·갱신은 태그를 올려 다시 복사). 엔트리(랜딩·약관·처리방침·위젯 챗봇·admin) `<head>`에 `<link rel=icon>` 등. 위젯은 base `/embed/`라 `/embed/flow/...`, admin은 `/admin-ui/flow/...`. 루트 `/favicon.ico`는 `widget/public/favicon.ico`를 nginx `location = /favicon.ico`가 서빙. 로고(icon.svg)는 랜딩 머리·약관/처리방침 홈 링크·푸터·admin 대시보드 머리·`/try` 머리·로그인 카드. 위젯 챗봇 머리는 테넌트 브랜드라 Flow 로고를 얹지 않는다. 시험: vitest + `e2e/tests/icons.spec.js` |
| **짧은 주소** (#165) | 바깥(portfolio 등)에 약속한 주소: `/try`(`?template=home\|shop\|academy` 보존)·`/flows/{slug}` → 운영 nginx(`backend/nginx/nginx.oracle.conf`)가 `/admin-ui/try`·`/admin-ui/flows/{slug}`로 **301**(상대 `Location`, 쿼리 보존). 위젯 embed(`/chatbot/<slug>/`)는 그대로. **주소 표의 단일 출처는 `docs/deployment.md` 「portfolio(sandcorp.co.kr)가 쓰는 Flow 주소」**. 랜딩 「체험해 보기」와 설정 「공개 흐름 보기」 주소 표시는 짧은 주소를 쓴다. 시험: `bash scripts/test-nginx-metrics.sh`(리다이렉트·이웃 location 겹침 없음·스니펫 1회) |
| **/try 임베드 모드** (#180, #199) | `/try?embed=1[&template=home][&theme=dark\|light]` — iframe 용. 머리줄(브랜드·로그인·테마 토글) 숨김(남은 대화·「이 흐름으로 가입」만 얇은 줄), 바깥 링크는 `target=_blank rel=noopener`, 높이는 내용 그대로·편집기는 고정 높이(폰 캔버스 480px·넓은 폭 격자 640px — 뷰포트 단위를 쓰지 않아 iframe 높이 전달과 순환하지 않음). 격자가 한 줄로 쌓이는 폭(lg 미만, 929px 등)에서는 캔버스를 노드 목록보다 먼저 그린다(일반 화면은 그대로). portfolio iframe 초기 높이 권장값은 640px. 판정·유지는 `admin/src/lib/embed.ts`(쿼리 + sessionStorage). `template=home`이면 곧바로 홈 편집기(세션은 방문이 아니라 첫 쓰기에 생김, #195). 세션이 없어도 「이 흐름으로 가입」은 새 탭 링크 `/admin-ui/try/signup?template=…`(`rel="noopener nofollow"`, #198)이고 세션이 생기면 옮기기 로그인으로 곧바로. 높이 전달: 부모가 `{type:'sandcorp-flow:hello'}`를 보내면 `event.source === window.parent`일 때만 그 origin으로 `{type:'sandcorp-flow:height', height}`를 보낸다(ResizeObserver, `'*'`·referrer 미사용). 규약·부모 스니펫·운영 전제는 `docs/deployment.md` 「체험 임베드」 표 행(단일 출처), 결정은 [ADR-0031](./docs/adr/0031-try-embed-mode.md)·[ADR-0032](./docs/adr/0032-try-session-created-on-first-write.md). 시험: vitest(`embed.test.ts`·`TryEmbed.test.tsx`) + `e2e/tests/try-embed.spec.js` + 같은 사이트 두 호스트 iframe 여정 `e2e/tests/try-embed-hosts.spec.js`(첫 저장 세션·새로고침 세션·가입 새 탭·hello→height; 프레임 거부 헤더는 nginx 계약 시험) |
| **내 사이트에 붙이기** (#166) | 설정 「공개 URL·보안」의 Slug 아래 카드(`admin/src/components/EmbedGuideCard.tsx`): slug 기준 iframe 코드(`<iframe src="{origin}/chatbot/{slug}/" width="400" height="600" style="border:none" allow="microphone">` — `docs/deployment.md` portfolio 표와 같은 문자열) 표시·복사, URL 복사, 미리보기 링크. slug가 없으면 slug 정하기 안내(Member는 관리자에게 요청 안내). 코드에 비밀은 없고 HMAC 해시는 서버에서 만들라는 안내만 한다. 읽기 전용 안내라 Member도 본다. 코드 블록은 카드 안에서 가로 스크롤. 시험: vitest `EmbedGuideCard.test.tsx` + `e2e/tests/embed-guide.spec.js`(복사 → 빈 HTML에 붙이면 위젯이 뜸. e2e 스택엔 nginx가 없어 src만 Vite 위젯 `/embed/chatbot/…`로 바꿔 넣음) |
| **방문 지표 스니펫** (#154) | env `METRICS_SNIPPET_URL`(비면 끔)·`METRICS_SITE_KEY`(기본 `flow`). 운영 nginx 컨테이너가 시작 때 `backend/nginx/flow-metrics.conf.template`을 envsubst → `sub_filter`가 랜딩(`/`·`/terms`·`/privacy`)·admin(`/admin-ui/`, `/try` 포함) 응답의 `</head>` 앞에 `<script defer src data-site>`를 넣는다. 위젯(`/embed/`·`/chatbot/`)엔 안 넣고 URL이 비면 태그 자체가 없다. 빌드에 굽지 않으므로 env 변경은 nginx 재생성으로 반영. 같은 nginx에서 `.webmanifest`를 `application/manifest+json`으로 낸다. e2e는 vite dev라 스니펫이 없다. 시험: `bash scripts/test-nginx-metrics.sh`(nginx:alpine에 운영 conf를 올려 curl) |
| **링크 공유 카드** (#186) | 랜딩·약관·처리방침(`widget/*.html`)과 admin `index.html`의 정적 head에 `og:*`·`twitter:card=summary_large_image` 메타(`og:url`·canonical은 #286에서 공개 3페이지(`/`·`/privacy`·`/terms`)에만 운영 주소로 고정 — admin `index.html`은 SPA 한 파일이 여러 경로를 서빙해 없음, 위젯 `/embed/`·`/chatbot/`엔 안 넣음). 이미지는 절대 URL `https://flow.sandcorp.co.kr/embed/flow/og.png`(랜딩류)·`/admin-ui/flow/og.png`(체험·관리), 1200×630. 원본 `scripts/og-image/og-landing.svg`·`og-admin.svg`, 다시 만들기 `bash scripts/og-image/build.sh`(docker 안 Chromium, Pretendard는 `PRETENDARD_DIR`) 후 `widget/public/flow/og.png`·`admin/public/flow/og.png` 커밋. 시험: `bash scripts/test-nginx-metrics.sh`(실제 소스 html을 nginx에 올려 curl) |
| **robots.txt·sitemap.xml** (#286) | 운영 nginx(`backend/nginx/nginx.oracle.conf`)의 `location = /robots.txt`·`= /sitemap.xml`이 정적 문자열로 직접 낸다(widget 빌드·이미지 재빌드 없이 conf만 바뀌고, CSP·지표 스니펫은 안 붙음. sitemap의 `.xml`은 `types { }`로 `application/xml` 유지). 운영 도메인이 `flow.sandcorp.co.kr` 하나라 절대 URL을 고정했다. **공개 경로를 더하거나 빼면** 그 두 location(robots의 Allow/Disallow, sitemap의 `<url>`)과 새 페이지의 canonical·`og:url`을 함께 고치고 `bash scripts/test-nginx-seo.sh`를 돌린다. 공개: `/`·`/privacy`·`/terms`·`/admin-ui/try`(체험, robots는 가장 긴 일치가 이겨 `Allow: /admin-ui/try`가 `Disallow: /admin-ui/`를 이기고, 렌더링용 `/admin-ui/assets/`·`/flow/`도 Allow). 비공개: `/admin-ui/`·`/api/`·`/mcp`·`/chatbot/`·`/embed`(자산 `/embed/assets/`·`/flow/` 제외)·`/.well-known/`·`/_internal/`·`/flows/`. `noindex`는 안 넣었다(Disallow된 페이지는 크롤러가 head를 못 읽고, admin `index.html`은 체험 화면과 같은 파일). 시험: `bash scripts/test-nginx-seo.sh`(상태·Content-Type·내용·스니펫 미주입·기존 경로 회귀) |
| **Admin UI** (`/admin-ui/`) | Operator·Tenant 관리 화면 (React + Tailwind/shadcn, **산업용 HMI 테마**(ADR-0026): 무채 회녹색 바탕·호박색 주색·2px 모서리·그림자 없음, 토큰은 `admin/src/index.css`의 `--flow-*`) — **좌측 사이드바 내비 + 자원별 URL 라우트**(ADR-0017). 문서·**지식그래프 인스펙터**·Visitors·설정(Provider 포함)·팀원·**흐름(목록·편집기 — 노드 설정은 종류별 폼, 필드 구성은 백엔드 `GET /api/tenant/flows/node-schema/`(노드 config pydantic 모델의 JSON 스키마)에서 끌어오고 사람이 읽는 라벨·도움말만 `admin/src/lib/nodeFormLabels.ts`에 두며, JSON 직접 편집은 「고급」 탭; 맞춤 보기는 노드 글자가 읽히는 배율 0.75 아래로 줄이지 않음)**·HITL·**토큰 사용량**(테넌트=자기 것/오퍼레이터=전체, recharts) 섹션. **390폭**: 공용 `Table`이 카드형으로 접히고 셸은 햄버거 메뉴 — `e2e/tests/narrow-overflow.spec.js`가 모든 라우트(`narrow-routes.json`)의 넘치는 요소 0개를 지킨다 |
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
3. **임베딩**: Mention(`name+description`)과 Text Unit을 **Tenant Embedding Provider**(미설정 시 dev=`OLLAMA_BASE_URL`: 기본 `fake_llm` 가짜, 켜면 ollama `bge-m3`)로 배치 임베딩해 **per-Tenant 벡터 인덱스**에 저장합니다. 인덱스 차원은 Tenant의 임베딩 모델에 맞춰지고, Tenant마다 라벨·인덱스가 격리됩니다([ADR-0012](./docs/adr/0012-per-tenant-llm-embedding-providers.md)).
4. **신선도 표시**: 그래프가 바뀌었으므로 `stale`로 표시됩니다. **Entity Resolution(SAME_AS)**은 전역 연산이라 업로드마다 돌리지 않고 **배치/트리거**로 재구축합니다([ADR-0008](./docs/adr/0008-incremental-ingest-batched-community-rebuild.md), [ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md)).

### 2.3 검색: Local + 원문(TextUnit) 폴백

채팅 그래프(LangGraph)는 항상 **Local Search**로 가고, 그래프-only로 답을 못 내면 원문으로 보강합니다([ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md) — Global Search 제거):

```
local_search ─▶ call_llm ──(context_sufficient=False)──▶ source_search ─▶ call_llm(재호출)
```

- **Local Search** — Entity 중심 근거. 질의로 resolved Entity를 찾고 그 이웃 관계를 구조화 근거로 모읍니다([ADR-0010](./docs/adr/0010-entity-resolution-mention-entity-context-equivalence.md)).
- **원문 폴백(source_search)** — LLM이 "제공된 근거로 답할 수 없다"(`context_sufficient=False`)고 표시하면, 질의 임베딩으로 최근접 **Text Unit 원문**을 `vector_search`해 근거에 보강하고 한 번 더 호출합니다(top_k 캡). 원문에서 **새로 보탤 것을 못 찾으면 다시 묻지 않고** 첫 답을 냅니다 — 프롬프트가 같아 같은 요청을 두 번 보내는 낭비였습니다(#230). 추출이 버린 스펙·수치·표를 원문으로 회복합니다([ADR-0016](./docs/adr/0016-remove-global-search-keep-entity-resolution.md)). 그래프로 답한 경우엔 호출되지 않아 토큰을 통제합니다.
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

- **실행 격리**: 런타임은 전면 async입니다([ADR-0022](./docs/adr/0022-full-async-uvicorn-taskiq.md)) — 웹은 **uvicorn ASGI**, chat 1턴은 **전용 taskiq `worker-chat`**에서 async로 실행되어 블로킹이 SSE 서빙을 얼리지 않습니다(gevent 블로킹-허브 병 원천 제거). 노드가 async라 한 이벤트루프가 동시 다수 chat을 IO 대기 중 yield하며 처리합니다. 노드의 sync 단계(DB)는 워커 프로세스의 thread-sensitive 스레드 하나를 나눠 쓰므로, 지식 검색의 임베딩 HTTP와 HTTP 도구의 외부 요청은 그 스레드 밖에서 기다려 다른 대화를 줄 세우지 않습니다(#262, #268). DB 단계 자체의 줄 섬은 턴당 20~30ms라 보류했습니다(ADR-0035). 동일 세션 동시 실행은 Redis 락으로 직렬화하며, 공개 URL 남용은 (tenant, visitor)당 레이트리밋으로 막습니다(at-most-once — taskiq `ListQueueBroker`는 pop=소비라 재배달 없음). 무거운 배치(인제스션·OCR·메모리)는 별도 **Celery prefork worker**로 공존합니다([ADR-0024](./docs/adr/0024-taskiq-chat-celery-batch-coexist.md)).
- **Conversation Memory**: 단일 ChatSession 내 히스토리는 **LangGraph Checkpoint**(PostgreSQL)로 관리됩니다. `thread_id = session_id`라 수동 로드 없이 이전 state가 자동 복원됩니다. 턴마다 새로 조립되는 검색 산출물(RAG 청크·원문 폴백)·휘발성 컨텍스트는 종단 노드에서 비워, 체크포인트엔 **대화만** 남고 비대해지지 않습니다.
- **Visitor Memory**: ChatSession을 넘어 축적되는 장기 기억. 대화 중 LLM이 자동 추출하며 어드민에서 조회·수정·삭제할 수 있습니다.
- **프롬프트 하드닝**(인젝션 방어): 비신뢰 입력(RAG·메모리·Visitor 메시지)을 `UNTRUSTED_DATA` 구역으로 격리·라벨링하고, 플랫폼이 anti-disclosure 지침을 항상 주입합니다. 테넌트 스코프 RAG와 무도구 에이전트가 크로스테넌트·행동 위험을 이미 차단합니다.
- **주제범위 제어**(opt-in): Tenant가 어드민에서 토글을 켜고 **응대 범위**를 한 줄 적으면, 봇이 범위 밖 질문(예: 일반 상식 "파란색 설명")을 정중히 거절합니다. 구조화 출력의 `in_scope` 신호를 프롬프트로 유도하고 **코드 백스톱**이 거절을 강제해(모델이 무시해도 누출 차단), 인사·메타 턴은 통과시키고 범위 밖만 거절합니다. 토글 OFF(기본)면 현행 개방. 거절 문구는 표준(범위 인용) 또는 테넌트 커스텀.
- **프롬프트 캐싱 친화 구조**: 테넌트-불변 prefix(구조화 출력 tool 스키마 + Base System Prompt + 보안 지침)를 안정 prefix로 고정하고, 매 턴 바뀌는 휘발성(RAG·메모리·운영 안내)은 마지막 사용자 턴으로 내려 모든 세션·턴에서 prefix가 재사용되게 합니다. Anthropic provider는 안정 블록에 `cache_control` 분기점 1개를 주입(boundary에서 provider-aware), OpenAI·OpenRouter는 자동 prefix 캐싱을 탑니다.
- **스트리밍**: 토큰·HITL 이벤트는 Redis pub/sub(`session:{id}` 채널)을 통해 SSE로 전달됩니다([ADR-0001](./docs/adr/0001-sse-redis-pubsub.md)). 다중 API 인스턴스에서도 스트리밍이 보장됩니다. AI 답변은 **토큰 델타로 실시간** 흘러(첫 토큰까지 대기 최소화) 위젯이 타이핑되듯 누적 렌더합니다. 구조화 출력의 라우팅 신호(`context_sufficient`)를 응답보다 **먼저** 받도록 스키마를 배치해, 원문 폴백(비-종단 패스)에선 스트리밍을 억제하고(중복 출력 방지) 종단 패스만 흘립니다. provider가 부분 구조화 스트리밍을 못 하면 자동으로 현행 one-shot으로 저하하며, `CHAT_STREAMING_ENABLED=false`로 전면 끌 수 있습니다.
- **토큰 사용량 추적 · 관찰성**: 모든 LLM·임베딩 호출의 토큰을 **테넌트·call_type별로** 집계합니다. LLM 경계(`apps/agent/llm.py`)의 langchain 콜백 + 임베딩 응답 usage를 한 곳에서 캡처해, 우리 DB 롤업(`TokenUsage`, 인앱 사용량 화면용)과 **Langfuse**(오퍼레이터 트레이스·디버깅, A1 셀프호스트·env 미설정 시 no-op)에 동시 기록합니다. 테넌트는 자기 사용량을, 오퍼레이터는 전체를 봅니다. **임베딩은 httpx 직접 호출이라 langchain 콜백이 못 잡으므로**, 단일 경계(`get_embeddings`)에서 Langfuse generation을 수동 발행해 LLM과 대칭으로 트레이스에 띄웁니다(Provider 검증 프로브 임베딩 포함 — OpenAI 과금분 누락 0). **per-tenant 1급 필터**: LLM·임베딩 트레이스에 `tenant:{id}` 태그 + native sessionId(=ChatSession)를 달아 Langfuse 대시보드에서 테넌트별·세션별로 바로 거릅니다. **GraphRAG 검색 관찰성**: 오퍼레이터는 `local_search`·`source_search`를 Langfuse retrieval span으로 봅니다(어떤 청크가 검색됐는지). **테넌트**는 Langfuse 접근이 없으므로, 어드민 콘솔 세션 상세의 **대화 내역에서 질문↔답변 사이에 턴별 실행 노드 흐름(흐름 노드 id: start→search→answer→…, #72 이전 턴은 local_search→call_llm→…)과 검색 근거를 접이식으로**(질문→검색→답변 순으로 원인-결과 추적) 직접 봅니다. 노드 흐름은 메타데이터에 노드명이 없어 체크포인트의 `versions_seen`(노드별 채널 소비 버전)을 스텝 간 비교해 역산하므로, **기존 세션에도 소급 적용**됩니다 — 휴지 체크포인트는 `_clear_transient`로 `rag_chunks`를 비워 슬림 유지하지만(prefix 캐시 설계와 정합 — RAG는 캐시 분기점 뒤 trailing 턴에만 실림), 검색 결과는 **체크포인트 히스토리의 중간 단계에 남아 있어** 새 저장 없이 그걸 복원해 노출합니다. 모든 Langfuse 발행은 가드형·예외안전이라 미설정/장애 시 챗·임베딩을 절대 막지 않습니다.
- **마크다운 챗버블**: AI(assistant) 메시지는 위젯·admin 모두 마크다운으로 렌더합니다(굵게·리스트·코드블록·표·링크, GFM). 스트리밍 중에도 라이브로 서식이 입혀집니다(react-markdown). 방문자 입력·상담원·시스템 안내는 평문 유지. 보안: raw HTML 미파싱·이미지 비활성·링크는 새 탭 + `rel="noopener noreferrer nofollow"` + http/https/mailto만 허용(`javascript:`·`data:` 차단)으로 3자 사이트 iframe에서 AI 출력의 XSS를 막습니다.

### HITL (Human-in-the-Loop)

- **활성화**: Tenant가 `hitl_enabled`로 켜고 끕니다(기본 켜짐). 끄면 AI 전용으로 운영되며 escalation이 발생하지 않습니다.
- **트리거**: (1) LLM이 구조화 출력으로 `needs_hitl: true` 반환(불확실 판단 + 상담원 요청 키워드 통합), (2) 방문자의 명시적 상담원 요청.
- **상태**: Escalation `pending` → `claimed` → `resolved`. trigger_type은 `ai`/`visitor`/`agent`(수동 takeover).
- HITL 모드에서는 AI가 침묵하고, 팀원이 "수락하기"로 클레임한 뒤 메시지를 보냅니다. "AI에게 넘기기"로 다시 AI 모드로 복귀합니다.
- Escalation 발생 시 Slack/Discord/Generic **웹훅**으로 알림을 보냅니다.
- **상담 가능 시간(영업시간)**: Tenant가 타임존 + 요일별 시간창 + 휴일(예외 날짜)을 설정하면, 그 시간 외에는 그래프가 plain으로 컴파일돼 **AI 자동 escalation이 일어나지 않고** 운영 안내만 곁들여 AI가 계속 답합니다. 미설정 시 24/7(opt-in 하위호환). 저장 때 형식을 검증한다(HTTP 400·MCP `invalid_settings`, 규칙은 `business_hours.hours_error` 하나): 시간대는 IANA 이름, 시간표는 `{mon: {enabled, start: "HH:MM", end: "HH:MM"}}`(켠 요일은 start<end, 키는 mon~sun), 휴일은 `YYYY-MM-DD` 목록. 이미 저장된 나쁜 값은 예외 없이 경고 로그 + 「제한 없음」(24/7)으로 본다. 수동 takeover는 시간과 무관하게 항상 가능합니다.
- **세션 콘솔 + 임의 takeover**: HITL 탭이 세션 콘솔로 진화 — 상단에 진행 중 상담(escalation) 카드, 하단에 **다른 세션 목록**(SSE 연결된 활성 세션 우선)을 보여줍니다. 팀원은 escalation이 없는 임의 세션도 **"내역 보기"로 takeover 없이 채팅 내역을 읽기 전용으로 펼쳐** 확인하거나, **"상담 시작"으로 직접 takeover**(자동-claimed `agent` escalation)할 수 있습니다. **흐름 칸·필터(#249, E4)**: 다른 세션 행·상담 카드·방문자 세션 목록에 그 대화의 흐름 이름(`ChatSession.flow`, 옛 세션은 「—」)이 보이고, 콘솔 머리와 방문자 세션 목록의 「흐름」 선택(흐름이 둘 이상일 때 나타남)이 `flow_id`로 거릅니다(`GET /api/tenant/sessions/`·`/visitors/{id}/sessions/`·`/escalations/`의 `?flow_id=`). 사람 넘김 큐는 하나이고 필터만 더합니다. 남의 테넌트 흐름 id·UUID 아닌 값은 빈 목록(존재 여부를 흘리지 않음)이며, 「흐름 없음」 선택지는 두지 않았습니다(옛 세션은 E1 마이그레이션이 채움, 새 세션은 기본 흐름 없는 테넌트에서만 null). 활성 여부는 Redis presence(SSE keepalive로 갱신되는 TTL = 자가치유)로 판단하고, 연결/해제는 `hitl:{tenant}` 채널로 콘솔에 실시간 push됩니다. 위젯 새로고침처럼 옛/새 SSE 연결이 겹칠 때 콘솔이 "유휴"로 잘못 뒤집히지 않도록 **연결을 참조 계수**해 세션의 첫 연결(0→1)·마지막 종료(1→0) 전이에서만 콘솔 델타를 발행합니다. 델타는 ephemeral pub/sub이라 콘솔이 그 순간 미구독(탭 전환·재연결·배포)이면 놓칠 수 있으므로, **콘솔은 자가치유 진실원천(`active_sessions`, TTL 20s)을 주기적으로(15s) 재조정**해 놓친 disconnect로 "접속중"이 멈추는 일을 자동 보정합니다.
- **Event-Driven 파이프라인**: HITL 라이프사이클 전이(Escalated/Claimed/TakenOver/Resolved)는 **Transactional Outbox**로 상태 변경과 한 트랜잭션에 이벤트로 기록되고, **relay 싱글톤**(Redis pub/sub wake로 깨어 드레인)이 **EventBus**(Redis Streams, 추후 Kafka 교체 가능)로 발행합니다. webhook·방문자 SSE(hitl_start/end)·콘솔 델타·presence가 **디커플링된 소비자**로 이 이벤트에서 파생되어, dual-write 유실 없이 at-least-once+멱등+DLQ로 동작합니다. 설계·원리: **[docs/event-driven-architecture.md](docs/event-driven-architecture.md)** · 운영(프로세스·dead-letter 리플레이): [docs/event-pipeline.md](docs/event-pipeline.md).

### Visitor 접근 & 신원 (공개 Slug URL)

옛 EmbedToken(per-session 서명 토큰) 방식은 폐지되고, **공개 `/chatbot/{slug}/` URL**로 대체됐습니다([ADR-0011](./docs/adr/0011-public-slug-url-replaces-embed-token.md)). Tenant는 발급 단계 없이 iframe만 박으면 됩니다.

- **흐름별 주소(#246, [ADR-0034](./docs/adr/0034-per-flow-embed-key-default-flow-session-key.md))**: `/chatbot/{slug}/`는 **기본 흐름**(`Flow.is_public`, 테넌트당 하나·보관 불가)이 답하고, `/chatbot/{slug}/{embed_key}/`는 그 흐름이 답합니다. `embed_key`는 흐름을 만들 때 발급하는 8자 소문자·숫자이고 바뀌지 않습니다. 키 주소는 「임베드 켬」(`embed_enabled`, 기본 꺼짐) + 배포 버전 있음 + 보관 아님일 때만 열리고(기본 흐름은 늘 열림), 아니면 `resolve`·`stream`이 없는 slug와 같은 404를 내며 위젯은 「지금 열려 있지 않은 상담입니다.」를 보이고 입력을 막습니다. 공개 API는 선택 인자 `flow=<embed_key>`를 받고 `resolve`는 `flow_name`도 줍니다. 위젯 세션은 (tenant, visitor, flow)마다 따로이고, 키 주소로 연 세션은 흐름의 임베드가 꺼지거나 보관되면 다음 메시지가 403 `Flow not open`입니다. 임베드를 켠 흐름을 보관하면 임베드도 꺼집니다. **켬/끔·기본 흐름 바꾸기(#247)**: `POST /api/tenant/flows/{id}/embed/`(`{enabled}`)·`POST …/default/`는 `flow.deploy`(Admin, Member 403)이고, 배포 버전이 없는 흐름은 켜기·기본 흐름 지정이 409 `not_embeddable`(이유가 `detail`)입니다. 기본 흐름 바꾸기는 한 트랜잭션에서 옛 기본 흐름을 풀고 새 흐름을 지정합니다(중간에 깨지면 옛 것 그대로). 흐름 응답에 `embed_key`·`embed_enabled`·`embed_path`가 더해졌고 `is_default` 별칭은 없습니다(`is_public`이 그 뜻 — MCP `list_flows`·`get_flow`에는 에이전트가 읽기 쉽게 `is_default`를 같은 값으로 더했다). admin의 흐름 목록 행·편집기 머리 「임베드」 패널(켬/끔·코드 복사·미리보기·「기본 흐름으로」, 코드 복사는 Member도)과 설정 > 보안 「내 사이트에 붙이기」 카드(기본 흐름 코드 + 임베드를 켠 흐름의 코드)로 붙입니다. 켠 흐름을 보관하려 하면 「붙여 둔 코드가 멈춥니다」 확인을 받습니다. 시험: `backend/tests/test_flow_embed_api.py`, vitest(`FlowEmbedPanel`·`FlowsTab`·`EmbedGuideCard`·편집기), 실제 스택 `e2e/tests/flow-embed-admin.spec.js`(폰 폭 포함). **허용 사이트(#248)**: 위젯을 iframe으로 붙일 수 있는 사이트를 테넌트 목록(`TenantConfig.embed_origins`, 설정 > 보안 카드)과 흐름 목록(`Flow.embed_origins`, 흐름 「임베드」 패널 — 비면 「테넌트 목록을 따름」)으로 정합니다. 둘 다 `flow.deploy`(Admin)이고 API는 `PATCH /api/tenant/config/`의 `embed_origins`·`PUT /api/tenant/flows/{id}/embed-origins/`(`{origins}`, 틀리면 400 `invalid_embed_origins`)입니다. 항목은 `scheme://host[:port]`(하위 도메인 `https://*.example.com`, http는 localhost·127.0.0.1만·포트 `*`도, 최대 20개, 공백·세미콜론·쉼표·따옴표·키워드 거부, 소문자·중복 제거)이고 검증은 `apps/flows/embed_policy.normalize_embed_origins` 한 곳입니다. nginx가 `/chatbot/`마다 `auth_request`로 `/_internal/embed-policy`에 물어 `Content-Security-Policy: frame-ancestors …`를 붙입니다 — 목록이 비면 `*`(도입 전과 같음), 아니면 `'self'` + 목록, 흐름 목록은 그 흐름의 키 주소에서만 테넌트 목록을 **대신**하고(합치지 않음) 기본 주소 `/chatbot/<slug>/`는 테넌트 목록만 봅니다. 없는 slug·키는 `'self'`. 값은 Redis에 60초 캐시하고 저장하면 지웁니다. 정책 엔드포인트가 실패하거나 2초 넘게 늦으면 `*`로 엽니다(fail-open — 위젯이 깨지지 않게). **한계: 붙이기 통제이지 접근 통제가 아닙니다** — `frame-ancestors`는 다른 사이트가 iframe으로 붙이는 것만 막고, 주소를 직접 열거나 API를 부르는 것은 막지 않습니다. 운영 확인은 `docs/deployment.md` 18번. 시험: `backend/tests/test_embed_origins.py`, nginx `bash scripts/test-nginx-embed-policy.sh`(가짜 정책 서버로 성공·5xx·404·403·시간 초과·앱 없음·바깥 호출·`nginx -t`), vitest(`FlowEmbedPanel`·`EmbedGuideCard`), 실제 스택 `e2e/tests/flow-embed-origins.spec.js`(운영 nginx 설정을 올린 `nginx-e2e` 경유 — 헤더, 다른 출처 iframe 막힘·`'self'` 뜸, 화면 편집). **MCP(#250)**: 같은 일을 에이전트가 하는 도구 `set_flow_embed`·`set_default_flow`·`set_embed_origins`(스코프 `flow.deploy`·`FLOW_DEPLOY`·2단계·감사)와 읽기 칸(`list_flows`·`get_flow`의 임베드 칸, `get_settings`의 `embed_origins`)이 있습니다 — REST 서비스·검증 함수를 그대로 부릅니다. 시험: `backend/tests/test_mcp_embed_tools.py`, 종합 e2e `e2e/tests/flow-embed-overall.spec.js`(한 테넌트의 두 흐름을 서로 다른 키 주소로 붙여 각자 답하고 세션이 따로이고 기존 주소는 기본 흐름이 답한다).
- **Tenant Slug**: 표시명과 분리된 고유·URL-safe 공개 식별자. Tenant가 어드민에서 설정. **완성형 한글**(예: `/chatbot/우리가게/`)·영문(대소문자)·숫자·하이픈 허용. NFC 정규화 + 대소문자 무시로 조회·중복을 처리합니다(`MyStore`↔`mystore`).
- **Visitor 신원 — 계층형**:
  - *익명*: 위젯이 생성·localStorage에 저장하는 **Anonymous Visitor ID**(세션 넘어 지속).
  - *식별 기본*: `?visitor_id=` 평문(마찰 0).
  - *식별 보안(opt-in)*: Tenant가 "신원검증 요구"를 켜면, `HMAC(tenant secret, visitor_id)` 해시를 요구·검증해 위조를 막습니다. 해시는 안정값이라 유저당 1회 계산해 캐시하며, Operator의 HMAC API(`POST /api/chat/identity`, TENANT_KEY 인증)로 받거나 직접 계산합니다.

---

### 흐름(Flow) 모델과 실행기 (ADR-0027)

테넌트가 그리는 상담 흐름은 `backend/apps/flows/`에 있습니다. **Flow**(테넌트당 하나를 「공개 흐름」으로 지정)는 고칠 수 있는 **초안** 하나와 **바뀌지 않는 Flow Version**들을 가지며, 배포 중 버전 하나를 가리킵니다. Flow Version은 만들 때 흐름 JSON을 검증하고 정규화 JSON의 SHA-256 해시를 남기며, 저장 뒤 수정은 예외입니다. 새 **ChatSession**은 시작 때 공개 흐름의 배포 중 버전에 고정됩니다(`flow_version`, 배포가 진행 중 대화를 바꾸지 않음). 흐름 JSON 형식·검증 오류 코드·순환 규칙(순환은 방문자 답을 기다리는 노드를 지나야 함)은 [docs/flow-json.md](./docs/flow-json.md). **실행기**(`apps/flows/executor.py`, #23)는 흐름 JSON을 LangGraph StateGraph로 짓고(흐름 해시로 캐시) 한 턴을 돌립니다. 노드 종류마다 핸들러(`apps/flows/handlers.py`, 계약은 `contract.py`)가 상태를 바꾸고 갈래를 고르며, 한 턴 노드 상한은 50입니다. 지금 핸들러는 시작·지식 검색·AI 답변·사람 넘김·AI 분류·추출·조건·가드레일·HTTP 도구 여덟이고, 기존 챗 노드 함수(local search·원문 폴백·call_llm·escalation)를 그대로 불러 씁니다 — LLM Provider는 기존 `chat_provider`가 고릅니다. **AI 분류·추출**(#25, `apps/flows/classify.py`)은 노드 config의 라벨 목록·추출 필드로 구조화 출력 스키마를 만들어 같은 Provider로 한 번 묻고 `label`·`fields`·`confidence`(0~1)를 상태에 쓰며, **조건**은 `when` 식(`apps/flows/expr.py`, `eval` 없는 작은 식 언어 — 문법은 docs/flow-json.md)을 위에서부터 계산해 처음 참인 갈래로(없으면 `else`) 보내고 판단 기록에 고른 갈래와 이유를 남깁니다. AI 답변의 `instructions`는 프롬프트 끝에 실리고, **사람 넘김이 결정된 턴엔 모델 답을 방문자에게 흘리거나 저장하지 않습니다**(넘김 안내만; 스키마에서 `needs_hitl`이 `response`보다 먼저 나옴). **가드레일**(#26, `apps/flows/guardrail.py`)은 나가는 AI 답변이나 들어오는 방문자 입력을 금지어·개인정보(전화번호·주민등록번호·카드번호·이메일 — 결정적 정규식)·가격 약속(LLM 판정)으로 검사해 막기(정해 둔 문구)·다시 쓰기(LLM)·가리기 중 하나로 처리하고, 판단 기록에 걸린 규칙과 처리를 남깁니다(걸린 원문은 남기지 않음). 바로 뒤에 출력 가드레일이 붙은 AI 답변은 답을 토큰으로 흘리지 않고 모아 두었다가 통과한 글을 한 번에 내보냅니다(ADR-0027 보충). **기본 흐름**(시작 → 지식 검색 → AI 답변 →[AI가 넘김 판단 시] 사람 넘김, `apps/flows/defaults.py`)은 옛 코드 그래프(build_graph)와 같은 동작이며, 흐름 없는 테넌트에 배포 중·공개로 시드됩니다(데이터 마이그레이션 `flows/0002`, 온보딩·Operator 테넌트 생성). **운영 챗(taskiq worker-chat 한 턴)은 실행기로 돕니다**(#72, 진입 `apps/flows/chat_turn.py`): 세션에 고정된 버전의 흐름을 돌리고, 고정 버전이 없는 기존 세션은 첫 턴에 공개 흐름의 배포 중 버전에 고정되며, 공개 흐름이 없으면 기본 흐름으로 돕니다. 체크포인트 자리(thread_id=세션)는 그대로라 옛 그래프 체크포인트의 대화 기록 위에 이어 쌓입니다. `await_reply` AI 답변에서 턴이 끝나면 다음 턴은 그 노드의 나가는 선에서 시작합니다. 세션 상세의 「실행 노드」는 흐름 노드 id(`start`·`search`·`answer`·`handoff`, 턴 끝 저장 `__flow_finish__`)로 보입니다.

**편집기(#29, `/tenant/flows`·`/tenant/flows/:id`)**: `@xyflow/react`(MIT) 캔버스 — 왼쪽 노드 목록(눌러서 더하거나 캔버스로 끌어다 놓기), 가운데 모눈 캔버스, 오른쪽 노드 설정 패널. 노드는 N-코드를 달고 가드레일은 황·흑 경고띠, 사람 넘김은 적색 점선, 고른 노드까지 오는 길은 호박색으로 보입니다(`FlowCanvas`의 `activeNodeIds`·`activeEdgeKeys`로 시험 대화의 실행 경로를 같은 방식으로 강조 — #30). 모든 편집(노드 추가·삭제, 선 잇기·끊기, 위치 이동, config 수정)은 서버의 패치 연산(`PATCH …/draft/`)으로 곧바로 초안에 저장하고, 응답의 검증 오류(노드 id·코드·이유)를 해당 노드의 오류 램프·툴팁과 패널에 보입니다. 오른쪽 패널은 바깥 탭 「시험 대화 / 판단 기록 / 노드 설정」(노드 설정 안의 「폼 / 고급」과 별개)입니다. **시험 대화(#30)**: 저장된 초안으로 `POST …/flows/{id}/test/`(SSE: `start` → 턴 도중 실시간 `node_start`·`node_end`|`node_error`×N → `trace`×N·`reply`·`done` — 노드 이벤트는 #239·[ADR-0035](./docs/adr/0035-executor-node-event-hook.md), `node_end`는 시간·토큰·`cost_usd`·`unpriced_models`·종류별 한 줄 `summary`를 싣고 판단 기록과 같은 비밀 가림을 거침, 노드 예외면 `node_error` 뒤 `done`{ran:false, code:`turn_error`})를 `fetch` 스트림으로 읽고(POST+Authorization이라 EventSource 불가, `admin/src/lib/testStream.ts`), 응답의 `session_id`를 다음 턴에 다시 보내 같은 대화로 이어 가며(await_reply 재개) 「새 대화」로 비웁니다. 체험 턴을 1 쓰고, 체험 한도·플랫폼 키 없음·초안 오류는 사람이 읽는 문구로, `flow.edit`(Admin)가 없는 멤버는 입력이 막힌 읽기 전용입니다. 시험 SSE는 이벤트가 없는 동안 `TEST_SSE_HEARTBEAT_SECONDS`(기본 15초)마다 SSE 주석 줄(`: heartbeat`)을 보내고(소비자는 `data:` 없는 블록을 건너뜀), nginx는 이 경로에만 `proxy_read_timeout 300s`를 둡니다(#253). **판단 기록**: 턴별 노드 항목(모노 글꼴, 이유·입력·출력, 토큰 입력/출력·비용·시간)과 턴·세션 합계 — 토큰을 썼는데 단가를 모르면 「단가 모름 (모델 X)」, 일부 호출만 모르면 아는 부분의 합에 「일부 미포함 (모델 X)」를 붙임(턴·세션 합계도 같음, #235), 내부 끝 노드(`__flow_finish__`)는 「끝」으로 표시하고 캔버스에는 없으니 강조하지 않습니다. 항목을 누르면 그 노드를 고르고 그 턴이 지나간 노드·선(`from:handle`)을 호박색으로 강조합니다(순수 로직 `admin/src/lib/testTrace.ts`). **실시간 노드 상태(#240)**: 턴이 도는 동안 `node_start`·`node_end`·`node_error`를 순수 규칙 `admin/src/lib/liveRun.ts`(`applyLiveEvent`)로 접어 캔버스에 보입니다 — 실행 중 노드는 맥동·빛나고, 끝난 노드는 ✓, 오류 노드는 빨강 ✕(`trace`가 없는 오류 턴이어도 새 턴·「새 대화」까지 남음), 지나간 선(`node_end.handle`)은 초록으로 흐릅니다(`data-live` 속성, `prefers-reduced-motion`이면 애니메이션 없이 색만). 노드별 상태에 `node_end` 값(시간·토큰·비용·summary)과 같은 노드를 지난 횟수(`count`)를 보관합니다. **노드 배지(#241)**: 끝난 노드 위에 마지막 턴의 값을 작은 표로 답니다 — 시간(`120ms`·`1.5초`)·토큰(입력/출력)·비용·서버가 만든 한 줄 `summary`, 같은 노드를 여러 번 지났으면 `×N`, 쓴 토큰이 있는데 단가를 모르면 판단 기록과 같은 문구 「단가 모름 (모델 X)」·「일부 미포함」(`formatCost` 재사용). 문구 규칙은 순수 함수 `admin/src/lib/nodeBadge.ts`. 배지는 `pointer-events: none`이라 선택·끌기·연결을 막지 않고, 폰 폭(`sm` 미만)에서는 감춥니다. 오류 노드엔 배지가 없습니다(빨강 ✕만). 새로고침 복원 때는 마지막 턴의 저장된 판단 기록으로 배지를 다시 그립니다(`liveRunFromEntries`) — 저장 기록엔 `summary`가 없어 이때는 시간·토큰·비용만 보입니다. 턴이 끝나면 시험 대화 탭에서도 그 턴의 호박색 경로로 이어지고, 화면 밖 노드가 실행 중이면 캔버스가 따라가되 그 턴에 사용자가 직접 캔버스를 움직였으면 따라가지 않습니다. /try·폰 하단 시트도 같은 `FlowEditorPage`라 그대로 적용됩니다. 시험: `liveRun.test.ts`, `FlowEditorPage.testChat.test.tsx`, 실제 스택 `e2e/tests/flow-live-canvas.spec.js`·`node-badge.spec.js`. 새로고침해도 남습니다 — 마지막 시험 세션 id를 초안(흐름)별로 브라우저에 기억(`flow-test-session:{flowId}`)하고 열 때 `GET /api/tenant/sessions/{id}/decision-traces`로 복원합니다(세션이 없으면 기억을 지움, 「새 대화」도 지움). 왼쪽 버전 이력은 최신 순이고 만든 이 없는 시드 v1은 「시스템(기본 흐름)」입니다. 종류별 설정 폼은 「노드 설정」 탭입니다. 순수 규칙(N-코드 배정·갈래·로컬 패치 적용·경로 찾기)은 `admin/src/lib/flowGraph.ts`. `flow.edit`가 없는 멤버(Member)는 읽기 전용입니다.

**편집기 높이·안내(#103)**: 대시보드 셸은 `md` 이상에서 뷰포트 높이로 고정되고 `main`이 flex 열이라, 편집기 3단(`lg`)은 체험 배너·제목 줄을 뺀 남은 높이를 `flex-1 min-h-0`로 채웁니다(`e2e/tests/flow-editor-height.spec.js`: 배너 있는 1600×900·1280×720에서 캔버스 컨트롤이 뷰포트 안). 「버전 저장」이 중복 가드로 새 버전을 안 만들면 적은 메모를 남기고, 대기 배포 요청이 닫히면 「배포를 요청했습니다」 안내를 지우며, 시험 턴이 끝나면 `notifyTrialChanged`로 상단 체험 배너가 남은 턴을 다시 읽습니다. 사용량 차트는 진입 애니메이션을 끄고(스크린샷이 도중 프레임을 찍어 막대가 짧게 보였음) 눈금을 K·M으로 줄입니다(`admin/src/lib/usageChart.ts`).

**흐름 보관(#126)**: 흐름 목록의 「보관」 버튼(Admin만 보임, `FlowsTab`) — 누르면 같은 줄에서 「보관하면 목록에서 사라집니다」 확인을 한 번 받고 `DELETE /api/tenant/flows/{id}/`를 보낸다. 위젯이 쓰는 공개 흐름에는 버튼이 없고(서버도 409), 서버 거절 문구는 그대로 보인다. 보관 해제 화면은 없다(`POST …/restore/`).

**버전·배포(#31)**: 편집기 왼쪽 아래 「버전 이력」(`VersionsPanel`) — 「버전 저장」(메모 선택, 같은 내용이면 새 버전 없음), 버전마다 「보기」(그 버전 그래프를 읽기 전용 캔버스로, 「초안으로 돌아가기」까지 초안 편집·저장 막힘)와 「배포」, 배포 중 버전 표시·메모·만든 이·시각, 「되돌리기」(배포 중 버전 바로 앞 번호를 승인 없이 한 번에). 시험 대화를 한 번도 하지 않고 배포하면 인라인 확인을 한 번 더 받습니다. 설정 「배포」 탭의 「배포에 관리자 승인 필요」(Admin만 바꿈)를 켜면 「배포」가 「배포 요청」으로 바뀌고(`202`), 대기 요청은 편집기 이력 위·흐름 목록 위 「배포 요청」 구획에 승인/거절 버튼과 함께 보입니다 — 요청자 본인은 승인 버튼이 꺼지고 이유가 글로 보이며(서버가 `self_approval`로 한 번 더 막음), 권한 없는 멤버는 버튼이 꺼지고 이유가 보입니다. 순수 규칙(버튼 이름·꺼짐·이유)은 `admin/src/lib/deploy.ts`. 노드 카드는 갈래가 여럿이면 제목·요약 아래 갈래마다 한 줄을 따로 둬 갈래 이름과 제목이 겹치지 않습니다. E2E 시드(`seed_e2e_member --serve-tenants-from`)는 조직마다 둘째 Admin 토큰(`<id>.admin2.token`)을 쓰고, 요청 둘째 줄에 `default-flow`가 있으면 공개·배포 중 기본 흐름도 만듭니다(`e2e/tests/flow-versions.spec.js`).

**판단 기록(Decision Trace, #24)**: 한 턴이 끝나면 실행기가 지난 노드마다 `{node_id, kind, handle, record(입력·출력·분기 이유), duration_ms, input_tokens, output_tokens, cost_usd, calls}`를 `DecisionTrace` 한 행(턴당 insert 한 번, `apps/flows/trace.py`)으로 남깁니다 — 운영 턴과 시험 턴(서비스 `run_test_turn`·MCP `test_turn`)이 같은 형식이고 `is_test`로만 구분합니다. 핸들러는 `NodeOutcome(record={…})`로 노드별 기록을 돌려줍니다. 토큰은 LLM 경계가 Provider 응답의 usage를 `report_llm_usage`로 보고한 것을 노드 단위로 모은 값이고(OpenAI 호환 클라이언트는 `stream_usage=True`로 스트리밍에도 `stream_options.include_usage`를 실어 usage를 받는다 — base_url을 지정하면 langchain 기본이 꺼져 플랫폼 키·custom 경로가 0이던 버그, #86), 비용은 **모델 단가 표** `apps/usage/model_prices.toml` (하루 한 번 호스트 cron의 `python manage.py check_model_prices`가 LiteLLM 단가 파일과 대조해, 최근 7일에 쓰였는데 표에 없는 모델·1% 넘는 단가 차이·LiteLLM에 키 없음·파일 못 받음/형식 바뀜을 stdout·로그·종료 코드(1/2)와 선택 웹훅 `PRICE_ALERT_WEBHOOK_URL`·`_TYPE`으로 알린다. 표는 자동으로 고치지 않는다, #236, `docs/deployment.md`)(100만 토큰당 USD, 출처·기준일 주석; 플랫폼 `gpt-4o-mini`·`text-embedding-3-small`과 현행 Claude 모델 포함 — 공급자가 돌려준 `model`은 `apps.usage.pricing.canonical_model(name)`이 표 이름으로 맞춘다 — 소문자로 → 공급자 앞머리(`anthropic/`·`openai/`·`anthropic.`)와 `@날짜` 꼬리 떼기 → 정확히 같은 이름 → 가장 긴 앞부분 이름(`-20251001`·`-2024-07-18`) → 없으면 `None`(단가 모름); 비용 계산·`is_priced`·일일 대조가 이 함수 하나를 쓴다.  `claude-haiku-5-5`는 프롬프트 10만 토큰 이하 단가만 담음, #234)로 계산합니다 — 표에 없는 모델의 호출은 호출별 `cost_usd`가 `null`이고 0으로 치지 않습니다. **합계의 의미(#235)**: 노드·턴 `cost_usd`는 **단가를 아는 호출의 합**이고, 모르는 호출이 있으면 항목에 `unpriced_models`(모델 이름들)가 붙습니다(턴 응답에도 같은 키 — HTTP `decision-traces`, MCP `get_session`·`get_session_trace`·`test_turn`). 호출이 전부 단가 모름이면 `cost_usd`는 `null`입니다. 이전에는 하나라도 모르면 합계 전체가 `null`이었고, 그 예전 기록(`DecisionTrace`)은 읽을 때 `calls`로 아는 부분 합과 모르는 모델을 다시 구해 새 기록과 같게 보입니다(`trace_query`, 화면 `testTrace.ts`도 `calls`를 우선 읽음). 기존 `TokenUsage` 일 롤업에도 `cost_usd` 합계와 `unpriced_requests`(기록할 때 단가가 없어 합계에 못 더한 호출 수) 칼럼이 있습니다 — 나중에 표에 단가가 생겨도 이 값이 남아 「일부 미포함」이 유지되므로 조용히 0원이 되지 않습니다. 마이그레이션 `usage/0003`은 기존 행을 채웁니다: 지금 단가가 있는 모델의 `cost_usd=0` 행(#234 이전에 0으로 굳은 `claude-haiku-5-5` 등)은 그 행의 토큰으로 지금 단가를 곱해 다시 계산하고, 지금도 단가가 없는 모델의 행은 `unpriced_requests=request_count`로 채웁니다(단가 표 파일을 읽어 계산 — pricing 모듈은 import하지 않음). 지식 검색의 질의 임베딩도 `record_embedding_usage`가 노드 수집기(`note_call`)에 한 번, 롤업에 한 번 보고해 노드 판단 기록에 토큰·비용이 잡힙니다(이중 집계 없음). **다시 계산(#244)**: `python manage.py reprice_usage`(기본 dry-run, `--apply`로 쓴다)가 `unpriced_requests>0`인데 지금은 단가가 있는 `TokenUsage` 행을 토큰×지금 단가로 다시 계산하고 플래그를 0으로 둔다. `--fix-mismatch`는 `cost_usd`가 계산값보다 (호출 수×0.000001) 넘게 작은 행(#235 이전에 섞인 행)도 고친다 — 큰 쪽·단가 모르는 모델 행은 건드리지 않고, 구간 단가 모델은 기록 때와 같은 근사다(절차: `docs/deployment.md`). **사용량 화면**(테넌트·Operator)은 `cost_usd`·`unpriced_models`(단가 모름 모델)·`unpriced_requests`(합계에 못 더한 호출 수)·일별 `has_unpriced`를 받아 총 비용·유형/테넌트별·일별 비용을 보이고, 모르는 호출이 있으면 모델 이름과 함께 「일부 미포함 · 단가 모름」·합계가 최소값임을 알립니다. MCP `get_usage`도 같은 `unpriced_models`·`unpriced_requests`를 담습니다. 체험 한도 계산(`try_session.turn_cost`)은 모르는 모델의 호출을 플랫폼 단가로 치는 규칙을 그대로 둡니다. custom LLM Provider는 스트리밍 사용량 집계를 위해 `stream_options(include_usage)`를 보냅니다(설정 도움말에 안내). 저장 직전 비밀 가림 훅(`trace.REDACTORS`, 기본은 Tenant Secret 가림 — #33)을 모든 문자열에 적용하고 문자열 하나는 2000자에서 잘라 저장합니다(전문은 ChatMessage에 있음). 저장 실패는 방문자 답을 막지 않습니다. 조회: `GET /api/tenant/sessions/{id}/decision-traces`(멤버 인증·테넌트 범위, 시험 세션 포함); MCP `get_session`은 최근 20턴의 합계만(`decision_traces`), `test_turn`은 노드별 상세를 `trace`에 싣습니다.

**HTTP 도구 보안 계층(#32, ADR-0029)**: 서버가 밖으로 부르는 새 요청은 `apps/core/safe_http.py`의 `SafeHttpClient(AllowList([...])).request(method, url, headers=, body=)`만 씁니다(일반 `httpx`·`requests` 금지 — `tests/test_safe_http.py`가 `apps/` 안 네트워크 import를 기존 모듈 목록으로 묶어 새 우회를 막음). 홉마다 URL 검사(HTTPS만, IP 리터럴 — `127.1`·정수 표기 포함 —·`user@host`·443 외 포트 거부, 허용 목록은 정확 일치 또는 `*.example.com` 한 단계 하위만) → 해석 → 해석한 **모든** 주소 검사(사설·루프백·링크로컬·메타데이터·CGNAT·미지정·브로드캐스트·멀티캐스트·ULA·IPv4 매핑/NAT64/6to4, 하나라도 걸리면 거부) → **검사한 IP로 TCP 연결**(SNI·인증서 검증·Host는 원래 호스트, `http.client` 기반이라 `HTTPS_PROXY` 등 무시). 리다이렉트는 직접 따라가며 매번 다시 검사하고 최대 3회, 다른 호스트로 가면 `Authorization`·`Cookie`를 뗍니다. 해석·손잡기·헤더·본문·리다이렉트를 합친 전체 **5초**(마감 시각에 소켓을 끊는 감시자 — 조금씩 흘리는 서버도 막음), 본문 **256KB**(스트리밍으로 세다 끊고, gzip은 해제 후 크기). 실패는 `SafeHttpError.code`(`not_https`·`ip_literal`·`userinfo`·`non_default_port`·`host_not_allowed`·`dns_failed`·`blocked_address`·`connect_failed`·`tls_failed`·`timeout`·`too_large`·`too_many_redirects`·`bad_redirect`·`bad_response`·`invalid_request`·`invalid_url`)이며 문구엔 경로·쿼리를 담지 않습니다. `request(..., cross_host_redirects=False)`면 다른 호스트로의 리다이렉트를 따르지 않습니다(`cross_host_redirect`). 허용 목록 정책 클라이언트는 `safe_http.allow_list_client(allow, limits)`(해석·연결·TLS는 `NETWORK` 자리 — 시험이 대역으로 바꿈).

**HTTP 도구 노드(#34, ADR-0029 보충)**: `apps/flows/http_tool.py`(핸들러는 `handlers.py`). 테넌트 허용 도메인(`tenant_secrets.allow_list_for`)으로 위 계층을 불러, 2xx면 `save`의 응답 JSON 경로(`$.a.b[0]`) 값을 상태 `fields`에 더하고 `next`로, 차단·시간 초과·크기·HTTP 오류·JSON 아님·없는 비밀이면 `error` 갈래로 갑니다(선택 — 선이 없으면 턴 끝, 이유 코드는 판단 기록 `record.error`). URL 호스트는 틀의 고정 부분이고 `{{state.…}}` 값은 한 조각으로 퍼센트 인코딩(`/`·`?`·`#`·`.`까지), 본문에는 JSON 문자열 안쪽으로 이스케이프돼 들어갑니다. 헤더는 고정 글과 `{{secret.…}}`만. 비밀을 헤더·본문에 실은 요청은 다른 호스트 리다이렉트를 따르지 않습니다(`X-Api-Key`나 307 본문이 새 호스트로 따라가지 않게). 판단 기록·상태 값은 이 테넌트 비밀(원문·파생값)로 가리고 헤더·본문은 기록하지 않습니다. config·참조 문법·이유 코드 표는 [docs/flow-json.md](./docs/flow-json.md) 「HTTP 도구」. 편집기 폼은 #123.

**가짜 API(#131, ADR-0029 보충)**: 예제 흐름·`/try`용. HTTP 도구 `url`이 `https://demo-api.flow.sandcorp.co.kr`(`apps/flows/demo_api.HOST`) 글자 그대로면 `http_tool._send`가 네트워크 없이 `apps/flows/demo_api.py`의 결정적 핸들러로 처리합니다(DNS 조회·연결 0, 허용 도메인 불필요). `GET /orders/{번호}`(끝자리로 배송 중·완료·준비 중, `0`은 없음), `POST /reservations`(`date`·`level` → 확정 번호; `POST /inquiries`는 #164에서 지움 — 모르는 경로 404), 실패 흉내 `9500`(500)·`9408`(timeout)·`?simulate=error|timeout`. 응답은 `SafeResponse`라 같은 `LIMITS`·`save`·실패 코드 규칙을 거칩니다. 대문자·포트·하위 도메인 등 변형은 일반 safe_http 경로. 편집기는 이 주소에 허용 목록 경고 대신 「가짜 API」 안내(`isDemoApiUrl`, 「배포 전 실제 주소로 바꾸세요」 — 체험에서 옮긴 흐름도 그대로 돌지만 예제 응답이다, #42). 모양은 [docs/flow-json.md](./docs/flow-json.md) 「가짜 API」.

**예제 흐름 셋·지식 묶음·Sandcorp 시드(#35)**: `apps/flows/example_catalog.py`의 `EXAMPLES`(`sandcorp_home`·`shop_support`·`academy_reservation`)가 `apps/flows/examples/<id>.json`과 `examples/knowledge/<id>/*.txt`를 읽는 고정 데이터입니다. HTTP 노드는 모두 가짜 API라 비밀·허용 도메인이 필요 없습니다. 지식 적재: `python manage.py seed_examples --tenant <slug|id> [--example <id>] [--force]`(멱등 — 문서 id가 테넌트·예제·이름에서 정해짐). Sandcorp 테넌트: `python manage.py seed_sandcorp [--update-flow]` — 공개 흐름 「Sandcorp 홈 상담」(예제 ① 그대로)을 만듭니다. 문의는 받지 않고 홈페이지 문의 폼으로 안내만 하며, 설정값·비밀·허용 도메인은 없습니다. `--update-flow`는 이미 있는 흐름을 예제 ①의 새 그래프로 버전 저장·배포·공개하고 지식 문서도 다시 적재합니다(#140: 회사 질문은 지식 검색을 거쳐 답하고, 문의 방법 질문은 `contact_request`로 바로 문의 폼 안내). 모양은 [docs/flow-json.md](./docs/flow-json.md) 「예제」.

**체험 테넌트·체험 도서관(Try Session 1/2, #134, ADR-0030)**: `apps/tenants/try_tenant.py`. `create_try_tenant(예제 id)`가 숨은 체험 테넌트(`Tenant.kind=try`)와 예제 흐름 사본(초안)을 만들고, `prune_expired()`(`python manage.py prune_try_tenants [--every 초]`)가 `TRY_TENANT_TTL_HOURS`(기본 24) 지난 체험 테넌트를 테넌트째 지웁니다. 체험 테넌트는 테넌트 Provider를 무시하고 플랫폼 키로만 돌고, HTTP 도구는 가짜 API만(그 밖은 `try_restricted`), 문서 업로드·배포·실제 사람 넘김이 막히며, 운영자 목록·멤버십·운영 사용량 집계에 나오지 않습니다. 지식은 공유 「체험 도서관」에 한 번 적재(`python manage.py seed_try_library [--example <id>] [--force]`, 플랫폼 키로 임베딩)하고 체험 테넌트는 자기 예제 문서로만 좁혀 검색합니다. 공개 API·쿠키·한도는 아래.

**Try Session 공개 API(2/2, #135, ADR-0030)**: `apps/tenants/try_api.py`(라우터 `/api/try`, 인증 없음)·`apps/tenants/try_session.py`. `GET templates/`(예제 목록) → `POST sessions/ {template, graph?}`(**첫 쓰기**가 부른다, ADR-0032: 체험 테넌트 + 세션 쿠키 `sf_try` — 추측 불가 ID, HttpOnly·Secure·SameSite=Strict·Path `/api/try/`, 서버엔 SHA-256만) → 쿠키로 `GET session/`, `GET flows/{id}/`(편집기와 같은 `FlowDetailOut`), `PUT|PATCH flows/{id}/draft/`, `POST flows/{id}/test/`(편집기와 같은 SSE, `done.code`에 사유), `GET node-schema/`. 배포 `POST flows/{id}/deploy/`는 403 `try_restricted`. 쓰기는 같은 호스트 Origin 또는 `TRY_ALLOWED_ORIGINS`만(아니면 403 `try_bad_origin`). 한도: 세션당 `TRY_SESSION_TURNS`(20)턴(`admit_turn`, 다 쓰면 `try_turns_exhausted`), IP당 하루 `TRY_SESSIONS_PER_IP_PER_DAY`(3)세션(429 `try_ip_limit`, 조건부 upsert), 전체 하루 `TRY_DAILY_BUDGET_USD`($5, `try_spend_days` 하루 한 행 — 정리에 안 지워짐, 넘으면 LLM 없이 `try_daily_budget` 「내일 다시」). 하루는 `TRY_QUOTA_TIMEZONE`(Asia/Seoul). 「흐름 보기」용으로 `GET templates/{id}/`(예제 이름·문서·그래프, 세션·한도 없음)도 있습니다(#37).

**/try 화면(#37)**: admin 앱의 로그인 없는 공개 라우트 `/admin-ui/try`(템플릿 고르기. `?template=home|shop|academy` 또는 전체 id면 카드 없이 그 템플릿 편집기를 곧바로 연다 — **방문 즉시 편집기**(#195): 진입은 `GET templates/{id}/`·`node-schema/`·`session/`만 읽고, 세션은 첫 쓰기(초안 저장·시험 대화·가입, 노드 위치 이동만은 제외)에 `lazyTryBackend`가 한 번만(single-flight) `POST sessions/ {template, graph?}`로 만든다(#196). 세션 전 편집(위치 이동 포함)은 지역 그래프에 쌓이고 생성이 그 그래프를 통째로 싣는다 — 서버는 PUT draft와 같은 `save_draft`로 사본 초안을 그것으로 만들고(틀려도 저장, 응답 `draft.errors/valid`), 한도 거절이면 테넌트·IP 수를 늘리지 않는다. `graph`가 없는 옛 요청은 템플릿 그대로. 거절(429 등)이면 화면은 되돌아가지 않고 「저장하지 못했습니다: …」가 보이며(시험 대화는 보낸 말과 입력이 남음) 다음 쓰기가 지금까지의 지역 그래프로 다시 만든다. 세션 전 머리줄은 「첫 저장 때 체험이 시작돼요」. 같은 템플릿 세션이 살아 있으면 그 사본을 연다(주소 그대로). **새로고침 유지(#197)**: 세션 전·거절 중 지역 그래프(위치 이동 포함)는 `sessionStorage['sandcorp-flow:try-draft:<template>']`에도 보관해 다시 열면 그것으로 연다(세션이 생기면 지움, 저장소 접근이 던지면 메모리만). **생성 직전 확인(Q6)**: `GET session/` 1회 — 같은 템플릿 세션이 이미 있으면(다른 탭) POST 없이 그 세션을 쓰고 `PUT draft`로 지역 그래프를 덮는다(방금 고친 편집이 이긴다). 다른 템플릿의 체험이 진행 중이면 편집기 위에 「여기서 고치면 새 체험으로 바뀝니다」를 보이고 첫 쓰기 전까지 POST 없음. **편집 중 만료(Q5)**: 새 편집기는 401 `try_no_session`/404에도 `/try?expired=1`로 튕기지 않고 「체험 시간이 지나…」 안내 + 지역 모드(편집 유지)로 돌아가 다음 쓰기에 새 세션을 만든다(옛 `/try/flow/:id`는 지금처럼 튕김)) · `/admin-ui/try/view/:id`(옛 읽기 전용 흐름 보기 — 이제 `/try?template=:id`로 replace 리다이렉트, #199) · `/admin-ui/try/flow/:id`(옛 주소·북마크 호환: 편집기+시험 대화+판단 기록, 만료되면 `/try?expired=1`). 카드 화면(`template` 없음)의 「고쳐 보기」는 `/try?template=`으로 이동만 하고 POST가 없으며, 「흐름 보기」 버튼은 없다. 결정은 [ADR-0032](./docs/adr/0032-try-session-created-on-first-write.md). 인증 게이트·대시보드 셸 밖이고(`TryShell`), 편집기를 별도 번들로 복제하지 않고 `FlowEditorPage`·`TestPanel`을 그대로 씁니다. 「체험 모드」는 `admin/src/lib/flowEditorBackend.tsx`의 `FlowEditorBackend` 컨텍스트 한 자리에서 주입합니다 — 기준 경로(`/api/try/flows/{id}/…`)·인증(Bearer 대신 `sf_try` 쿠키: `mutator.ts`가 `/api/try/`는 Authorization·refresh를 건너뜀, `testStream.ts`는 `base='/api/try'`)·숨길 UI(버전·배포 패널은 안내 한 줄로, 이름 바꾸기 없음)·저장된 판단 기록 복원 없음(SSE `trace`로만 그림). 한도: 머리줄에 남은 턴(`GET session/`, 턴이 끝날 때마다 `notifyTrialChanged`로 다시 읽음), SSE `done.code`는 `lib/tryRefusal.ts` — `try_daily_budget` 「내일 다시」(편집·재시도 계속), `try_turns_exhausted` 가입 유도(입력 막음), `try_unavailable` 준비 중. 401 `try_no_session`은 `flow:try-session-lost` 이벤트로 `/try?expired=1`로 돌려보내고, 429 `try_ip_limit` 사유는 카드 위에 보입니다. 「이 흐름으로 가입」은 아래(#42). **콘솔 401 정리(#150)**: 체험·공개 보기 백엔드는 `tenantLookups=false`라 HTTP 노드 폼이 `/api/tenant/allowed-domains/`·`secrets/`를 부르지 않고(허용 목록 밖 경고도 없음, 가짜 API 안내만), `bootSilentRefresh`는 `/try`·`/try/view`·`/try/flow`·`/flows/:slug`에서 operator·member refresh를 시도하지 않습니다(`auth.ts` `isAnonymousRoute`). `/try/claim`은 로그인 뒤 착지라 refresh 쿠키로 access를 되살려야 해서 제외합니다. `GET /api/try/session/`은 세션이 없거나 만료·위조면 **200 `null`**(#163, 「아직 없음」을 묻는 질의라 오류가 아님 — 생성 타입 `TrySessionOut | null`)이고, 다른 `/api/try/*`는 같은 상황에 401 `try_no_session`을 그대로 냅니다(e2e `watch4xx`에 예외 없음). dev·e2e에서는 Vite 프록시(`changeOrigin`)가 Host를 바꿔 쓰기 요청이 `try_bad_origin`이 되므로 compose에 `TRY_ALLOWED_ORIGINS`를 둡니다(운영은 프록시가 Host를 넘기면 불필요). 시험: `admin/src/pages/Try*.test.tsx`, 실제 스택 `e2e/tests/try-flow.spec.js`, 390폭은 `narrow-routes.json`의 `public`.

**이 흐름으로 가입(#42, ADR-0030 보충)**: 세션이 아직 없으면(편집 전) 링크가 중간 페이지 `/admin-ui/try/signup?template=…`(`TrySignupPage`, #198)를 가리킨다 — 일반 화면은 `rel="nofollow"`, 임베드는 `target=_blank rel="noopener nofollow"`. 이 페이지는 `GET session/`으로 확인해 세션이 없을 때만 `POST sessions/` 1회(같은 탭에 보관된 지역 그래프가 있으면 `graph`로 — 새 탭은 iframe과 sessionStorage가 달라 없으면 템플릿 그대로) 뒤 `location.replace`로 아래 로그인에 보낸다. 거절이면 사유와 「체험으로 돌아가기」. 색인 제외는 SPA가 이 페이지에서만 `<meta name="robots" content="noindex,nofollow">`를 넣고 떠나면 걷는다(nginx 불변). 세션이 있으면 링크는 곧바로 로그인 주소. 아래는 그 로그인 이후: `/try/flow/:id` 머리줄 「이 흐름으로 가입」 → `GET /api/auth/authorize/member?then=try_claim`(고객 로그인, 옮기기 표시는 서명된 시도 쿠키에) → 콜백이 테넌트 수와 무관하게 표(`member_ticket`)를 주고 `AUTH_TRY_CLAIM_REDIRECT`(기본 `/admin-ui/try/claim`)로. 옮기기 화면(`TryClaimPage`)은 체험 세션이 살아 있는지 먼저 보고, Admin 테넌트가 있으면 「새 테넌트 만들기 / 기존 테넌트에 추가」, 없으면 조직 이름만(기존 온보딩 `POST /api/auth/member/tenants` 그대로 — 이름·slug 규칙·IP 한도) 받거나 `POST /api/auth/member/select`로 세션을 연 뒤 `POST /api/try/claim/`(쿠키 `sf_try` + 그 세션 Bearer, `apps/tenants/try_claim.py`)을 부릅니다. 한 트랜잭션에서 세션 행을 잠그고 사본 초안 그래프를 그대로(노드 id 유지) 새 흐름(초안, 배포·공개 없음, 이름이 겹치면 「(2)」)으로 만든 뒤 체험 테넌트를 정리와 같은 경로(`try_tenant.delete_try_tenant`)로 지우고 쿠키도 지웁니다. 거부: `try_login_required`(401)·`try_no_session`(401 — 없음·만료·이미 옮김, 동시 두 요청 중 늦은 쪽)·`try_claim_forbidden`(403, Member)·`try_bad_origin`(403). 지식 검색 노드·가짜 API URL은 그대로, 체험 도서관 문서는 옮기지 않습니다 — 편집기는 `?claimed=1`이면 「체험에서 옮긴 흐름이에요」 안내(지식 문서 올리기·가짜 API 바꾸기, 해당 노드가 있을 때만, `ClaimedNotice`)를 보입니다. 시험: `backend/tests/test_try_claim.py`, `admin/src/pages/TryClaimPage.test.tsx`, 실제 스택 `e2e/tests/try-claim.spec.js`(sandcorp-auth 왕복만 대역).

**공개 흐름 보기(#39)**: 테넌트 설정 「공개 URL·보안」의 **공개 흐름 보기**(`TenantConfig.public_flow_view`, 기본 꺼짐, 바꾸기는 Admin만 — 권한 비트 `flow.public_view`; 설정 저장의 Admin 전용 필드는 `apps/tenants/api.py`의 `CONFIG_FIELD_PERMISSIONS` 표(필드→권한) 한 자리에서 「값이 바뀔 때만」 검사해, 화면·MCP가 전체 본문을 보내도 Member가 같은 값을 그대로 실어 보내면 통과(#152))를 켜면 로그인 없이 `/admin-ui/flows/{slug}`에서 공개 흐름(`is_public`)의 **배포 버전**을 읽기 전용으로 봅니다. API는 `GET /api/public/flows/{slug}/`(인증 없음, `apps/flows/public_api.py`) — 응답은 `slug`·`brand_name`·`flow_name`·`version`·`template`·`graph`(노드 `id`·`kind`·`code`·`name`·`position{x,y}`·`config`, 선 `from`·`to`·`handle`)뿐이고 모두 공개 보기 화면이 그립니다(#301 대조, 키를 더하려면 화면이 읽는지 먼저 대조 — 시험이 키 집합을 못 박음). `config`는 `apps/flows/public_view.py`의 **노드 종류별 허용 목록**으로만 직렬화합니다(거부 목록 아님): 공개 필드는 `ai_answer` instructions·await_reply, `condition` branches(name·when), `handoff` reason, `knowledge_search` top_k, `ai_classify` instructions·labels·fields(name·description·type), `guardrail` rules·action·target·block_message, `http_tool` method와 가짜 API 예약 호스트 URL(쿼리 뺌)뿐이고, `{{secret.*}}`가 든 값은 값째 빠집니다. HTTP 헤더·바디·응답 저장 경로·그 밖의 URL, 금지어 목록, Provider·웹훅·허용 도메인·지식 문서, 테넌트·흐름 id, 초안은 담지 않습니다. 없는 slug·꺼짐·미배포는 같은 404(`no-store`), 200은 `Cache-Control: public, max-age=60`, IP당 분당 `PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE`(기본 60). 배포 그래프가 예제 그대로면 `template`이 와서 머리줄 「고쳐 보기」 → `/admin-ui/try?template=home` 같은 체험으로 잇습니다. 화면은 `PublicFlowPage`가 `publicViewBackend`(`flowEditorBackend.tsx`)를 끼운 같은 편집기 — 편집·시험 대화·판단 기록·버전 없음, 넓은 폭에서도 끌기·선 잇기 꺼짐, 폰(767px 이하)은 하단 「노드 설정」 시트, HTTP 도구 노드에서 테넌트 비밀·도메인 API를 부르지 않음(`tenantLookups: false`). 「Sandcorp」 시드(`seed_sandcorp`)가 공개 보기를 켜고 플랫폼 키 env로 Provider를 맞춥니다(`docs/deployment.md` 「서버 쪽 할 일」 13번, portfolio용 주소 표도 거기). 시험: `backend/tests/test_public_flow.py`(심은 비밀·헤더·내부 URL·도메인·Provider 값이 응답 문자열에 없음, 노드 종류 전수), `admin/src/pages/PublicFlowPage.test.tsx`, `e2e/tests/public-flow.spec.js`.

**설정 권한(#162, ADR-0025 갱신)**: Provider 키·base_url·종류·모델·임베딩 차원(`providers.manage`)과 웹훅 주소·종류(`webhook.manage`)는 Admin 전용입니다. `CONFIG_FIELD_PERMISSIONS`(필드 표)와 `CONFIG_KEY_FIELDS`(API 키 3종)가 「값이 바뀔 때만」 검사합니다 — 키는 가림 값 `********`을 되돌려 보낸 것과 저장된 키가 없을 때의 빈 값은 변경이 아니고, 저장된 키를 빈 값으로 지우면 변경입니다. 빠른 설정(`providers/quick-setup`)과 모델 목록 불러오기(`providers/models`, 가림 키로 저장된 키를 임의 주소에 보내는 길)도 같은 비트로 막습니다. 시스템 프롬프트·환영 문구·표시 이름·범위 문구는 Member도 고칩니다. 설정 화면은 Member에게 AI 모델 탭(`<fieldset disabled>`)과 상담 전환 탭의 웹훅 입력을 읽기 전용으로 두고 「관리자만 바꿀 수 있습니다」를 보입니다. 시험: `backend/tests/test_config_provider_permissions.py`, `admin/src/components/ConfigTab.test.tsx`.

**폰 모드(#38)**: 폭 767px 이하(Tailwind `md` 미만 — 대시보드 사이드바가 접히는 기존 분기점)에서는 편집기(`/try/flow/:id`와 로그인 뒤 `/tenant/flows/:id` 둘 다)가 「캔버스는 읽기만, 노드 설정과 시험 대화만」으로 바뀝니다. 판정은 `admin/src/lib/useIsNarrow.ts` 한 자리(matchMedia)이고 `FlowEditorPage`가 이 값으로 `FlowCanvas`를 읽기 전용(노드 끌기·선 잇기·삭제·드롭 끔, 팬·줌·노드 누르기만)으로 두고 노드 목록 팔레트·노드 삭제 버튼을 그리지 않습니다. 노드를 누르면 하단 시트에 「노드 설정」(값 고치기·저장은 됨), 하단 탭은 「노드 설정 / 시험 대화 / 판단 기록 / 버전(버전 패널이 있는 편집기만)」. 서버 권한은 그대로 — UI 규칙입니다. 노드를 눌러 시트가 열리면 `FlowCanvas`가 그 노드를 시트 위 남은 영역으로 옮깁니다(#150: `coverRef`=시트 요소, `lib/flowGraph.ts`의 `panIntoView`, 배율 유지·이미 보이면 가만히·넓은 폭엔 `coverRef`가 없어 영향 없음). 시험: `FlowEditorPage.phone.test.tsx`, 실제 스택 `e2e/tests/try-phone.spec.js`(390×844). 팬·콘솔 정리는 `e2e/tests/try-polish.spec.js`.

**HTTP 도구 편집기 폼(#123)**: `NodeConfigForm.tsx`가 메서드·주소·헤더·본문 틀·`save`(응답→상태 이름-경로 표)를 백엔드 node-schema에서 그리고, 갈래는 `next`·`error`(`flowGraph.ts` `outHandles`). 서버 검증 문구(`invalid_config`의 `필드: 이유`, `unknown_secret`)는 클라이언트가 규칙을 새로 짜지 않고 그대로 보입니다. 클라이언트 경고만 따로: 주소 호스트가 허용 도메인(`listAllowedDomains()`)에 없으면(서버 `AllowList`와 같은 규칙 — `lib/httpToolWarn.ts`) 설정 탭 링크와 함께, 쓰는 `{{secret.이름}}`이 `listSecrets()`에 없으면 경고합니다(창 포커스 때 다시 읽음). 「시험 대화도 이 API를 실제로 호출합니다」 안내가 폼 위에 항상 붙습니다. `admin/src/lib/__fixtures__/nodeSchema.json`은 백엔드 `NODE_CONFIG_MODELS`의 `model_json_schema()` 출력 사본이라 config 모델을 바꾸면 다시 뽑습니다(`docker compose -f docker-compose.test.yml run --rm --no-deps -v $PWD/backend:/app test python -c ...`). e2e: `e2e/tests/flow-http-tool-form.spec.js`.

**Tenant Secret·허용 도메인(#33, ADR-0029)**: HTTP 도구가 쓸 테넌트 비밀과 나갈 수 있는 호스트. 로직은 `apps/tenants/tenant_secrets.py`, API는 `apps/tenants/secrets_api.py`.
- **비밀**(`TenantSecret`, 이름 + Fernet 암호문 — `apps/tenants/crypto.py`): **쓰기 전용**. `GET /api/tenant/secrets/`는 이름·시각만, `PUT /api/tenant/secrets/{name}` `{value}`는 만들기·값 교체, `DELETE …/{name}`(없으면 404). 이름은 `[A-Za-z_][A-Za-z0-9_]{0,63}`, 값은 8~4096자 — 틀리면 422이고 본문에 값을 되돌려 보내지 않습니다. 흐름 JSON에는 `{{secret.이름}}` 참조만 두고, 치환은 요청을 만들 때 서버에서 `render_secrets(tenant_id, template)`로만 합니다. 이 테넌트에 없는 이름을 참조하면 흐름 검증 오류 `unknown_secret`(초안 검증·버전 저장·시험 대화, MCP `get_flow`의 `errors`도 같음).
- **가림**: 원문과 **원문의 6자 이상 부분 문자열**을 `[비밀]`로 바꿉니다. 범위·출처는 출력마다 다릅니다(#115·#118): 테넌트가 읽는 판단 기록(`trace.REDACTORS` → `redact(tenant_id, text)`, 저장 기록·시험 대화 SSE/MCP `test_turn` trace)은 **그 기록의 테넌트가 DB에 저장한 비밀 전체**(복호화)와 그 테넌트로 등록된 파생값(`register_revealed(tenant_id, value)` — Basic base64 등)으로만 가립니다 — 남의 비밀로도 가리면 임의 문자열을 넣어 어디가 가려지는지로 남의 비밀을 한 조각씩 알아내는 오라클이 됩니다. DB에서 읽는 까닭은 비밀을 저장한 api 워커와 대화를 돌리는 worker-chat이 다른 프로세스여서입니다. 테넌트별 캐시 30초(지나면 비밀 표의 개수·최근 수정 시각만 보고 바뀌었을 때만 다시 복호화, 같은 프로세스 저장·삭제는 즉시 무효화, 비밀 없는 테넌트는 빈 집합 캐시). 같은 테넌트 Member는 시험 대화로 자기 테넌트 비밀 조각을 맞춰 볼 수 있다는 점을 받아들였습니다(ADR-0029 「위협 모델」). 운영자만 보는 **모든 로그 기록**(LogRecord 팩토리 — 메시지·예외 본문·스택, `TenantsConfig.ready`가 설치)은 `redact_log`로 프로세스가 본 모든 테넌트 비밀(저장·치환·가림 캐시 적재 때 오름)을 가립니다 — 팩토리에서 DB는 부르지 않습니다. 목록 상한: 테넌트 파생값 256개, 로그 공용 1024개(가장 오래 안 쓰인 값부터 빠지고 쓸 때마다 다시 오름). 오류 메시지는 이름만 담습니다.
- **허용 도메인**(`TenantAllowedDomain`): `GET/POST /api/tenant/allowed-domains/` `{pattern}`, `DELETE …/{id}`. 저장 때 `safe_http.normalize_allow_pattern`으로 검사·정규화합니다 — 호스트명만(스킴·경로·포트·`user@`·IP 리터럴·한 단어 호스트 거부), 와일드카드는 맨 앞 `*.` 한 단계 하위만(`*.com` 거부). 중복은 409. 실행 때 정책은 `allow_list_for(tenant_id)`(`AllowList`).
- **권한**: 쓰기는 `secrets.manage`·`domains.manage`(Admin 전용), 목록 읽기는 멤버 누구나. 다른 테넌트 행은 404. TENANT_KEY 인증도 받습니다(Admin 등가). **설정 화면(#113)**: 설정 탭 「비밀·허용 도메인」(`admin/src/components/SecretsSection.tsx`, API 호출은 `admin/src/api.ts`의 `listSecrets`·`putSecret`·`deleteSecret`·`listAllowedDomains`·`addAllowedDomain`·`deleteAllowedDomain`) — 도메인 목록·추가(와일드카드 설명, 서버 422·409 문구 그대로)·삭제, 비밀 이름 목록·추가·「값 바꾸기」·삭제(확인 한 번). 값 입력란은 password형이고 보내는 즉시 상태에서 비워 화면·DOM에 남지 않습니다. 전역 「저장」 버튼은 이 탭에서 숨깁니다(항목마다 즉시 저장). Member는 목록만 보고 쓰기 컨트롤이 없으며, 서버가 403이면 「권한이 없습니다.」를 보입니다. 시험: `SecretsSection.test.tsx`, E2E `e2e/tests/secrets-domains.spec.js`(390폭 모든 요소 넘침 0 포함).

**테넌트 URL SSRF 막기(#108, ADR-0029 보충)**: 테넌트가 넣은 URL도 같은 계층으로 부릅니다 — 허용 목록 없는 「공용 호스트만」 정책(`PublicHosts`). 웹 문서 「웹 URL」 가져오기는 http·https(80·443), 20초·5MB·리다이렉트 5회, 웹훅은 https만(저장 때 형식 검사, 보낼 때마다 해석·IP 검사, 실패는 로그+재시도 그대로). LLM·임베딩·OCR Provider `base_url`은 저장(일반 설정 PATCH 포함, Provider type이 플랫폼 기본이어도 — #110)·모델 조회 때 https·해석 IP 공용을 보고(바뀐 값만, 저장된 값 그대로의 round-trip은 다시 풀지 않음), **부를 때도** LLM SDK·임베딩·모델 목록의 httpx 클라이언트(`guarded_http_client`)가 연결마다 다시 해석·검사합니다(리바인딩 대비, 체험 플랫폼 키 `api.openai.com`도 통과). 막히면 사람이 읽는 사유를 보여 주고(문서 failed 사유 「이 주소는 가져올 수 없습니다: 내부망·사설 주소입니다」, Provider 저장 400 「Provider 주소를 쓸 수 없습니다: …」, 웹훅 저장 400 「웹훅 주소를 쓸 수 없습니다: …」) 해석한 IP는 로그에만 남깁니다. dev·test만 `OUTBOUND_PRIVATE_ALLOWED=True`로 내부 호스트(fake-llm·ollama)를 허용하고, 운영(prod.py)은 False 고정입니다.

## 4. 기술 사양

| 영역 | 사양 |
|------|------|
| 백엔드 | Python 3.14, Django 6.1 + Django-Ninja 1.7 (전면 async), **uvicorn ASGI**, **taskiq**(chat 워커) + Celery 5(배치 워커) |
| LLM 오케스트레이션 | LangChain + LangGraph (PostgresSaver 체크포인트) |
| LLM Provider | **per-Tenant** — OpenAI / Claude(Anthropic 네이티브) / Custom(OpenAI-호환). 미설정 시 플랫폼 기본(OpenRouter). 챗·추출 공용, 키 암호화 저장 |
| Embedding Provider | **per-Tenant**(LLM과 독립) — OpenAI/Custom, OpenAI-호환 `/v1/embeddings`. dev 기본은 결정적 가짜(`fake_llm`, 1024차원), `--profile ollama`로 `bge-m3`(Ollama) |
| Knowledge Graph | **Postgres + pgvector**(차원별 HNSW, **per-Tenant 가변차원**) — 메타데이터와 임베딩 분리 |
| OCR | per-Tenant Vision LLM(GPT-4o·Claude·Gemini 등) — dev/test/prod 같은 경로. 이미지/스캔 PDF·깨진 추출 |
| 관계형 DB | PostgreSQL 16 (Django 모델 + LangGraph 체크포인트) |
| 메시징 | Redis 7 (SSE pub/sub + Celery 브로커 + 레이트리밋·세션 락) |
| 프론트 | React 18 + Vite. 그래프 시각화: `react-force-graph-2d` |
| 인증 | Operator: sandcorp-auth Resource Token(EdDSA, PyJWT — ADR-0028) + 옛 Operator JWT(python-jose, #21에서 걷어냄). 고객(멤버): 같은 sandcorp-auth 로그인 → Membership으로 Flow 테넌트 세션(JWT, python-jose — #40). Visitor는 공개 slug URL + opt-in HMAC 신원검증. TENANT_KEY는 HMAC API·break-glass Admin 초대용 |

> **GPU**: 필요 없다. dev·시험의 기본 임베딩은 `fake_llm`의 결정적 가짜라 GPU·모델 내려받기 없이 mac·리눅스 어디서나 뜬다. 실제 bge-m3가 필요하면 `docker compose -f docker-compose.dev.yml --profile ollama up -d`와 `OLLAMA_BASE_URL=http://ollama:11434`(CPU로 돈다). GPU 호스트에서 쓰려면 `ollama` 서비스에 `runtime: nvidia`·`NVIDIA_VISIBLE_DEVICES=all`을 직접 겹치고, bge-m3가 GPU flash-attention에서 NaN을 내는 이슈로 `OLLAMA_FLASH_ATTENTION=0`을 그대로 둔다.

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
| `OLLAMA_BASE_URL` | 임베딩 서버 URL(OpenAI 호환 `/v1/embeddings`). dev·시험 기본은 `fake_llm` 가짜, 실제는 ollama | dev compose `http://fake-llm:8090`, 코드 기본 `http://localhost:11434` |
| `OLLAMA_EMBED_MODEL` | dev 기본 임베딩 모델 | `bge-m3` |
| `PLATFORM_DEFAULT_PROVIDERS_ENABLED` | 플랫폼 기본 Provider 폴백(OpenRouter LLM + ollama 임베딩). **dev만 `true`**, prod(GPU 없음)는 `false` → Tenant가 LLM·Embedding Provider 설정 필수. `dev.py`/`prod.py`에서 명시(env 아님) | `True`(dev)/`False`(prod) |
| `PLATFORM_OPENAI_API_KEY` | 플랫폼 OpenAI 키(체험 한도, #41·ADR-0026). 자기 LLM Provider가 없는 prod 테넌트가 남은 체험 턴 동안 쓴다. 비면 그 테넌트의 챗은 방문자에게 「상담 준비 중」으로 멈춘다 | (운영 env에만) |
| `PLATFORM_LLM_MODEL` / `PLATFORM_EMBED_MODEL` | 체험 테넌트의 챗·추출 모델 / 임베딩(OpenAI `dimensions`로 `embed_dim`에 맞춤) | `gpt-4o-mini` / `text-embedding-3-small`(기본) |
| `TRIAL_TURNS` | 새 조직의 체험 턴(1턴 = 방문자 메시지 하나) | `200`(기본) |
| `TRY_TENANT_TTL_HOURS` | Try Session 체험 테넌트의 수명(지나면 `prune_try_tenants`가 삭제) | `24`(기본) |
| `TRY_SESSION_TURNS` / `TRY_SESSIONS_PER_IP_PER_DAY` / `TRY_DAILY_BUDGET_USD` | Try Session 한도: 세션당 턴 / IP당 하루 세션 / 전체 하루 비용(USD) | `20` / `3` / `5`(기본) |
| `TRY_QUOTA_TIMEZONE` / `TRY_ALLOWED_ORIGINS` | 한도의 「하루」 시간대 / `/api/try` 쓰기에 허용할 다른 Origin(쉼표, 같은 호스트는 항상 허용) | `Asia/Seoul` / 빈 값 |
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

# 2. 인프라 + 앱 기동 (dev 스택: db, redis, fake-llm(가짜 임베딩; 실제 bge-m3는 --profile ollama),
#    api(uvicorn), worker(Celery 배치), worker-chat(taskiq chat), widget, admin)
docker compose -f docker-compose.dev.yml up --build

# 3. 최초 1회: 마이그레이션
docker compose exec api python manage.py migrate
```

Operator 비밀번호 로그인은 없다(#21). 관리 화면 Operator 로그인은 sandcorp-auth 대표 계정으로 한다 — dev용 기밀 클라이언트를 등록하고 `.env`에 `AUTH_CLIENT_ID`·`AUTH_CLIENT_SECRET`·`AUTH_REDIRECT_URI`(admin dev 서버 주소의 `/api/auth/callback`)·`AUTH_OWNER_SUB`를 채운다. sandcorp-auth 없이 화면만 볼 때는 `docker compose exec api python manage.py seed_e2e_operator --token-file /tmp/op_token`으로 대표 Operator와 세션 토큰을 만들고, 그 값을 브라우저 sessionStorage `op_access`에 넣는다(e2e가 쓰는 방식). 고객(멤버) 화면도 같다 — `seed_e2e_member --token-file …`이 조직·Admin 멤버십과 세션 토큰을 만들고(sessionStorage `agent_access`), `--accept-invite-from <파일>`은 그 파일에 떨어진 초대 원문을 콜백처럼 수락하는 sandcorp-auth 대역이다(e2e `member-team.spec.js`). `--serve-tenants-from <디렉터리>`는 거기에 떨어진 `<id>.req`(조직 이름)마다 새 조직+Admin 멤버십을 만들고 `<id>.token`을 쓰는 대역이다 — e2e 스펙이 자기 조직을 받는 방법(`provisionTenant`, `e2e/tests/memberToken.js`). 조직 이름은 대소문자 무시 유일이라 `--repeat-each` 반복 판에서는 `provisionTenant`가 이름 뒤에 반복 번호(` r<n>`)를 붙여 돌려준다 — 같은 스펙이 나란히 돌 때 같은 ms 이름이 겹쳐 대역이 죽던 것을 막는다(#128).

접근 경로:

| 경로 | 용도 |
|------|------|
| `http://localhost:5174/admin-ui/` | Operator·Tenant 관리 화면 |
| `http://localhost:8000/api/docs` | Django-Ninja 자동 API 문서 (개발·시험만, 운영은 꺼짐 — `API_DOCS_ENABLED`) |
| `http://localhost:5173/chatbot/{slug}/` | Visitor 챗봇 위젯 (토큰 없이 slug로) |
| `http://localhost:7474` | Neo4j Browser (그래프 직접 조회, 개발용) |

## 프로덕션 배포

실서버(**oracle A1**, arm64·CPU 전용)는 **무중단 롤링**으로 배포한다 — main push를 Jenkins 잡
`sandcorp-flow`(Jenkinsfile)가 받아 mac(못 잡으면 home)에서 arm64 이미지 3개를 굽고 GHCR에 올린 뒤, 같은 잡이
oracle에서 `docker rollout`으로 교체한다(ADR-0036). 파이프라인은 공용 Jenkins 라이브러리 `sandcorpDeploy`(배포 전 검사·헬스 실패 시 직전 이미지로 자동 롤백)이고,
PR은 잡 `sandcorp-flow-pr`이 `scripts/ci/check.sh`(로컬 Docker 시험 전부)와 파괴적 migrate 검사로 보고 상태 `continuous-integration/jenkins/pr-merge`를 올린다 — 그 상태가 초록일 때 병합한다(jenkins-shared #9). NPM(Nginx Proxy Manager)이 공개 TLS를 종단한다. 운영 주소는 `https://flow.sandcorp.co.kr`,
배포 경로는 Jenkins 잡 `sandcorp-flow`의 워크스페이스(호스트 `/var/jenkins_home/workspace/sandcorp-flow`), compose 프로젝트는 `sandcorp-flow-prod`다. 이 스택은 공유 박스에서
도므로 서비스마다 메모리 상한을 둔다(합계 약 3.2G, 표는 deployment.md). 서버 쪽 설정(DNS·NPM·Jenkins
잡·운영 `.env`)은 deployment.md의 「서버 쪽 할 일」 체크리스트를 따른다.
PR 검사는 GitHub Actions `.github/workflows/pr-check.yml`(check.sh 전 단계·파괴적 migrate·vite build)과 Jenkins `sandcorp-flow-pr`이
함께 돈다 — 자세히는 docs/deployment.md 「PR 검사」.

전체 흐름·1회 설정·환경변수·롤백·트러블슈팅은 **[docs/deployment.md](docs/deployment.md)** 참조
(설계 근거: [ADR-0015](docs/adr/0015-oracle-zero-downtime-rolling-deploy.md)).

운영 오류는 자체 GlitchTip으로 보낸다(#308) — 백엔드(api·워커·events)는 `sentry-sdk`, admin·widget은 `@sentry/react`.
운영 `.env`의 `SENTRY_DSN_BACKEND`·`SENTRY_DSN_ADMIN`·`SENTRY_DSN_WIDGET`이 비면 꺼진다(dev·시험은 꺼짐). release는 배포 sha,
프런트 DSN은 빌드가 아니라 init 컨테이너가 쓰는 `runtime-config.js`에서 온다. 연동 확인은 `manage.py glitchtip_test`
(deployment.md 「GlitchTip 오류 수집」).

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
- 멤버(Membership)는 **Admin / Member** 역할을 가집니다(권한 비트 기반, ADR-0025). **Admin은 전 권한**, **Member는 팀원 관리·`TENANT_KEY` 회전·Slug 변경·흐름 편집/배포/승인(`flow.edit`·`flow.deploy`·`flow.approve`)·비밀/허용 도메인 관리(`secrets.manage`·`domains.manage`)만 제외한 전부**(문서·HITL·프롬프트/HITL 설정·Provider 키·조회, 흐름 읽기 등 일상 운영은 다 가능).
- **팀원 탭**: Admin이 **초대 링크**(역할 지정·7일·1회용, 이메일 발송 없음)를 만들고(만료 시각 표시·복사) 직접 전달합니다. 아직 쓰이지 않은 초대는 **대기 중인 초대**에서 취소하고, 팀원은 이름 → 이메일 → sub 끝 6자리로 보이며 승격/강등·**내보내기**(비활성화)를 합니다. **마지막 활성 Admin은 비활성화·강등 불가**(lockout 방지). Admin 전체가 사라지는 사고는 `TENANT_KEY`로 `POST /api/tenant/invites/ {"role":"admin"}` — Admin 초대 링크를 발급해 복구(break-glass). Member에겐 팀원 관리·키 재발급·Slug 변경 컨트롤이 숨겨집니다. **화면의 역할 판단(#120)**: 토큰 claims 의 `role`은 발급 시점 값이라 강등·승격 뒤 낡으므로 쓰지 않고, 앱 진입·새로고침 때 `GET /api/tenant/members/me`의 DB 역할을 한 번 읽어 공용 상태(`admin/src/roleStore.ts`)에 둔다. Admin 전용 화면은 `useIsAdmin()`/`useAgentRole()`(`admin/src/lib/useAgentRole.ts`)만 쓰고(`api.currentAgentRole()`이 같은 상태를 읽음), 읽기 전(null)에는 쓰기 컨트롤이 숨은 채로 시작한다. 테넌트 쓰기가 403이면 `mutator.ts`가 역할을 다시 읽어 화면을 맞춘다. 시험: `serverRole.test.tsx`, E2E `secrets-domains.spec.js`.

### Tenant 운영 (멤버)
1. 어드민 UI 로그인 → **설정 탭**에서 Tenant Slug, (선택) LLM·Embedding Provider 키, HITL 토글, 브랜드 텍스트, 신원검증 등을 설정.
2. **문서 탭**: PDF/TXT/이미지/**Excel** 업로드 또는 **웹 URL** 추가(레이블 지정 가능) → 그래프 자동 구축.
3. **🕸️ 지식그래프 탭**: 엔티티 검색(다국어 의미 검색) → 노드-엣지 그래프로 탐색, 노드 클릭으로 이웃 확장.
4. 그래프 신선도 확인 후 **재구축** 트리거. **HITL 탭**에서 에스컬레이션 수락·응대.

### MCP — claude.ai에서 흐름 다루기 (#74)
- **연결**: claude.ai → 설정 → 커넥터 → 사용자 지정 커넥터 추가 → URL `https://flow.sandcorp.co.kr/mcp`. 커넥터가 `/.well-known/oauth-protected-resource/mcp`를 읽고 sandcorp-auth로 로그인시킨다(일반 사용자 member 토큰, `aud`=`AUTH_MCP_RESOURCE`, 스코프 `flow.read`·`flow.write`). auth 쪽 등록은 [docs/deployment.md](./docs/deployment.md) 「서버 쪽 할 일」 11번.
- **스코프(#210)**: 연결할 때 4개를 받는다 — `flow.read`(읽기)·`flow.write`(흐름 편집·시험·저장·보관·복원, 설정 수정, 문서 URL 등록·재수집)·`flow.deploy`(배포·되돌리기·배포 요청 승인/거절·임베드 켬/끔·기본 흐름 바꾸기·허용 사이트)·`flow.admin`(팀 관리, 문서·기억 삭제·그래프 재빌드, 「배포에 승인 필요」·「공개 흐름 보기」 토글). `deploy_version`·`rollback_flow`는 `flow.deploy`가 필요하다(옛 `flow.write` 호환 기간은 #220에서 끝남 — 감사 로그 `legacy_scope`는 지난 기록용). 스코프가 모자란 연결은 403 `insufficient_scope` — 커넥터를 다시 연결하면 풀린다.
- **권한**: 스코프는 상한이고 실제 판정은 멤버십 권한 비트(HTTP API와 같은 `has_permission`, 없으면 `admin_required`) — 도구별 비트는 아래 표. 흐름 편집·배포·승인·팀 관리·감사 조회는 테넌트 Admin만, 표에서 권한이 「멤버」인 도구는 Member도 쓴다. 테넌트가 「배포에 승인 필요」를 켰으면 `deploy_version`은 배포 대신 요청을 만들고 `approval_required=true`·`request_id`를 돌려준다(다른 Admin이 승인 — **내가 낸 요청은 승인 불가**, 서비스 규칙이라 HTTP·MCP 공통). 대표 계정도 멤버십이 있는 테넌트만 보인다(Operator 기능 없음).
- **테넌트 고르기**: 테넌트 도구는 `tenant` 인자(slug **또는 테넌트 id(UUID)**)를 받고, 멤버십이 하나면 생략한다. slug는 공개 챗봇 URL용이라 없을 수 있어(null이 의도, #119) slug 없는 테넌트는 id로 고른다. 어느 쪽이든 내 활성 멤버십 안에서만 풀린다. `list_tenants`가 id·slug·이름·역할을 보여 준다.
- **도구 표**(43개: 읽기 19·쓰기 24). 「2단계」 ○ = 처음 부르면 실행하지 않고 미리보기+`confirm_token`을 돌려준다(아래 「2단계 확인」). 쓰기는 모두 감사 로그에 남고, 읽기는 남지 않는다. 데이터는 같은 일을 하는 HTTP 핸들러와 같은 함수·쿼리를 쓴다. 목록은 `limit`+`next_cursor`(`pagination.py`).

  | 도구 | 스코프 | 권한 | 2단계 | 요지 |
  |---|---|---|---|---|
  | `list_tenants` | read | 멤버 | | 내 테넌트 id·slug·이름·역할(`can_edit`은 Admin만 true) |
  | `get_flow_schema` | read | 멤버 | | 흐름 JSON 규칙 — 흐름을 고치기 전에 한 번 읽는다 |
  | `list_flows` / `get_flow` | read | 멤버 | | 흐름 목록 / 초안·검증 결과·버전 목록(`version`으로 버전 JSON). 행마다 임베드 칸 `is_default`(=`is_public`, 위젯 기본 흐름)·`embed_key`·`embed_enabled`·`embed_path`(`/chatbot/<slug>/<key>/`)·`embed_origins`(#250) |
  | `list_documents` | read | 멤버 | | 문서 이름·상태·오류·갱신 시각 + 지식그래프 신선도 |
  | `search_knowledge` | read | 멤버 | | 질의 → 엔티티·1홉 관계(챗봇 Local Search와 같은 `apps/rag/services.py`, `top_k` ≤ 20) |
  | `list_sessions` / `get_session` | read | 멤버 | | 상담 세션 목록(`since`/`until`/`status`/`flow_id`, 행에 `flow_id`·`flow_name`) / 메시지(≤200+커서, 메시지당 2000자)·사람 넘김 이력·속한 흐름(`flow`={id,name}|null)·고정 흐름 버전. 시험 세션(`is_test`)은 빠진다 |
  | `get_settings` | read | 멤버 | | 비비밀 설정 전부 + `providers{llm,embed,ocr}`(`provider_type`·`model`·`api_key_set`) + `webhook{webhook_set,webhook_type}` |
  | `list_members` | read | 멤버 | | id·이름·이메일·역할·`is_active` |
  | `list_invites` | read | `agents.manage` | | id·역할·`expires_at`·`created_at`만(토큰·링크 없음) |
  | `list_secrets` | read | 멤버 | | 비밀 **이름**·시각만(값·암호문 없음) |
  | `list_allowed_domains` | read | 멤버 | | 허용 도메인 패턴 |
  | `get_usage` | read | 멤버 | | 토큰·비용 합계, call_type별, 일별(`days` ≤ 365) |
  | `list_deploy_requests` | read | `flow.approve` | | 배포 요청(`status` 필터) |
  | `get_session_trace` | read | 멤버 | | 세션의 `decision_traces`(노드별 상세)·`retrievals`(검색 근거), 턴 `limit` ≤ 20 쪽 |
  | `list_visitors` / `get_visitor_memory` | read | 멤버 | | 방문자 식별값(시험 세션 제외) / 방문자 기억 항목 |
  | `list_audit_events` | read | `audit.read` | | 쓰기 감사 로그(최신 순, `next_cursor`) |
  | `create_flow` / `update_flow` / `save_version` / `test_turn` / `archive_flow` | write | `flow.edit` | | 흐름 만들기 / 연산 목록 원자 적용 / 검증 통과한 초안을 버전으로(`note` ≤ 200자) / 초안으로 한 턴 시험 / 보관(공개 흐름은 `flow_in_use`로 거부, #126) |
  | `restore_flow` | write | `flow.edit` | ○ | 보관한 흐름 복원. 미리보기 = 흐름 이름·보관 시각 |
  | `update_settings` | write | 멤버 | | 허용 12칸만(system_prompt·agent_display_name·brand_name·welcome_message·topic_scope_enabled·scope_description·scope_refusal_message·hitl_enabled·hitl_timezone·hitl_schedule·hitl_holidays·require_identity_verification), 그 밖은 `invalid_arguments`(`extra="forbid"`). 안 준 칸은 그대로. 응답 `{changed:{칸:{before,after}}}`. 형식 검증은 HTTP `PATCH /config/`와 같은 함수(`config_rules`·`business_hours.hours_error`) → `invalid_settings` |
  | `add_document_url` | write | 멤버 | ○ | URL 한 개를 지식 문서로 등록(가져오기·임베딩을 큐에). 미리보기 = 주소·「비용 발생」·`already_registered`(**주소를 가져오지 않는다**). 미리보기·실행 모두 URL 가드(스킴·IP 리터럴·userinfo·포트)를 먼저 걸어 막히면 `url_not_allowed` |
  | `refetch_document` | write | 멤버 | ○ | URL 문서 재수집(그래프 기여분 교체). 파일 문서면 `not_url_document` |
  | `deploy_version` | deploy (호환: write도) | `flow.deploy` | | 배포 — 승인 필요 테넌트면 요청 생성. 배포 번호는 실행기에 핸들러가 있는 노드만으로 된 버전만 |
  | `rollback_flow` | deploy (호환: write도) | `flow.deploy` | | 배포 중 버전 바로 앞 번호를 다시 배포(긴급 복구라 2단계 없음) |
  | `approve_deploy_request` / `reject_deploy_request` | deploy | `flow.approve` | ○ | 대기 요청 승인(그 버전 배포)·거절. 인자 `request_id`·`note?`(200자). **내가 낸 요청은 승인 불가**(`self_approval`, 미리보기부터; 거절=철회는 가능). `request_not_pending`·`request_not_found` |
  | `set_deploy_approval` | admin | `flow.approve` | ○ | 「배포에 승인 필요」 켜기/끄기. 미리보기 = 현재→새 값, 같으면 `no_change` |
  | `set_public_flow_view` | admin | `flow.public_view` | ○ | 공개 흐름 보기 켜기/끄기. 같은 미리보기 모양 |
  | `set_flow_embed` | deploy | `flow.deploy` | ○ | 흐름의 키 주소 임베드 켜기/끄기 + (주면) 그 흐름의 허용 사이트 `origins`(빼면 그대로, `[]`이면 비워 테넌트 목록을 따름). 켜기는 배포 버전이 있어야(`not_embeddable`), 허용 사이트는 REST와 같은 `normalize_embed_origins`(`invalid_embed_origins`)로 **미리보기 단계에서도** 검증해 틀리면 아무것도 안 바뀐다. 미리보기 = 현재→새 값 + `effect`(「이 주소에 붙인 코드가 어떻게 되는지」), 같으면 `no_change`. 저장 뒤 정책 캐시 무효화도 REST와 같은 서비스(#250) |
  | `set_default_flow` | deploy | `flow.deploy` | ○ | 기본 흐름(`/chatbot/<slug>/`)을 바꾼다 — REST `…/default/`와 같은 서비스(한 트랜잭션에서 옛 기본 흐름 해제·새 흐름 지정, 배포 버전 없으면 `not_embeddable`). 미리보기 = 현재→새 기본 흐름 + `effect`(기존 주소는 새 대화부터 새 흐름·열려 있던 대화는 안 끊김·옛 흐름 키 주소가 닫히는지) |
  | `set_embed_origins` | deploy | `flow.deploy` | ○ | 테넌트 허용 사이트(기본 흐름 주소와 자기 목록 없는 흐름의 키 주소가 쓴다) 통째로 바꾸기, `[]`이면 어디서나. 응답 `{embed_origins:{before,after}}`. 미리보기 = 현재→새 목록 + `effect`. `get_settings`가 `embed_origins`를 준다 |
  | `delete_document` | admin | 멤버 | ○ | 문서 삭제(청크·그래프 기여분 포함). 미리보기 = 이름·청크 수·「복구 불가」 |
  | `rebuild_graph` | admin | 멤버 | ○ | 지식그래프 커뮤니티 재빌드(큐). 미리보기 = 문서 수·신선도·「LLM 비용 발생」, 이미 재빌드 중이면 `note` |
  | `delete_visitor_memory` | admin | 멤버 | ○ | 방문자 기억 한 건 삭제(`visitor_id`·`memory_id`). 미리보기 = key·내용 앞 100자 |
  | `change_member_role` | admin | `agents.manage` | ○ | 팀원 역할 admin/member. 미리보기 = 현재→새 역할, 자기 강등이면 `warning`. 마지막 활성 Admin은 미리보기부터 `last_admin`(HTTP 409와 같은 규칙, 서비스 `apps/tenants/members.py` 공유) |
  | `deactivate_member` | admin | `agents.manage` | ○ | 팀원 비활성화 — **즉시 접근 끊김**(refresh 폐기·액세스 토큰은 `is_active` 검사·MCP는 `no_membership`). 자기 자신이면 `warning`, 마지막 Admin은 `last_admin` |
  | `cancel_invite` | admin | `agents.manage` | ○ | 초대 취소(`invite_id`). 미리보기 = 역할·만료·만든 시각. 토큰·링크는 응답 어디에도 없다 |

  **같은 스코프 안의 권한 차이**: 문서 삭제·그래프 재빌드·방문자 기억 삭제는 화면에서 Member가 하던 일이라 권한 비트를 두지 않았다(Member도 가능) — MCP에서는 2단계 확인만 더하고, 스코프는 `flow.admin`이다.

  **MCP에 없는 것(의도)**: Provider(종류·base_url·키·모델)·웹훅·비밀 값·허용 도메인·slug·TENANT_KEY 재발급·사람 넘김 실시간 답장·**초대 만들기**(링크가 대화 기록에 남지 않게 Admin 화면에서)·파일 업로드. 근거는 ADR-0033.

  **비밀 처리 원칙**: API 키 값·가림 문자열(`********`)·base_url·웹훅 주소·TENANT_KEY·초대 토큰/링크·비밀 값은 어떤 도구 응답에도 없다. `get_settings`는 허용 목록으로 만들어 새 설정 칸은 기본으로 빠진다. 회귀 시험(`tests/test_mcp_tenant_reads.py`)이 값을 심어 두고 새 도구와 기존 읽기 도구 응답 전체에서 찾는다.
- **흐름 고치기**: `update_flow`는 연산 목록(`upsert_node`·`delete_node`·`connect`·`disconnect`)을 한 번에 **원자적으로** 적용한다(하나라도 못 하면 아무것도 안 바뀜). 초안은 틀려도 저장되고 결과에 `errors[{node_id, code, message}]`가 온다 — 도구 오류가 아니다. 버전 저장은 검증 통과한 초안만(`note` 선택 메모 200자 이내 — 만들 때만 붙고 못 바꾸며 `get_flow` 버전 목록에 `note`로 나온다), 배포는 실행기에 핸들러가 있는 노드만으로 된 버전만(되돌리기는 이전 번호 배포). `test_turn`은 초안으로 한 턴을 돌려 답·지나간 노드를 보여 준다 — 시험 세션이라 운영 대화 목록에 안 섞이고, 사람 넘김은 실제로 넘기지 않으며, 체험 테넌트면 체험 턴 1을 쓴다. 편집 규칙은 `backend/apps/flows/services.py`(흐름 API #28·편집기 #29도 이 층을 쓴다).
- **도구별 권한·힌트·감사(#208)**: 도구는 등록 때 권한 비트(`permission`, `apps/tenants/permissions.py` — 흐름 쓰기 6개는 `flow.edit`, `deploy_version`·`rollback_flow`는 `flow.deploy`, 비트 없는 Member 쓰기는 `None`)를 적고 디스패처가 `has_permission` 한 곳에서 판정한다(없으면 `admin_required`). `tools/list`의 `annotations`에 `readOnlyHint`·`destructiveHint`(쓰기 기본 true — `create_flow`·`save_version`·`test_turn`·`update_settings`만 false)·`idempotentHint`(읽기와 `save_version`·`update_settings`만 true). **쓰기 도구의 `tools/call`은 모두 감사 로그(`McpAuditEvent`, `apps/mcp/audit.py`)에 한 줄**: 시각·테넌트·User·토큰 `sub`·`client_id`·`jti`·도구·인자(JSON 4000자 상한, 넘으면 `{_truncated, preview}`)·결과(`ok`/ToolError code/`invalid_arguments`/`insufficient_scope`/`internal`)·2단계 단계(`preview`/`confirm`). 부르기 **전에** `started`로 쓰고 끝나면 고친다 — 기록이 안 되면 도구를 실행하지 않는다(fail-closed, 결과 쓰기만 실패하면 행이 `started`로 남는다). 읽기 도구는 남기지 않는다. 조회는 Admin 전용 비트 `audit.read`로 `GET /api/tenant/audit-events/?limit&cursor`(최신 순, `next_cursor`)와 MCP `list_audit_events`(`flow.read`). 보존 `MCP_AUDIT_RETENTION_DAYS`(기본 180일), 정리 `python manage.py prune_mcp_audit`(하루 한 번 cron). ADR-0033.
- **2단계 확인 기반(#209)**: `register(..., writes=True, preview=fn)`으로 등록한 도구는 `confirm_token` 없이 부르면 실행하지 않고 `{confirm_required, preview, confirm_token, expires_in: 300}`을 돌려주고, 같은 인자에 토큰을 실어 부르면 실행한다(미리보기도 스코프·권한 검사 뒤). 토큰(`apps/mcp/confirm.py`)은 SECRET_KEY 서명이고 도구·테넌트 id·사용자 sub·정규화 인자 해시(`tenant`·`confirm_token` 제외, 기본값 채운 뒤)에 묶이며 5분 만료, 한 번만(jti를 `mcp_confirm_use` 표에 고유 키로 넣는다 — 다중 워커에서도 한 번). 거부 코드 `confirm_token_invalid`·`_expired`·`_wrong_user`·`_mismatch`(토큰을 쓰지 않음)·`_used`. 레지스트리가 `destructiveHint=true`·입력의 `confirm_token` 칸·설명 끝 공통 문구를 강제한다. 감사 로그에 단계가 남고 토큰 값은 `[redacted]`. 사람의 확인을 강제하지는 않는다(사람 관문은 클라이언트 승인 UI). 2단계 도구는 위 표의 ○.
- **전송**: Streamable HTTP의 JSON 응답 모드(POST 한 번에 JSON 한 답, 세션·SSE 없음, 배치 거부, 본문 1 MiB). 코드는 `backend/apps/mcp/`(도구 등록 방법은 `registry.py` 머리말).

### Visitor
- Tenant 사이트에 임베드된 위젯이 **`/chatbot/{slug}/`**(필요 시 `?visitor_id=`·`hash`)로 연결 → 지식그래프 RAG로 응답, HITL이 켜져 있으면 필요 시 사람 전환.

---

## 8. API 개요 (개발에서 `GET /api/docs`에 전체; 운영은 비공개)

**Operator 인증**(`/api/operator/*`): `Authorization: Bearer <토큰>`으로 두 가지를 받는다. ① sandcorp-auth 액세스 토큰(Resource Token, `aud`=`AUTH_RESOURCE`) — 규칙은 sandcorp-auth `docs/resource-server-token-validation.md` 3~6·10절(T1~T10 → A1~A3 → S1). 검증 코드는 패키지 `sandcorp-authkit`(sandcorp-auth `clients/python`, 태그 `authkit-v0.2.0`)에 있고, 이 저장소에는 settings를 패키지 설정으로 바꾸는 어댑터 `backend/apps/tenants/authkit.py` 한 파일만 있다(#282). **대표 토큰(`acct` 없음, `sub`=`AUTH_OWNER_SUB`) + `admin` 스코프**일 때만 열린다. 일반 사용자 토큰(`acct=member`)은 검증을 통과해도 403. 실패는 401(토큰 없음/`invalid_token`)·403(`insufficient_scope`)에 `WWW-Authenticate` 헤더. ② Flow 세션 Access Token(30분, ADR-0013) — 관리 화면이 sandcorp-auth 로그인 콜백 뒤 `/api/operator/auth/refresh`로 받는다.

**관리 화면 Operator 로그인**(#21, 패키지 `sandcorp_authkit.login` + 어댑터 `apps/tenants/authkit.py`): `GET /api/auth/authorize`가 state·PKCE(S256)·nonce를 서명된 httpOnly 쿠키(SameSite=Lax, 10분, `authkit.CookieAttemptStore`)에 맡기고 sandcorp-auth `/authorize`로 보낸다(`scope=openid profile email login admin`, `resource=AUTH_RESOURCE`). `GET /api/auth/callback`은 state → 코드 교환(`client_secret_basic` + `code_verifier`) → 액세스 토큰(`login` 스코프·`client_id`) → ID 토큰(EdDSA·`iss`·`aud`=client_id(문자열)·`azp`·`exp`·`iat`·`nonce`·같은 `sub`·같은 `acct`)을 확인하고, **대표(`acct` 없음, A1) + `admin`**이면 사용자 행 `User(iss, sub)`와 이어진 Operator에 Refresh Family를 열어 `/admin-ui/operator`로 돌려보낸다. 일반 사용자면 `?login_error=member`, 대표인데 `admin`이 없으면 `admin`, 그 밖에 `state|exchange|token|denied|config`. sandcorp-auth 토큰은 저장하지 않는다.

**고객(일반 사용자) 로그인**(#40, `apps/tenants/members.py`): `GET /api/auth/authorize/member`(`scope=openid profile email login` — admin 없음)로 시작하면 같은 콜백이 A1~A3을 통과한 `acct=member`를 받아 `User(iss, sub, acct="member")`를 갱신(이름·이메일 캐시)하고, `AUTH_MEMBER_REDIRECT`(기본 `/admin-ui/tenant`)로 돌려보낸다. 멤버십 1개 → 그 Membership의 Refresh Family(`member_refresh` 쿠키, `/api/tenant/members/auth`), 여럿 → `?select_tenant=1` + 10분 서명 표 `member_ticket`(→ `GET /api/auth/member/tenants`, `POST /api/auth/member/select`), 없음 → `?onboarding=1`(같은 표). `?then=try_claim`(#42)으로 시작하면 멤버십 수와 무관하게 표를 주고 `AUTH_TRY_CLAIM_REDIRECT`(기본 `/admin-ui/try/claim`)로 보낸다. 초대 링크(`/api/auth/authorize/member?invite=<토큰>`)로 시작한 로그인은 먼저 멤버십을 만들고, 실패하면 `?login_error=invite_expired|invite_used|invite_invalid`. 고객 진입에 대표 계정이면 `?login_error=owner`.

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
| `GET·POST /api/tenant/flows/` · `GET·PATCH …/{id}/` | 멤버(쓰기는 Admin) / TENANT_KEY | 흐름 목록·생성·상세(초안+검증 결과+버전 목록+대기 중 배포 요청)·이름 변경 (#28, `apps/flows/api.py` — 공용 서비스 층을 감쌀 뿐) |
| `PUT·PATCH /api/tenant/flows/{id}/draft/` | Admin / TENANT_KEY | 초안 통째 저장(틀려도 저장, 검증 결과 반환) · 노드/선 패치(MCP `update_flow`와 같은 연산 목록, 하나라도 안 되면 422·무변경) |
| `GET·POST /api/tenant/flows/{id}/versions/` · `GET …/versions/{n}/` | 멤버 / 저장은 Admin | 버전 목록·조회(`note` 포함)·저장(`{note?}`, 검증 실패 422 `errors`, 같은 해시면 200 `created=false`) |
| `DELETE /api/tenant/flows/{id}/` · `POST …/restore/` | Admin / TENANT_KEY (`flow.edit`) | **보관**(soft delete, `archived_at`, #126) → **204**. 목록·조회·MCP `list_flows`/`get_flow`·편집은 404가 되고, 버전·세션·판단 기록은 남아 `decision-traces` 조회는 계속 된다. 위젯이 쓰는 공개 흐름(`is_public`)은 **409 `flow_in_use`**(먼저 다른 흐름을 배포). 같은 이름을 새 흐름이 쓸 수 있다(이름 유일은 보관 안 한 흐름끼리). `restore`는 되돌린다(API만, 화면 없음 — 그 사이 같은 이름이 생겼으면 409 `duplicate_name`). 기다리던 배포 요청은 `superseded` |
| `POST /api/tenant/flows/{id}/deploy/` · `…/rollback/` | Admin / TENANT_KEY | 배포 `{version}` — 승인 필요 테넌트면 배포 대신 배포 요청을 만들어 **202**. 되돌리기는 승인 제외 |
| `GET /api/tenant/deploy-requests/?status=` · `POST …/{id}/approve\|reject/` | 멤버(목록) / Admin 승인·거절 | 배포 요청 목록·승인(그 버전 배포)·거절. 요청자 본인 승인은 403 `self_approval`, 이미 처리됐으면 409 |
| `POST /api/tenant/flows/{id}/test/` | Admin / TENANT_KEY | **시험 대화(SSE, POST)** — 초안으로 한 턴. 이벤트 `start`→(`node_start`→`node_end`\|`node_error`)×N(턴 도중 실시간, #239)→`trace`×N→`reply`→`done`{ran·handoff·note·code·errors·session_id}. 노드 예외면 `node_error` 뒤 `done`(code `turn_error`). 연결이 끊겨도 턴은 끝까지 돌고 판단 기록이 남는다. 노드 이벤트 가림은 턴 전에 만든 가림 함수로 루프에서 해, 턴이 sync 스레드를 쥔 지식 검색 중에도 `node_start`가 검색 전에 나간다(#256). 사람 넘김 알림 없음(`is_test` 세션), 체험 턴 1 사용. 판단 기록은 `session_id`로 `GET …/sessions/{id}/decision-traces` |
| `PATCH /api/tenant/slug/` | 멤버 세션 | Tenant Slug 설정 |
| `PATCH /api/tenant/config/` | 멤버 세션 | 설정(Provider·HITL·brand·신원검증·**`deploy_requires_approval`(Admin만, #28)** 등). 임베딩 Provider 변경 시 재임베딩 트리거 |
| `POST /api/tenant/documents/` | 멤버 세션 | 파일 업로드(PDF·TXT·이미지·Excel) |
| `POST /api/tenant/documents/url` | 멤버 세션 | 웹 URL(들) 인제스션 |
| `POST /api/tenant/documents/{id}/refetch` | 멤버 세션 | 웹 Document 재-fetch |
| `GET /api/tenant/documents/graph/search?q=` | 멤버 세션 | 엔티티 하이브리드 검색 → 서브그래프 |
| `GET /api/tenant/documents/graph/status` | 멤버 세션 | Graph Freshness |
| `POST /api/tenant/documents/graph/rebuild` | 멤버 세션 | 엔티티 해소(SAME_AS) 재구축 트리거 |

---

## 9. 테스트

- **단위/통합**: `docker-compose.test.yml`의 `test` 서비스(pytest). **실제 Postgres(pgvector)·실제 Redis**를 사용(결정적). 비결정적 외부 경계인 **LLM·Vision OCR·임베딩 호출만 결정적 Fake로 교체**합니다(`apps/agent/llm` 경계 + conftest Fake, OCR은 `vision_ocr` 픽스처가 `transcribe_image`를 대체, 임베딩은 아래) — 외부 모델 판단이 아니라 우리 코드 동작을 검증.
  - **인증 패키지 받기**: backend 이미지는 `sandcorp-authkit`을 공개 저장소 sandcorp-auth에서 git+https로 받는다(`requirements.txt`, #320) — ssh-agent·자격이 필요 없다. 시험·e2e·운영 이미지 빌드 모두 같다.
  - **실행**: `./scripts/test.ps1` (PowerShell) 또는 `bash scripts/test.sh` — 호스트 `backend/`를 마운트해 코드 변경을 즉시 반영하고, **종료 시 테스트 전용 인프라(db·redis·fake-llm)를 자동 `stop`** 합니다(다음 실행은 `start`로 빠르게 재개). 인자로 특정 테스트 경로를 넘길 수 있습니다(예: `bash scripts/test.sh tests/test_rag.py`).
  - **결정적 가짜 임베딩(#260)**: 시험의 플랫폼 기본 임베딩(`OLLAMA_BASE_URL`)은 `fake_llm`의 OpenAI 호환 `/v1/embeddings`를 가리킵니다 — 실제 HTTP·정규화·사용량 기록 경로는 그대로이고 벡터만 가짜입니다. 같은 글은 같은 1024차원 L2 정규화 벡터이고, 낱말·글자 2-gram을 해시해 더하므로 낱말이 겹치는 글끼리 더 가깝습니다(의미 임베딩은 아님). 질의에 `[[delay:N]]`을 넣으면 응답을 N초 늦춥니다(최대 10초 — 느린 임베딩 시험용). ollama는 기본으로 뜨지 않고 첫 실행의 모델 내려받기(약 1.2GB)도 없습니다. `fake_llm/app.py`를 고치면 `test.sh`가 이미지를 다시 빌드하고, e2e는 `docker compose -f docker-compose.test.yml build`가 필요합니다.
  - **실제 bge-m3 시험(수동)**: `real_embedding` 마커 시험(`backend/tests/test_real_embedding.py`, 2건)은 기본 실행에서 빠집니다. `TEST_REAL_EMBEDDING=1 bash scripts/test.sh`(PowerShell은 `$env:TEST_REAL_EMBEDDING='1'; ./scripts/test.ps1`)가 profile `real-embedding`의 ollama를 띄워 bge-m3를 받고 마커 시험만 돌립니다(CPU로 도는 한 번에 몇 분, 첫 실행은 내려받기 포함). 가짜가 실제 모델의 계약(차원 1024·정규화)과 어긋나지 않는지 맞춰 보는 용도입니다. **임베딩이 바뀔 때만 돌립니다** — bge-m3 교체, 가짜 임베딩 계약 변경, 임베딩 코드를 바꾸는 PR에서 그 작업자가 실행하고, 정기 실행이나 배포 전 수동 실행은 하지 않습니다.
  - 리눅스에선 시험 컨테이너가 호스트 uid:gid로 돌아, 마운트한 `backend/`에 root 소유 파일을 남기지 않습니다.
- **E2E**: Playwright. 챗 LLM은 OpenAI 호환 **Fake LLM 서비스**(`fake_llm/`)로 결정화합니다.
  - **부하 재현(#177)**: `bash scripts/e2e-soak/soak.sh [판 수] [출력 폴더]` — 전체 e2e 판을 여러 번 돌리며 판마다 api 요청별 응답 시간·동시 처리 수, **이벤트 루프 지연과 막힌 순간의 스택**, 컨테이너 CPU, DB 대기를 따로 남기고 표로 요약합니다(`scripts/e2e-soak/README.md`).
  - api 워커는 시작 때 URLconf 와 async 경로의 무거운 지연 import(langgraph·langchain·taskiq)를 미리 불러옵니다(`config/warmup.py`). 첫 요청·첫 시험 턴이 루프 스레드에서 모듈을 불러오며 같은 프로세스의 모든 요청을 1초씩 멈추던 것을 없앴습니다(#177). 함수 안 import 를 새로 더하고 이 목록에 빼먹으면 `backend/tests/test_warmup_guard.py` 가 빨강이 됩니다(#189).
- **시험 시간 한도(#257)**: 백엔드 시험 하나의 본문 CPU 1.0초(`TEST_CPU_LIMIT`, 0=끔), e2e 시험 하나 3초·재시도 0(스펙 안 `test.setTimeout` 금지, 워커 `E2E_WORKERS` 기본 4·api `E2E_API_WORKERS` 기본 2, e2e는 Vite dev 대신 빌드본 서빙 — #280). 재는 법·고치는 순서는 `CLAUDE.md`「시험 시간 한도」. 전체 백엔드 시험은 약 3분 40초에서 약 1분 15초로 줄었습니다(로컬 HTTP 시험 서버의 종료 대기 0.5초×수백 건 제거, warmup 서브프로세스 중복 제거).
- **고정 시간 대기 금지(#259)**: e2e·vitest는 `waitForTimeout`·`networkidle`·`setTimeout`/`sleep`으로 기다리지 않고 사건(응답·요소·화면 안정)을 기다립니다. e2e는 `e2e/tests/settle.js`(`settled`·`trackRequests`), admin vitest는 `admin/src/flushAsync.ts`를 씁니다. `bash scripts/lint-fixed-waits.sh`가 막고(pre-commit 훅이 부름, `git config core.hooksPath scripts/hooks`), 사건이 없어 남겨야 하는 줄은 같은 줄 끝 `// wait-ok: 이유`로만 풉니다.
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
