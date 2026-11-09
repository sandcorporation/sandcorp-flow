# PRD — 공개 랜딩 페이지 (`/`)

Status: ready-for-agent

## Problem Statement

루트 URL `/`로 접속하면 아무것도 안 뜬다(nginx에 `/` 라우트 없음 — 404). 서비스 소개와 핵심
기능(지식그래프·실시간 스트리밍 챗봇)을 보여줄 공개 랜딩이 없고, 연락처도 노출돼 있지 않다.

## Solution

`/`에 공개 랜딩 페이지를 둔다. 멀티테넌트 GraphRAG 챗봇 플랫폼을 소개하고, **실제 제품 컴포넌트를
목업 데이터로 구동**해 두 가지를 라이브로 보여준다 — ① 사용자가 질문하면 AI가 **토큰 스트리밍으로
마크다운 답변**하는 챗봇(실제 `ChatWidget`), ② **지식그래프 시각화**(실제 `react-force-graph`).
연락처(이메일·전화)도 노출한다. 위젯은 경량을 유지해야 하므로 랜딩은 **admin 레포**에서 만들되 `/`에
별도 엔트리로 서빙하고, 데모는 백엔드 없이 정적으로 동작한다.

## User Stories

1. As a 방문자, I want `/`에서 서비스가 무엇인지 한눈에 보기, so that 이 플랫폼이 뭘 하는지 안다.
2. As a 방문자, I want 첫 화면(Hero)에서 실제 챗봇이 동작하는 걸 보기, so that 핵심 가치를 즉시 체감한다.
3. As a 방문자, I want 챗봇에 추천 질문을 클릭해 보기, so that 손쉽게 데모를 시작한다.
4. As a 방문자, I want 내가 자유롭게 질문을 입력해 보기, so that 직접 상호작용한다.
5. As a 방문자, I want AI 답변이 토큰 단위로 실시간 흘러나오기, so that 실시간 스트리밍 경험을 본다.
6. As a 방문자, I want AI 답변이 마크다운(굵게·리스트·코드)으로 서식되기, so that 실제 답변 품질을 본다.
7. As a 방문자, I want 지식그래프 시각화를 보고 노드를 클릭해 펼치기, so that GraphRAG의 작동을 직관한다.
8. As a 방문자, I want 서비스 특징(지식그래프 RAG·실시간 스트리밍·HITL·임베드 위젯)을 카드로 보기, so that 기능을 빠르게 파악한다.
9. As a 방문자, I want 연락처 이메일을 클릭해 메일을 보내기(mailto), so that 바로 문의한다.
10. As a 방문자, I want 연락처 전화번호를 클릭해 전화 걸기(tel), so that 모바일에서 바로 연락한다.
11. As a 운영자, I want Hero에서 로그인(`/admin-ui/`)으로 가는 CTA, so that 관리 화면에 진입한다.
12. As a 방문자, I want 모바일·데스크톱·다크모드에서 보기 좋기, so that 어떤 환경에서도 읽는다.
13. As a developer, I want 랜딩이 admin을 안 건드리고 위젯은 가볍게 유지되기, so that 기존 앱에 회귀가 없다.
14. As a developer, I want 데모가 백엔드 없이 정적으로 돌기, so that 데모 tenant·API 의존 없이 안정적이다.

## Implementation Decisions

- **위젯 TypeScript 전환(선행)**: 위젯 소스(`ChatWidget`·`Markdown`·`App`·`main`)를 `.jsx`→`.tsx`로 전환 + `tsconfig` 추가. 위젯이 JS였던 건 역사적 사정일 뿐(번들 크기와 무관). admin(TS) 랜딩이 타입 있는 컴포넌트를 공유하기 위한 토대.
- **`ChatWidget` 트랜스포트 주입**: 하드코딩된 `new EventSource`/`fetch`를 주입 가능한 경계로. 프로토타입이 확정한 인터페이스:
  ```ts
  interface ChatTransport {
    createEventSource(url: string): EventSourceLike  // addEventListener/close
    postMessage(sessionId: string, content: string): Promise<void>
  }
  ```
  기본 구현 = 현재 동작(실제 백엔드). `ChatWidget`의 이벤트 처리 로직은 그대로 두고 생성·전송 지점만 주입. 위젯 테스트도 전역 `EventSource` 덮어쓰기 대신 mock 트랜스포트 주입으로 정리.
- **크로스 패키지 재사용 = vite alias**: 모노레포 워크스페이스가 없으므로, admin `vite.config`에 `resolve.alias`(`@widget` → `../widget/src`)만 추가해 admin이 실제 `ChatWidget`을 import. 위젯 빌드/번들은 무변경(경량 유지). admin은 `react-markdown`·`remark-gfm`을 이미 보유.
- **mock 챗 트랜스포트(랜딩)**: `ChatTransport` 구현체. `createEventSource`가 즉시 `connected` 발화, `postMessage`가 질문에 맞는 스크립트 답변을 **`token` 델타로 setInterval 스트리밍 후 `done`**. 추천 질문 칩은 매칭 답변(마크다운), 자유 입력은 일반 답변으로 폴백. 우리가 만든 스트리밍+마크다운을 그대로 쇼케이스.
- **KG 데모 = `ForceGraph2D` + 목업**: `KnowledgeGraphTab`은 인증 admin API·router에 묶여 재사용 불가 → 랜딩은 `react-force-graph-2d`를 직접 쓰고 목업 `{nodes,edges}` 공급(admin의 `toGraphData` 매핑·노드리스트·디테일 패널 재현). 노드 클릭 시 목업 이웃을 머지해 확장 연출.
- **서빙 = `/`에 별도 엔트리, admin 무변경**: admin 레포의 멀티페이지 빌드에 landing 엔트리 추가. nginx에 `location /` 추가해 랜딩을 루트에서 정적 서빙. admin은 `/admin-ui/` 그대로. 공개 방문자는 admin 전체 SPA가 아니라 랜딩 번들만 받는다. (자산 base 처리는 구현에서 확정 — 단일 빌드 base `/admin-ui/` + nginx가 landing.html을 `/`에 서빙하는 방식 권장.)
- **레이아웃**: Hero(분할 — 좌 카피, 우 라이브 챗봇 데모, "운영자 로그인"→`/admin-ui/` CTA) → 소개/특징 카드 4개(지식그래프 RAG·실시간 토큰 스트리밍·HITL 상담 전환·임베드 위젯) → 지식그래프 데모 → Contact(Email `gksdjf1690@gmail.com` mailto · 전화 `<LANDING_TEL>` tel) → footer.
- **스타일/언어**: admin의 Tailwind + shadcn 테마 재사용(카드·버튼 등), 다크모드 지원, 한국어. 브랜드명 "Embed Chat".

## Testing Decisions

- 좋은 테스트는 외부 동작을 본다 — 렌더된 섹션·링크·데모 동작. 구현 세부가 아니라.
- 프론트 테스트는 Docker node 컨테이너 vitest(jsdom).
- **위젯 `ChatWidget`(TS)**: 주입 mock 트랜스포트로 ① 스트리밍 토큰 누적 렌더, ② assistant=마크다운·user=평문(기존 회귀 보존). 전역 EventSource 덮어쓰기 → 주입으로 전환.
- **mock 챗 트랜스포트**: `postMessage` 시 `connected`/`token` 델타/`done`이 순서대로 나오고 누적이 스크립트 답변과 일치.
- **랜딩 페이지**: 소개·특징 카드 렌더, Contact의 `mailto:`·`tel:` 링크 존재, 로그인 CTA가 `/admin-ui/` 가리킴, 챗봇 데모가 `ChatWidget`을 렌더(칩 클릭 → 스트리밍). `react-force-graph`는 canvas라 jsdom에서 실제 렌더는 제한적 — KG는 데이터/래퍼 전달 수준으로 검증(렌더 자체는 수동/시각 확인).
- prior art: 위젯 `ChatWidget.test.jsx`(MockEventSource), admin 컴포넌트 vitest, `Markdown.test`.

## Out of Scope

- 데모의 실제 백엔드 연결 — 전부 목업(데모 tenant·API 불필요).
- SEO·애널리틱스·마케팅 카피 고도화 — 구조와 기본 문안까지.
- admin URL 변경(루트 이전) — admin은 `/admin-ui/` 그대로.
- 위젯의 모든 기능 TS 강타입화 — 전환 + 트랜스포트 타입까지(점진 강화는 후속).

## Further Notes (구현 중 전환)

- **랜딩 위치: admin → widget 레포로 변경.** Docker 빌드 컨텍스트 격리(admin 빌드는 `./admin`만, widget 소스 접근 불가)로 admin이 `@widget` alias로 ChatWidget을 import할 수 없었다. 대신 **랜딩을 widget 레포의 별도 멀티페이지 엔트리**로 두어 ChatWidget을 로컬 import. "widget은 가벼워야"의 실제 이유는 충족됨 — 랜딩 전용 무거운 의존(`react-force-graph`)은 **landing 청크에만** 들어가고 위젯 챗봇 main 번들은 불변. 스타일은 admin Tailwind/shadcn 대신 plain CSS. nginx는 `location = /`로 widget의 landing.html을 서빙.



- 위젯 TS 전환은 랜딩의 선행 슬라이스(공유 컴포넌트 토대) — issues에서 의존으로 정렬.
- 챗봇·스트리밍·마크다운(PRD-chat-token-streaming / PRD-markdown-chat-bubble)을 그대로 재사용해 쇼케이스.
- 관련: ADR-0001(SSE), 위젯 `role` 모델, admin Tailwind/shadcn.
