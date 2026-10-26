# ADR-0033: MCP 쓰기 감사 로그와 도구별 권한 비트

## Status
Proposed (2026-10-26, #208 — 우산 #207 T1. 7·8번은 #209(T2). T9 #216에서 MCP 쓰기 범위와 합쳐 확정한다)

## Context
MCP(claude.ai 커넥터)로 흐름 말고도 설정·지식·팀을 다루게 연다(#207). 그 전 조건이 두 가지다.

- 지금 MCP는 `writes=True`이면 Admin만 쓴다(`require_editor`). 앞으로 Member도 쓰는 도구(설정·문서)와 Admin 안에서도 비트가 다른 도구(배포·승인·팀)가 생긴다. 역할 하나로는 표현이 안 된다.
- 에이전트가 사람 대신 쓰기를 한다. 「누가 어느 커넥터로 무엇을 바꿨나」를 테넌트가 나중에 볼 수 있어야 한다. 사람이 화면에서 한 일과 달리 대화 기록은 우리에게 없다.

## Decision
1. **도구별 권한 비트.** `register(..., permission=…)`에 `apps/tenants/permissions.py`의 비트를 적는다(비트 없는 Member 도구는 `None`, 기본값 없음 — 등록하는 사람이 늘 적게). 판정은 HTTP API와 같은 `has_permission` 한 곳. 기존 흐름 쓰기 5개는 `flow.edit`, `deploy_version`은 `flow.deploy`. 거부 코드는 `admin_required`로 둔다 — Member에게 없는 비트는 모두 ADMIN_ONLY라 뜻이 맞고, #74부터 클라이언트·시험이 읽는 코드다. 스코프는 그대로 상한이다.
2. **힌트.** `annotations`에 `readOnlyHint`(= not writes)·`destructiveHint`·`idempotentHint`. 쓰기 도구의 기본은 `destructiveHint=true`, `idempotentHint=false`(보수적). 읽기 도구는 파괴적일 수 없다(등록 때 `ValueError`).
3. **감사 로그 모델 `McpAuditEvent`(`apps.mcp`, 표 `mcp_audit_event`).** 쓰기 도구의 `tools/call` 한 번 = 한 줄. 결과는 `ok` / ToolError code(권한 거부 `admin_required`, `tenant_not_found` 등) / `invalid_arguments` / `insufficient_scope` / `internal`. 테넌트를 풀기 전에 끝난 호출은 테넌트가 비어 테넌트 조회에는 안 보인다. 인자는 JSON 4000자 상한(넘으면 `{_truncated, preview}`). 2단계 도구는 단계 칸(`confirm_stage`)에 `preview`/`confirm`을 남긴다(7번).
4. **기록 실패 = 실행 안 함(fail-closed).** 도구를 부르기 **전에** `started`로 한 줄 쓰고, 끝나면 결과 코드·테넌트로 고친다. 앞 쓰기가 실패하면 도구를 부르지 않고 JSON-RPC internal error. 뒤 쓰기(결과)가 실패하면 도구 결과는 그대로 돌려주고 행은 `started`(결과 모름)로 남는다 — 이미 일어난 일을 되돌릴 수 없어서다.
5. **보존 180일**(`MCP_AUDIT_RETENTION_DAYS`). 정리는 기존 관례대로 management command `prune_mcp_audit`를 호스트 cron으로 하루 한 번.
6. **조회는 새 Admin 전용 비트 `audit.read`.** 테넌트 HTTP `GET /api/tenant/audit-events/?limit&cursor`(TENANT_KEY도 Admin 등가)와 MCP `list_audit_events`(`flow.read`, 같은 비트). 최신 순, id 키셋 커서(`next_cursor`). 화면은 후속.
7. **2단계 확인(#209).** `register(..., preview=fn)`으로 미리보기 함수를 같이 등록한 도구는 `confirm_token` 없이 부르면 실행하지 않고 `{confirm_required, preview, confirm_token, expires_in: 300}`을 돌려준다. 같은 인자에 토큰을 실으면 실행한다. 미리보기도 스코프·인자·권한 비트 검사를 모두 지난 뒤에만 부른다.
   - **토큰**: Django `signing`(SECRET_KEY + salt `apps.mcp.confirm.v1`, HMAC)으로 서명한 `{jti, 도구, 테넌트 id, 사용자 sub, 인자 해시, 만료}`. 발급 때 서버에 아무것도 쓰지 않는다.
   - **정규화**: 입력 모델로 검증한 값(기본값 채움·형 변환 뒤)에서 `tenant`·`confirm_token`을 빼고 키 정렬 JSON의 SHA-256. 테넌트는 표기(slug/id·대소문자)와 상관없이 풀린 테넌트 id로 묶는다.
   - **한 번만 = DB**: 실행 직전에 jti를 `McpConfirmUse`(표 `mcp_confirm_use`, jti 기본 키)에 넣는다. 고유 제약이 두 번째를 막으므로 워커·프로세스가 여럿이어도 한 번만이다. 실행이 실패해도 토큰은 쓴 것으로 남는다(새 미리보기를 받는다). 만료 지난 행은 `prune_mcp_audit`가 지운다.
   - **거부 코드**(검사 순): `confirm_token_invalid`(위조·잘림) → `confirm_token_expired` → `confirm_token_wrong_user` → `confirm_token_mismatch`(도구·테넌트·인자 다름 — 토큰을 쓰지 않는다) → `confirm_token_used`.
   - **감사**: 토큰을 실었으면 `confirm`, 아니면 `preview` 단계로 남긴다(검사에서 막혀도). 토큰 값은 `[redacted]`로 바꿔 남긴다.
8. **2단계 도구 규약은 레지스트리가 강제한다.** `writes=True`·테넌트 도구여야 하고, `destructiveHint=true`(`destructive=False`면 `ValueError`), 입력에 선택 칸 `confirm_token`을 더하며, 설명 끝에 「(2단계) 먼저 미리보기를 사용자에게 보이고 확인을 받은 뒤 confirm_token으로 다시 부른다.」를 붙인다.
9. **스코프 4개와 배포 호환 분기(#210).** `flow.deploy`(배포·되돌리기)·`flow.admin`이 열렸다. `deploy_version`·`rollback_flow`는 `scope="flow.deploy", legacy_scope="flow.write"`로 등록해, 옛 토큰(`flow.write`만)도 통과시키되 감사 행 `legacy_scope=true`와 응답 `notice`를 남긴다. 일반 장치 대신 도구 한 칸(`Tool.legacy_scope`)과 판정 함수 하나(`used_legacy_scope`)뿐이라 걷을 때 `#207` 표기를 따라 지우면 된다. `rollback_flow`는 서비스 `rollback`을 재사용하고 2단계가 없다(긴급 복구).

## Alternatives
- **기록 실패해도 실행(fail-open)**: 가용성은 높지만 「쓰기 도구는 반드시 기록」이 깨진다. 감사 표 쓰기는 같은 DB의 작은 insert라, 그것이 실패하면 도구 자체(같은 DB를 쓰는)도 대개 실패한다. 막아서 잃는 것이 거의 없다.
- **결과만 한 번 쓰기(사후 기록)**: 왕복이 하나 줄지만 프로세스가 도구 중간에 죽으면 흔적이 없다. 사전 기록은 `started`로라도 남는다.
- **기존 비트 재사용(`agents.manage`)**: 「팀원 바꾸기」와 「활동 보기」를 나중에 나눌 수 없게 된다. 비트는 싸다(ADR-0025).
- **Event Store에 넣기**: Event Store는 영구 append-only 도메인 이벤트(세션·HITL)라 보존 기간 삭제와 맞지 않고, 테넌트 조회 모양도 다르다.
- **2단계 토큰을 Redis에 두기(SET NX + TTL)**: 만료 정리가 공짜지만, 운영 Redis는 지금 작업 큐(taskiq)용이라 MCP 쓰기가 Redis 가용성에 새로 묶인다. DB는 이미 감사 로그로 쓰기마다 필수이고, 고유 키 insert 한 번이면 다중 워커에서도 한 번만이 보장된다. 정리는 기존 cron에 얹는다.
- **발급 때 서버에 토큰 저장(상태 있는 토큰)**: 미리보기마다 쓰기가 생기고 「발급했지만 안 쓴 토큰」이 쌓인다. 서명 토큰 + 쓸 때만 기록이면 쓰기는 확인 때 한 번이다.
- **보존 1년**: 인자에 흐름 내용·시험 메시지가 들어간다. 최소 보관 쪽으로 180일. 늘릴 일이 생기면 env로 바꾼다.

## Consequences
- 새 MCP 도구는 등록 때 `permission`을 반드시 적고, `writes=True`면 자동으로 감사된다. 도구 쪽 코드는 감사에 관여하지 않는다.
- 감사 행 하나가 쓰기 호출마다 insert 1 + update 1을 더한다.
- `started`가 남은 행은 「결과 모름」이다 — 운영자가 로그(`MCP audit result write failed`)와 함께 본다.
- 테넌트가 지워지면 그 테넌트의 감사 행도 지워진다(CASCADE). User가 지워지면 `user`만 비고 `token_sub`는 남는다.
- **2단계의 한계**: 이 장치는 모델이 미리보기를 사용자에게 보이도록 *유도*할 뿐, 사람이 실제로 봤는지·동의했는지 알 수 없다. 모델은 미리보기를 받은 즉시 토큰으로 다시 부를 수 있다. 사람 쪽 관문은 클라이언트(claude.ai)의 도구 승인 UI이고, 그래서 2단계 도구는 `destructiveHint=true`로 고정한다. 2단계가 막는 것은 「한 번의 호출로 바로 실행」과 「다른 인자·다른 사람·오래된 확인으로 실행」이다.
- SECRET_KEY를 바꾸면 발급된 2단계 토큰이 모두 `confirm_token_invalid`가 된다(5분 안의 미리보기만 다시 받으면 된다).
