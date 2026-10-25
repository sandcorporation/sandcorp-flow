# ADR-0030: Try Session은 숨은 체험 테넌트에 예제 사본을 넣는다 — 지식은 공유 「체험 도서관」에서 좁혀 검색

## Status
Accepted (2026-10-25, 사용자 결정 — #36·#134)

## Context
`/try`의 「고쳐 보기」(CONTEXT 「Try Session」)는 로그인 없는 방문자에게 예제 흐름(Template Flow) 사본을 편집·시험 대화하게 한다.
편집기·시험 대화·판단 기록·테넌트 범위 검사·HTTP 도구 보안(ADR-0029)은 모두 **테넌트** 단위로 짜여 있다. 체험을 위해
테넌트 없는 별도 저장소(세션 행에 흐름 JSON)를 두면 이 모든 경로에 「테넌트가 아니면」 갈래가 생긴다. 또 공개 남용 표면이라
플랫폼 키 비용·외부 호출·사람 알림을 체험에서 막아야 하고, 사본마다 지식을 다시 임베딩하면 비용이 세션 수에 비례한다.

## Decision
- **Try Session마다 숨은 체험 테넌트**를 하나 만든다: `Tenant.kind = "try"`(일반은 `standard`), `try_example_id`, `try_expires_at`.
  예제 흐름 사본을 **초안**으로 넣는다(배포·공개 없음). 편집·시험 대화·판단 기록·테넌트 범위 검사는 기존 것을 그대로 쓴다.
- **24시간 뒤 테넌트째 지운다**: `prune_try_tenants`(주기 작업, `TRY_TENANT_TTL_HOURS` 기본 24, `try_expires_at <= now`).
  Tenant FK는 연쇄로, `tenant_id` 칸만 가진 표(대화 세션·방문자 메모리·토큰 사용량·문서)는 같은 트랜잭션에서 먼저 지운다.
  감사용 event_store는 지우지 않는다(체험은 사람 넘김 이벤트를 만들지 않는다).
- **지식은 공유 「체험 도서관」**(`kind = "try_library"`, 하나)에 예제 묶음을 **한 번** 적재하고(`seed_try_library`, #35
  `seed_example_knowledge` 재사용), 체험 테넌트의 검색(엔티티·이웃·원문 폴백)은 도서관 그래프를 **그 예제의 문서 id**로만 좁혀 연다
  (`GraphStore.for_search` → `try_tenant.knowledge_scope`). 예제 문서 id는 (테넌트, 예제, 이름)에서 정해지므로 표시 칸이 필요 없다.
- **제약은 한 곳**(`apps.tenants.try_tenant`)에서 정하고 각 자리는 묻기만 한다. 이유 코드는 하나(`try_restricted`):
  - LLM·자료 정리·임베딩: 테넌트 Provider를 **무시**하고 플랫폼 키·`PLATFORM_LLM_MODEL`(체험·도서관 모두). Trial Quota의 남은 턴과 무관.
    dev 폴백(`PLATFORM_DEFAULT_PROVIDERS_ENABLED`)이면 그 폴백(테넌트 Provider는 여전히 무시).
  - HTTP 도구: 가짜 API 예약 호스트만. 허용 목록·비밀이 있어도 그 밖의 URL은 연결 없이 `try_restricted` 실패 갈래.
  - 문서 업로드·URL 등록: 403 `try_restricted`. 배포·배포 요청: `try_restricted`(배포 경로가 모두 지나는 `_apply_deploy`·`submit_deploy`).
  - 실제 사람 넘김: Escalation·이벤트(웹훅·콘솔·방문자 알림)를 만들지 않는다(넘김 노드는 판단 기록에만 남는다).
- **숨김**: 체험·도서관 테넌트는 운영자 테넌트 목록, 멤버십 목록(로그인 뒤 선택·MCP `list_tenants`), 운영 사용량 집계에 나오지 않는다.
- 이름: 「체험 테넌트」(kind=try)는 가입 테넌트의 **Trial Quota**(#41, `trial_turns_remaining`)와 다른 것이다.

- **공개 API·쿠키·한도(#135)**: `/api/try`(인증 없음)는 편집기 API와 같은 모양의 얇은 어댑터다(`try_api`, 응답 조립은
  `flows.api.flow_detail_body`·`stream_test_turn` 공유). 「고쳐 보기」는 `create_try_tenant` + `TrySession`(쿠키 `sf_try`의
  SHA-256만 저장, 테넌트와 함께 연쇄 삭제). 쿠키는 HttpOnly·Secure·SameSite=Strict·Path `/api/try/`, 쓰기 요청은 Origin이 같은
  호스트이거나 `TRY_ALLOWED_ORIGINS`일 때만(CSRF).
  - 세션당 20턴: 체험 테넌트의 `trial_turns_remaining = TRY_SESSION_TURNS`로 두고 기존 `admit_turn`(조건부 UPDATE)을 탄다.
  - IP당 하루 3세션: `try_ip_days`(IP HMAC, 날짜) 한 문장 조건부 upsert(`… WHERE count < 한도 RETURNING`). IP는 `client_ip`
    (XFF 끝에서 `TRUSTED_PROXY_COUNT`번째)를 따른다.
  - 전체 하루 $5: `try_spend_days`(날짜 한 행, 정리에 지우지 않음). 시험 대화 전에 **누적 < 상한**이면 들이고, 턴 뒤 그 턴의
    판단 기록 비용(모르는 모델 호출은 `PLATFORM_LLM_MODEL` 단가)을 원자적으로 더한다. 그래서 상한은 **진행 중인 턴들만큼(보통 한 턴)
    넘을 수 있다** — 미리 잡아 두는 예약 방식은 턴 비용을 미리 몰라 기각. 넘으면 LLM·턴 없이 `try_daily_budget`(「내일 다시」).
  - 「하루」는 `TRY_QUOTA_TIMEZONE`(Asia/Seoul) 날짜.

## Consequences
- 체험 테넌트의 토큰 사용량 행은 정리 때 함께 지워지므로, 하루 비용 상한은 따로 센다(`try_spend_days`, #135).
- 세션당 턴은 dev 폴백(`PLATFORM_DEFAULT_PROVIDERS_ENABLED`)이 켜져 있으면 세지 않는다(운영은 꺼져 있다).
- 도서관 적재는 운영에서 플랫폼 키가 있어야 한다(없으면 `KnowledgeSeedError`, 다시 돌리면 이어 적재).
- 체험 테넌트 판별은 호출마다 `kind` 한 칸을 읽는다(인덱스 `kind, try_expires_at`).

## Alternatives
- **테넌트 없는 체험 저장소(세션 행에 흐름 JSON)**: 기각. 편집·시험·판단 기록·범위 검사에 갈래가 생긴다.
- **사본마다 지식 재임베딩**: 기각. 비용이 세션 수에 비례하고 첫 화면이 늦다.

## 보충: 「이 흐름으로 가입」 옮기기(#42)
대장(조율자) 결정, 사용자 사후 보고 예정.
- **넣는 자리**: 체험 사본은 대상 테넌트의 **새 흐름(초안, 배포·공개 없음)**으로 들어간다. 새 가입자는 기존 온보딩(`create_tenant_for`)으로 만든 테넌트의 기본 흐름 옆에, Admin인 테넌트가 있으면 골라서 같은 방식으로. 이름이 겹치면 「이름 (2)」.
- **소유권**: `POST /api/try/claim/`은 `sf_try` 쿠키(세션 행을 `SELECT … FOR UPDATE`) **와** 고객 멤버 세션 Bearer(`flow.edit` = Admin) 둘 다 요구한다. 옮길 테넌트는 Bearer의 테넌트다 — 새 테넌트·테넌트 선택은 기존 온보딩·선택 엔드포인트가 세션을 연다. 쿠키 경로(`/api/try/`) 아래라 SameSite=Strict 쿠키가 같은 사이트 fetch에 실린다.
- **로그인 왕복**: `/api/auth/authorize/member?then=try_claim` — 옮기기 표시는 서명된 시도 쿠키에 담기고, 콜백은 테넌트 수와 무관하게 표(`member_ticket`)를 주고 `AUTH_TRY_CLAIM_REDIRECT`(`/admin-ui/try/claim`)로 보낸다. sandcorp-auth 리다이렉트 URI는 그대로.
- **한 트랜잭션**: 세션 잠금 → 사본 초안 그래프 그대로(노드 id 유지) 새 흐름 → 체험 테넌트 삭제(`delete_try_tenant`, 정리 작업과 같은 경로). 같은 세션의 두 번째·동시 요청은 세션이 없어 `try_no_session`(401).
- **지식**: 지식 검색 노드는 그대로(config에 체험 전용 참조 없음 — 범위는 테넌트 종류로 정해진다). 체험 도서관 예제 문서는 옮기지 않는다 — 편집기 안내 「지식 문서를 올리세요」.
- **가짜 API**: URL은 그대로 둔다. 가짜 API 예약 호스트는 어느 테넌트에서나 가짜 응답으로 돌아(`http_allowed`) 배포해도 깨지지 않으므로 배포 검증 경고는 두지 않고, 편집기 「가짜 API」 안내에 「배포 전 실제 주소로 바꾸세요」를 더했다.
- 체험 턴·하루 비용 누적(`try_spend_days`)과 대상 테넌트의 Trial Quota는 건드리지 않는다.
