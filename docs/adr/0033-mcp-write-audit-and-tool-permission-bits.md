# ADR-0033: MCP 쓰기 감사 로그와 도구별 권한 비트

## Status
Proposed (2026-10-26, #208 — 우산 #207 T1. T9 #216에서 MCP 쓰기 범위·2단계 확인과 합쳐 확정한다)

## Context
MCP(claude.ai 커넥터)로 흐름 말고도 설정·지식·팀을 다루게 연다(#207). 그 전 조건이 두 가지다.

- 지금 MCP는 `writes=True`이면 Admin만 쓴다(`require_editor`). 앞으로 Member도 쓰는 도구(설정·문서)와 Admin 안에서도 비트가 다른 도구(배포·승인·팀)가 생긴다. 역할 하나로는 표현이 안 된다.
- 에이전트가 사람 대신 쓰기를 한다. 「누가 어느 커넥터로 무엇을 바꿨나」를 테넌트가 나중에 볼 수 있어야 한다. 사람이 화면에서 한 일과 달리 대화 기록은 우리에게 없다.

## Decision
1. **도구별 권한 비트.** `register(..., permission=…)`에 `apps/tenants/permissions.py`의 비트를 적는다(비트 없는 Member 도구는 `None`, 기본값 없음 — 등록하는 사람이 늘 적게). 판정은 HTTP API와 같은 `has_permission` 한 곳. 기존 흐름 쓰기 5개는 `flow.edit`, `deploy_version`은 `flow.deploy`. 거부 코드는 `admin_required`로 둔다 — Member에게 없는 비트는 모두 ADMIN_ONLY라 뜻이 맞고, #74부터 클라이언트·시험이 읽는 코드다. 스코프는 그대로 상한이다.
2. **힌트.** `annotations`에 `readOnlyHint`(= not writes)·`destructiveHint`·`idempotentHint`. 쓰기 도구의 기본은 `destructiveHint=true`, `idempotentHint=false`(보수적). 읽기 도구는 파괴적일 수 없다(등록 때 `ValueError`).
3. **감사 로그 모델 `McpAuditEvent`(`apps.mcp`, 표 `mcp_audit_event`).** 쓰기 도구의 `tools/call` 한 번 = 한 줄. 결과는 `ok` / ToolError code(권한 거부 `admin_required`, `tenant_not_found` 등) / `invalid_arguments` / `insufficient_scope` / `internal`. 테넌트를 풀기 전에 끝난 호출은 테넌트가 비어 테넌트 조회에는 안 보인다. 인자는 JSON 4000자 상한(넘으면 `{_truncated, preview}`). 2단계 단계 칸(`confirm_stage`)은 #209가 채운다.
4. **기록 실패 = 실행 안 함(fail-closed).** 도구를 부르기 **전에** `started`로 한 줄 쓰고, 끝나면 결과 코드·테넌트로 고친다. 앞 쓰기가 실패하면 도구를 부르지 않고 JSON-RPC internal error. 뒤 쓰기(결과)가 실패하면 도구 결과는 그대로 돌려주고 행은 `started`(결과 모름)로 남는다 — 이미 일어난 일을 되돌릴 수 없어서다.
5. **보존 180일**(`MCP_AUDIT_RETENTION_DAYS`). 정리는 기존 관례대로 management command `prune_mcp_audit`를 호스트 cron으로 하루 한 번.
6. **조회는 새 Admin 전용 비트 `audit.read`.** 테넌트 HTTP `GET /api/tenant/audit-events/?limit&cursor`(TENANT_KEY도 Admin 등가)와 MCP `list_audit_events`(`flow.read`, 같은 비트). 최신 순, id 키셋 커서(`next_cursor`). 화면은 후속.

## Alternatives
- **기록 실패해도 실행(fail-open)**: 가용성은 높지만 「쓰기 도구는 반드시 기록」이 깨진다. 감사 표 쓰기는 같은 DB의 작은 insert라, 그것이 실패하면 도구 자체(같은 DB를 쓰는)도 대개 실패한다. 막아서 잃는 것이 거의 없다.
- **결과만 한 번 쓰기(사후 기록)**: 왕복이 하나 줄지만 프로세스가 도구 중간에 죽으면 흔적이 없다. 사전 기록은 `started`로라도 남는다.
- **기존 비트 재사용(`agents.manage`)**: 「팀원 바꾸기」와 「활동 보기」를 나중에 나눌 수 없게 된다. 비트는 싸다(ADR-0025).
- **Event Store에 넣기**: Event Store는 영구 append-only 도메인 이벤트(세션·HITL)라 보존 기간 삭제와 맞지 않고, 테넌트 조회 모양도 다르다.
- **보존 1년**: 인자에 흐름 내용·시험 메시지가 들어간다. 최소 보관 쪽으로 180일. 늘릴 일이 생기면 env로 바꾼다.

## Consequences
- 새 MCP 도구는 등록 때 `permission`을 반드시 적고, `writes=True`면 자동으로 감사된다. 도구 쪽 코드는 감사에 관여하지 않는다.
- 감사 행 하나가 쓰기 호출마다 insert 1 + update 1을 더한다.
- `started`가 남은 행은 「결과 모름」이다 — 운영자가 로그(`MCP audit result write failed`)와 함께 본다.
- 테넌트가 지워지면 그 테넌트의 감사 행도 지워진다(CASCADE). User가 지워지면 `user`만 비고 `token_sub`는 남는다.
