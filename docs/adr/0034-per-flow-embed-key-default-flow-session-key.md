# ADR-0034: 흐름별 임베드 — 불변 embed_key, 기본 흐름, 세션 키 (tenant, visitor, flow), nginx auth_request로 동적 frame-ancestors

## Status
Accepted (2026-10-28, 사용자 결정 Q1~Q12 — 우산 #245). [ADR-0011](./0011-public-slug-url-replaces-embed-token.md)(공개 slug 주소)과
[ADR-0003](./0003-iframe-embed.md)(iframe 위젯)을 보완한다. 세션 버전 고정([ADR-0027](./0027-flow-json-interpreted-executor.md))은 그대로다.
E1(#246)이 주소·기본 흐름·세션·공개 API·위젯을 했다. 허용 사이트(`frame-ancestors`) 절은 E3에서 구현하며 그때 이 문서를 고친다.

## Context
테넌트는 위젯 주소 `/chatbot/<slug>/` 하나만 가졌고, 그 주소는 `is_public` 흐름 하나로 돌았다. 페이지마다 다른 상담 챗봇을 붙이고 싶다는
요청이 왔다. 이미 붙여 둔 코드(portfolio 홈 `/chatbot/sandcorp/`)는 그대로 동작해야 한다.

## Decision
- **주소.** 흐름마다 `Flow.embed_key`(8자 소문자·숫자, 테넌트 안 유일)를 만들 때 발급하고 바꾸지 않는다. 키 주소는
  `/chatbot/<slug>/<embed_key>/`다. 흐름 이름을 바꿔도 끊기지 않는다. nginx는 이미 `/chatbot/` 아래를 위젯 SPA로 넘기므로
  위젯이 경로의 두 번째 조각을 읽기만 하면 된다. 기존 흐름에는 마이그레이션(`flows.0007`)이 키를 발급했다.
- **기본 흐름.** `is_public`이 「기본 흐름」을 뜻한다. `/chatbot/<slug>/`는 기본 흐름이 답한다. 테넌트당 하나, 보관 불가(`FlowInUse`)는 그대로다.
  - **칸 이름은 `is_public` 그대로 둔다.** 바꾸면 `FlowOut`·MCP `list_flows`/`get_flow` 응답 키, admin 생성 코드·화면, 시드,
    e2e까지 약 70곳이 바뀌고, 바깥 MCP 클라이언트가 읽는 키가 깨진다. 뜻은 모델 주석·이 문서·CONTEXT.md로 고정한다.
- **열리는 조건.** 키 주소는 (`embed_enabled` 또는 기본 흐름) + 배포 버전 있음 + 보관 아님일 때만 열린다(`Flow.embeddable`).
  `embed_enabled`는 기본 꺼짐이다. 기본 흐름은 토글과 상관없이 늘 열려 있다(자기 키 주소로도).
- **같은 404.** `GET /api/chat/resolve`·`/stream`은 선택 인자 `flow=<embed_key>`를 받는다. 없는 키·임베드 꺼짐·보관·미배포·
  다른 테넌트의 키는 없는 slug와 **같은 404 몸통**이다(`resolve`는 `{"detail": "Not found"}`, `stream`은 빈 몸통). 키가 있는지 흘리지 않는다.
  기본 흐름으로 몰래 넘기지 않는다 — 다른 상담 봇이 답하게 되기 때문이다. `resolve` 200 응답에 `flow_name`을 더했다.
- **세션 키 (tenant, visitor, flow).** `ChatSession.flow`(null)를 더하고 위젯 세션 재사용을 (tenant, visitor, flow)로 바꿨다. 같은 방문자라도
  흐름마다 대화가 따로다. 기본 흐름 주소는 `flow` = 그때의 기본 흐름이다. 그래서 기본 흐름을 바꾸면 기존 주소의 **새 세션**부터 새 흐름이고,
  이미 열린 세션은 고정된 버전으로 계속 답한다. 기존 세션의 `flow`는 마이그레이션(`chat.0004`)이 `flow_version.flow`로 채웠다.
  고정 버전이 없는 세션은 첫 턴에 세션의 흐름(없으면 기본 흐름)의 배포 버전에 고정한다. 방문자 기억(Visitor Memory)은 지금처럼 테넌트 안 모든 흐름이 공유한다.
- **메시지 막기.** `ChatSession.opened_by_key`가 키 주소로 연 세션을 표시한다. 그 세션의 흐름이 닫히면(임베드 꺼짐·보관) `POST /api/chat/message`가
  **403 `{"detail": "Flow not open"}`**을 내고 메시지를 저장하지 않는다. 위젯은 「지금 열려 있지 않은 상담입니다.」를 보이고 입력을 막는다.
  - 404가 아니라 403인 까닭: 세션 id는 방문자 자신의 것이라 숨길 존재가 없고, 위젯이 「세션 없음(404)」과 구별해야 한다.
  - 기본 흐름 주소로 연 세션은 막지 않는다. 기본 흐름을 바꾼 뒤 옛 기본 흐름의 임베드가 꺼져 있어도 열린 대화가 끊기지 않게 하려는 것이다.
- **보관.** `embed_enabled`인 흐름을 보관하면 임베드도 끈다(`archive_flow`). 「붙여 둔 코드가 멈춥니다」 경고·확인은 화면·MCP 몫(E2·E5)이다.
- **허용 사이트(E3).** 테넌트 목록 `TenantConfig.embed_origins`와 흐름 목록 `Flow.embed_origins`(비어 있지 않으면 그 흐름 키 주소에서만 대신한다).
  nginx가 `/chatbot/`에서 `auth_request`로 앱의 내부 정책 엔드포인트에 물어 `Content-Security-Policy: frame-ancestors`를 붙인다. 목록이 비면 `*`.
  `frame-ancestors`는 붙이기 통제이지 접근 통제가 아니다.

## Consequences
- 위젯 경로는 한 조각(기본 흐름)과 두 조각(키) 둘이다. 한 조각 주소는 전과 같다.
- 목록의 흐름 칸·필터는 E4(#249, `ChatSession.flow` 기준 `flow_id` 필터, 큐는 하나), MCP 도구는 E5다. 켬/끔·기본 흐름 바꾸기 API와 화면은 E2(#247, 아래 「E2 덧붙임」)에서 생겼다.
  e2e의 시드 대역 `embed-flow`는 그대로 두되 새 시험은 API로 켠다.
- 테넌트 slug를 바꾸면 키 주소도 함께 끊긴다(기존과 같다). slug 별칭은 후속 후보다.

## E2 덧붙임 (#247) — 켬/끔·기본 흐름 바꾸기 API와 화면
- **API**(권한 `flow.deploy`, Admin. Member는 403): `POST /api/tenant/flows/{id}/embed/` 본문 `{enabled}`, `POST /api/tenant/flows/{id}/default/`.
  흐름 목록·상세 응답(`FlowOut`)에 `embed_key`·`embed_enabled`·`embed_path`(`/chatbot/<slug>/<key>/`, origin 없음, slug 없는 테넌트는 null)를 더했다.
  **`is_default` 별칭은 두지 않는다** — `is_public`이 이미 「기본 흐름」이고 MCP·생성 코드가 그 이름을 쓴다. 두 키를 나란히 두면 어느 쪽이 원본인지 흐려진다.
- **켜기 거부**: 배포 버전이 없으면(`Flow.embeddable`과 같은 조건) 409 `not_embeddable`이고 `detail`이 이유(「…배포한 버전이 없어…」)이며 화면이 그대로 보인다. 끄기는 늘 된다.
  기본 흐름은 끄더라도 자기 키 주소로 열려 있다(`embeddable`이 `is_public`을 본다).
- **기본 흐름 바꾸기**: 배포됨·보관 아님만 된다(아니면 같은 409). 한 트랜잭션에서 테넌트 행 → 흐름 행을 잠그고 옛 기본 흐름을 False로 바꾼 뒤 새 흐름을 True로 바꾼다
  (`flow_one_public_per_tenant` 때문에 순서가 이렇다). 중간에 깨지면 옛 기본 흐름도 그대로다. 이미 기본 흐름이면 아무것도 안 한다.
  옛 기본 흐름은 임베드를 켜 두지 않았다면 키 주소가 닫히고(새 세션부터), 기본 주소로 열려 있던 세션은 끊기지 않는다(막는 것은 `opened_by_key` 세션뿐). 옛 기본 흐름은 이제 보관할 수 있다.
- **화면**: 흐름 목록 행과 편집기 머리의 「임베드」 패널(`FlowEmbedPanel` — 켬/끔, iframe 코드 복사(Member도), 미리보기, 「기본 흐름으로」), 켠 흐름을 보관하려 하면 「붙여 둔 코드가 멈춥니다」 확인,
  설정 > 보안 「내 사이트에 붙이기」 카드(`EmbedGuideCard`)에 기본 흐름 코드 + 임베드를 켠 흐름 목록·코드 복사·흐름 목록 링크. 허용 사이트 칸은 E3(#248, 아래)가 패널·카드에 더했다.

## E3 덧붙임 (#248) — 허용 사이트 목록과 동적 CSP
- **칸**: `TenantConfig.embed_origins`·`Flow.embed_origins`(문자열 목록, DB 기본값 `[]` — 롤아웃 중 옛 api가 행을 넣어도 깨지지 않게). 편집은 `flow.deploy`: `PATCH /api/tenant/config/`의 `embed_origins`(값이 바뀔 때만 권한을 본다 — 화면 round-trip 통과), `PUT /api/tenant/flows/{id}/embed-origins/`(`FlowOut.embed_origins`로 돌려준다).
- **검증 한 함수**(`apps/flows/embed_policy.normalize_embed_origins`): `scheme://host[:port]`, 하위 도메인 와일드카드는 맨 앞 `*.` + 두 조각 이상, http는 `localhost`·`127.0.0.1`만, 포트 `*`는 그 로컬 http만, 경로·쿼리·조각·사용자 정보·`*` 단독·키워드 거부, 끝 빗금 하나는 지운다, 소문자·중복 제거(순서 유지), 20개까지. CSP 값에 그대로 들어가므로 공백(줄바꿈 포함)·세미콜론·쉼표·따옴표는 형식 검사 전에 거부한다(헤더 주입). 거절 문구는 항목을 담고 화면이 그대로 보인다.
- **정책**(`frame_ancestors(slug, key)`): 목록이 비면 `*`, 아니면 `'self'` + 목록. 키 주소는 흐름 목록이 비어 있지 않으면 그것만(테넌트 목록을 **대신**), 기본 주소는 테넌트 목록만. 없는 slug·키(보관한 흐름 포함)는 `'self'`. 키가 있지만 꺼진 흐름은 목록을 그대로 쓴다 — 위젯이 어차피 「열려 있지 않음」을 보이고, 키는 붙인 코드에 공개돼 있어 구별을 숨길 까닭이 없다.
- **캐시**: Redis 해시 `embed:fa:<tenant_id>`(필드 = 흐름 키, 기본 주소는 `""`) 60초. 테넌트·흐름 목록을 저장하면 해시를 지운다. Redis가 죽으면 DB로 답한다(경고 로그).
- **nginx**(`nginx.oracle.conf`·dev `nginx.conf` 같은 모양): `/chatbot/`이 `auth_request /_internal/embed-policy` → 앱 `GET /_internal/embed-policy`(Django 경로, `/api/` 밖)가 `X-Original-URI`(`$request_uri` — 쿼리·퍼센트 인코딩 그대로, `try_files`로 바뀌지 않는다)로 판정해 204 + `X-Frame-Ancestors`. `auth_request_set` → `map`(빈 값이면 `*`) → `add_header Content-Security-Policy "frame-ancestors …" always`.
  폴백 index는 `/embed/`로 내부 이동하지 않고 같은 위치의 named location `@chatbot_index`에서 낸다(이동하면 `/embed/`의 헤더가 붙는다). 정책 위치는 `internal`이고, nginx는 `/api/`·`/mcp`·`/.well-known`만 앱으로 넘기므로 바깥(NPM)에서 닿지 않는다(직접 부르면 404).
- **실패할 때 = fail-open `*`**: 하위 요청의 4xx·5xx·연결 1초·읽기 2초 초과·이름 해석 실패(api 재시작·롤아웃)는 `proxy_intercept_errors` + `error_page … = @embed_policy_open`(204, 값 없음)이 받아 `*`가 된다. `auth_request`는 2xx·401·403 밖을 500으로 만들고 401·403은 위젯을 막으므로 모두 삼킨다. 근거: (1) 운영 테넌트는 배포 직후 목록이 비어 있어 `*`가 곧 지금 동작이다. (2) 정책이 잠깐 열리는 피해(그 몇 초 동안 다른 사이트가 붙일 수 있음)는 붙이기 통제의 일시 해제일 뿐이고, 닫는 쪽(`'self'`·500)은 운영 위젯을 portfolio에서 깨뜨린다. 처음 설계(Q9 4절 「앱이 죽으면 nginx가 오류를 낸다」)를 이렇게 바꾼다.
  바깥 직접 호출의 `internal` 404도 같은 `error_page`로 오므로 `@embed_policy_open`은 `$request_uri`가 `/_internal/`이면 404를 낸다.
- **`/embed/`는 `*` 그대로**: slug가 없어 대화할 수 없는 페이지라 막아서 지킬 것이 없고, 운영 nginx를 이 티켓에서 덜 바꾼다.
- **한계**: 붙이기 통제이지 접근 통제가 아니다 — 주소를 직접 열거나 API를 부르는 것은 막지 않는다.
- **시험**: `backend/tests/test_embed_origins.py`(검증·정책·캐시·내부 엔드포인트·편집 API), `scripts/test-nginx-embed-policy.sh`(실제 nginx + 가짜 정책 서버 — 성공·500·404·403·시간 초과·앱 없음·바깥 호출·`nginx -t` 두 conf), `e2e/tests/flow-embed-origins.spec.js`(test compose의 `nginx-e2e`가 운영 conf를 그대로 올린다 — 헤더, 다른 출처 iframe 막힘, 화면 편집). e2e 스택의 다른 시험은 nginx를 거치지 않는다(admin·widget은 Vite).

## Alternatives
- **사람이 정하는 흐름 slug.** 읽기 쉽지만 바꾸면 끊기고 중복 규칙이 필요하다. 기각.
- **flow UUID를 주소에.** 길고 내부 id가 드러난다. 기각.
- **`is_public` → `is_default` 이름 변경.** 위 「기본 흐름」 절 근거로 기각.
- **닫힌 키를 기본 흐름으로 넘기기.** 다른 봇이 답하게 된다. 기각(Q4).
- **메시지 막기를 「흐름이 닫혔으면 무조건」으로.** 기본 흐름을 바꾼 순간 옛 기본 흐름으로 열린 대화가 끊긴다. `opened_by_key`로 키 주소 세션만 막는다.
