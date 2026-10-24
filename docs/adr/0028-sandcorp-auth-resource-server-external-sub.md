# ADR-0028: 인증을 sandcorp-auth로 통합하고 Flow 사용자는 외부 sub로 식별

## Status
Accepted (2026-10-24, 사용자 결정). Operator 전환은 지금, 고객 쪽은 sandcorp-auth 다중 사용자화 이후.

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
