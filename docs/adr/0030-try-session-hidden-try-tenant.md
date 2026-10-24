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

## Consequences
- 공개 API·세션 쿠키·세션당 턴·IP·하루 비용 한도는 다음 층(#135)이 `create_try_tenant`·`prune_expired` 위에 얹는다.
  체험 테넌트의 토큰 사용량 행은 정리 때 함께 지워지므로, 하루 비용 상한은 따로 세야 한다.
- 도서관 적재는 운영에서 플랫폼 키가 있어야 한다(없으면 `KnowledgeSeedError`, 다시 돌리면 이어 적재).
- 체험 테넌트 판별은 호출마다 `kind` 한 칸을 읽는다(인덱스 `kind, try_expires_at`).

## Alternatives
- **테넌트 없는 체험 저장소(세션 행에 흐름 JSON)**: 기각. 편집·시험·판단 기록·범위 검사에 갈래가 생긴다.
- **사본마다 지식 재임베딩**: 기각. 비용이 세션 수에 비례하고 첫 화면이 늦다.
