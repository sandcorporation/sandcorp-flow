# ADR-0028: 인증을 sandcorp-auth로 통합하고 Flow 사용자는 외부 sub로 식별

## Status
Accepted (2026-10-24, 사용자 결정). Operator 전환은 지금. 고객 토큰 계약은 확정(sandcorp-auth #35·PR #40), 고객 가입은 sandcorp-auth 소셜 로그인·약관·화면·탈퇴(#36~#39) 이후.

## Context
Embed Chat은 자체 계정을 가진다: Django `Operator`, `TenantAgent`(tenant_name + username + password, ADR-0025 셀프 가입),
자체 Access/Refresh 토큰(ADR-0013). Sandcorp의 사이트들(portfolio, 뉴스)은 이미 sandcorp-auth(`auth.sandcorp.co.kr`)의
리소스 서버이고, 규칙은 sandcorp-auth `docs/resource-server-token-validation.md`에 있다.

그런데 지금의 sandcorp-auth는 **계정이 대표 한 명뿐**이다. 검증 규칙 T11은 `sub`가 `AUTH_OWNER_SUB`(대표)와 다르면 거부한다.
그래서 고객 셀프 가입을 sandcorp-auth로 받으려면 sandcorp-auth를 **다중 사용자**로 넓혀야 한다. 이 작업은 sandcorp-auth
저장소에서 별도 트랙으로 하며, 고객 `sub`를 받는 계약(클레임·스코프)은 그 트랙이 정한다.

## Decision
- **장기 방향은 통합 인증**: Flow는 비밀번호를 갖지 않는다. 모든 사람(대표·고객)은 sandcorp-auth로 로그인하고, Flow는 토큰을 검증하는 리소스 서버다.
- **Flow 사용자는 외부 신원 `(iss, sub)`로 식별한다.** Flow의 사용자 행은 `iss`와 `sub`를 유일 키로 갖고, 이름·비밀번호를
  들고 있지 않다(표시 이름은 캐시일 뿐 신원이 아니다). username·tenant_name은 신원이 아니다.
- **테넌트 멤버 역할은 Flow 안에서 관리한다.** 「누가 어느 테넌트의 Admin/Member인가」는 Flow의 멤버십 행(사용자 × 테넌트 × 역할)이며
  권한 비트 가드(ADR-0025 C·D, 마지막 Admin 보호)는 그대로 쓴다. sandcorp-auth 스코프로 테넌트 역할을 표현하지 않는다.
- **Operator = 대표의 `admin` 스코프.** Flow 리소스(제안: `aud = https://flow.sandcorp.co.kr/api`, 스코프 `admin`·`login`)에 대해
  대표 토큰의 `admin` 스코프가 Operator 권한이다. 이 부분은 **지금 구현한다** — 단일 대표 규칙(T11)으로 그대로 동작한다.
  검증은 sandcorp-auth 문서 3~6절을 그대로 따른다(EdDSA만, JWKS 캐시 600초, `exp` 허용 30초, 실패 응답 헤더, 보호 리소스 메타데이터).
  리소스·스코프·기밀 클라이언트 등록은 대장 세션이 sandcorp-auth에 한다.
  **판정은 `acct`로 한다**: Operator는 `acct` 클레임이 없는 대표 토큰(아래 A1 통과)이면서 `admin` 스코프를 가진 토큰뿐이다. 일반 사용자 토큰은 스코프와 무관하게 Operator가 될 수 없다.
- **Admin UI 로그인 세션**: 관리 화면 로그인 콜백에서 토큰을 한 번 검증한 뒤, Flow 자기 세션(ADR-0013의 Access/Refresh 구조 재사용)을
  연다. sandcorp-auth 액세스 토큰을 브라우저에 오래 들고 있지 않는다.
- **걷어낼 대상**: Flow 자체 고객 계정(`TenantAgent`의 username/password), 공개 셀프 가입 엔드포인트, Operator 비밀번호 로그인.
  **새로 만들지 않는다.** 고객 가입·테넌트 생성·체험 한도 부여, `/try`의 「이 흐름으로 가입」은 sandcorp-auth 다중 사용자화가
  끝날 때까지 막혀 있다.

## Considered Options
- **혼합(고객은 Flow 자체 계정 유지)**: 기각. 계정이 두 곳에 생기고 나중에 다시 옮겨야 한다.
- **고객은 Flow 안의 소셜 로그인**: 기각. 통합 인증 방향과 어긋난다.
- **sandcorp-auth 스코프로 테넌트 역할 표현**: 기각. 테넌트·멤버는 Flow의 데이터이고, 스코프는 리소스 단위 권한이다.

## Consequences
- 고객 쪽 전환 전까지 Flow에는 대표(Operator)만 실제로 로그인한다. 테넌트 화면 개발·시험은 시험용 사용자·멤버십을 직접 만들어 한다.
- `TENANT_KEY`(위젯·HMAC용 서버 키)는 사람 로그인이 아니므로 이 결정의 범위 밖이다. ADR-0025 D의 break-glass(TENANT_KEY로 Admin 생성)는
  고객 전환 때 「외부 sub를 Admin 멤버로 붙이기」로 다시 정한다.
- 고객 `sub` 계약이 정해지면 이 ADR에 「고객 토큰 검증」 절을 더하고, 막혀 있던 티켓을 푼다.

## 고객 토큰 검증 (2026-10-24 계약 확정, sandcorp-auth #35·PR #40)
규칙 원문은 sandcorp-auth `docs/resource-server-token-validation.md` 10절, 참조 구현은 `backend/tests/reference_validator.py`의 `validate_account_access_token`.

- **검증 순서**: T1~T10(3절) → **T11 대신 A1~A3** → S1.
  - A1. `acct`가 없으면 대표 토큰. `sub`가 `AUTH_OWNER_SUB`와 정확히 같지 않으면 거부.
  - A2. `acct`가 있는데 정확히 문자열 `"member"`가 아니면 거부(`"owner"`, 대소문자 차이, 빈 문자열, 배열, `null`).
  - A3. `acct`가 `"member"`인데 `sub`가 `^m_[0-7][0-9A-HJKMNP-TV-Z]{25}$`(대소문자 구분)에 맞지 않으면 거부.
  - 위반은 401 `invalid_token`.
- **External Identity**: 일반 사용자는 `(iss, sub)`, `sub` = `m_` + ULID 26자이며 바뀌지 않는다. 대표도 같은 사용자 표에 `(iss, "farrar142")`로 있다. 사용자·테넌트·역할은 `sub`로 묶고 **이메일로 묶지 않는다**.
- **스코프**: 일반 사용자는 `login`(리소스 스코프)과 `openid`·`profile`·`email`만 받는다. `admin`·`editor`는 받을 수 없다(auth가 `invalid_scope`). Flow 관리 화면 로그인 콜백은 `login`을 요구하고, Operator 기능은 A1 통과 + `admin`일 때만 연다.
- **신원 정보**: Flow 클라이언트는 기밀 클라이언트로 `openid profile email`을 요청한다. ID 토큰·userinfo의 일반 사용자 필드는 `sub`, `acct`, `name`(있을 때), `email`·`email_verified`(이메일이 있을 때만 — Kakao는 없을 수 있다). `preferred_username`은 없다. 이메일·이름은 표시용 캐시이며, 이메일이 필요하면 Flow가 사용자에게 입력받는다.
- **클라이언트 등록**: Flow 기밀 클라이언트는 auth 쪽 「일반 사용자 허용」(`allows_members=True`)이 켜져 있어야 한다. 꺼져 있으면 일반 사용자는 `access_denied`로 돌아온다. 등록은 서버 쪽 일이다(`docs/deployment.md` 「서버 쪽 할 일」).
- **탈퇴**(#55, 규칙 원문 sandcorp-auth 규칙 문서 11절 W1~W5): `POST /api/auth/webhook`(인증 없음 — 서명이 인증, CSRF 검사 없음)이 sandcorp-auth의 `user.deleted`를 받는다. 구현은 `apps/tenants/withdrawal.py`이고 검증 함수는 문서 예시를 그대로 옮겼다.
  - **검증**: 받은 본문 **바이트 그대로** `X-Sandcorp-Signature: t=…,v1=…`를 본다(다시 직렬화하지 않는다). 형식 틀림 400(W1), `|지금 − t| > 300초` 400(W2), HMAC 불일치 401(W3, `hmac.compare_digest`). `AUTH_WEBHOOK_SECRET`이 비면 모든 요청을 503으로 거부한다(설정 누락이 열린 문이 되지 않게, auth는 재시도하므로 비밀을 넣으면 이어진다). 서명은 맞는데 본문이 이벤트 모양(`id`·`type` 문자열)이 아니면 400.
  - **한 번만 처리(W4)**: 처리한 이벤트 `id`를 `auth_webhook_events`에 기록하고, 같은 id는 아무것도 하지 않고 200. 7일(문서 하한 3일, auth 재시도 약 47시간) 지난 행은 웹훅을 받을 때마다 지운다 — 별도 주기 작업(beat)이 없어서 받는 자리가 정리 작업을 겸한다.
  - **처리(W5)**: `user.deleted`면 `members.forget_user(AUTH_ISSUER, sub)` — 일반 사용자 행(이름·이메일 캐시)·멤버십·테넌트 Refresh Family 전부가 지워지고(CASCADE) 상담 기록의 담당자는 비운다. 대표(`acct=owner`)는 지우지 않는다. 모르는 `type`은 기록만 하고 200. 정리는 행 하나 삭제(CASCADE)라 짧으므로 큐로 넘기지 않고 id 기록과 **같은 트랜잭션**에서 끝낸다 — 「기록은 됐는데 정리는 안 됨」이 없고 세션이 즉시 끊긴다. 나중에 무거운 정리(개인정보가 담긴 대화 기록 등)가 생기면 그 부분만 Celery로 넘긴다.
  - **다시 가입**: 같은 사람이 다시 가입하면 새 `sub`다. 옛 데이터를 이어 붙이지 않는다(옛 사용자 행은 이미 없다).
  - **마지막 Admin이 탈퇴한 Tenant**: 지우지도 정지하지도 않는다. Tenant·설정·문서·상담 기록과 위젯은 그대로 돌고, Admin 없는 조직이 된다. 복구는 break-glass(TENANT_KEY로 Admin 초대 링크 발급, 아래). 자동 삭제는 되돌릴 수 없어 하지 않으며, 계약 해지·삭제는 Operator가 정한다(정지는 기존 `suspend`). Operator 테넌트 목록이 활성 Admin 수(`active_admin_count`)를 보여 0이면 「주인 없음」 배지·필터를 띄우고, 목록에서 `POST /api/operator/tenants/{id}/admin-invite`로 Admin 초대 링크를 바로 낼 수 있다(#66).
- **고객 로그인(#40)**: 같은 콜백(`/api/auth/callback`)이 A1~A3을 통과한 member를 받아 `User(iss, sub, acct="member")`를 갱신(이름·이메일 캐시)한 뒤 **Membership**으로 테넌트 세션(ADR-0013, 주체 = Membership)을 연다 — 하나면 바로, 여럿이면 조직 선택(`?select_tenant=1` + 10분 서명 표 `member_ticket` → `/api/auth/member/select`), 없으면 「온보딩 필요」(`?onboarding=1`, 테넌트 만들기는 #41). TenantAgent의 username/password·테넌트 비밀번호 로그인·공개 셀프 가입은 없앴다.
- **진입별 요청 스코프**: 관리 화면(Operator) 진입 `GET /api/auth/authorize`는 `openid profile email login admin`, 고객 진입 `GET /api/auth/authorize/member`는 `openid profile email login`을 요청한다(member가 `admin`을 요청하면 auth가 `invalid_scope`). 진입은 로그인 시도 쿠키에 기록되고, 콜백은 Operator 진입의 member를 `login_error=member`로, 고객 진입의 대표를 `login_error=owner`로 거절한다 — Operator는 언제나 `acct` 없음 + `admin`뿐이다.
- **팀원 초대**: Flow는 비밀번호를 만들어 주지 않으므로 Admin이 초대 링크(256비트 토큰·해시 저장·7일·1회용·역할 지정, 이메일 발송 없음)를 만든다. 링크는 `/api/auth/authorize/member?invite=…`이고, 초대 토큰은 로그인 시도 쿠키에 실려 콜백에서 멤버십이 된다.
- **break-glass(ADR-0025 D 보충)**: TENANT_KEY는 Admin 등가로 `/api/tenant/invites/`에 **Admin 초대 링크**를 발급할 수 있다(옛 「TenantAgent 생성」 자리). 마지막 Admin이 떠난 조직은 이것으로 복구한다.

