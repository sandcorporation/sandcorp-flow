# ADR-0025: Tenant Self-Signup + 권한 비트(Admin/Member) 인가 모델

## Status
Accepted — 단, 공개 셀프 가입(A)과 TenantAgent 자체 계정은 ADR-0028 「고객 토큰 검증」으로 대체됨(#40). 권한 비트(C)·마지막 Admin 보호와 TENANT_KEY break-glass(D)는 Membership 위에서 그대로 유효하다.

## Context
지금까지 Tenant 계정은 **Operator가 생성해 자격증명을 전달**했고, TenantAgent에는 **역할 구분이 전혀 없어**
(모든 agent가 동등) 누구나 팀원 생성·설정 변경·키 회전을 할 수 있었다. 이를 (1) Tenant가 직접 가입해 첫
관리자가 되는 **Self-Signup**과 (2) Admin/Member **역할 인가**로 바꾸려 한다. 두 가지 제약이 설계를 좁혔다:
- **이메일 인프라가 전무**하다(EMAIL_BACKEND·send_mail·SMTP 0, TenantAgent에 email 필드 없음).
- 어드민 로그인은 `tenant_name + username`인데 **`Tenant.name`에 unique 제약이 없어**, self-signup으로 같은
  이름 조직이 둘 생기면 그 이름의 누구도 로그인 못 한다(`MultipleObjectsReturned → 401`).

(작성 시점 기준 실 서비스 사용자가 없어 데이터 이행 리스크는 사실상 없다.)

## Decision
**A. Self-Signup(공개 가입).** `tenant_name + username + password`만 받아 Tenant + 그 조직의 **첫 Tenant
Admin**을 생성한다(Operator 개입 없음). 남용은 v1에서 즉시 가입을 허용하되 기존 Operator 정지/삭제로 대응한다.
Operator의 수동 프로비저닝 경로(create_tenant)는 **공존**한다.

**B. 로그인 식별자 = `tenant_name`(전역 unique).** 이메일을 도입하지 않는다. 대신 `Tenant.name`에 unique
제약을 걸어 `name + username` 로그인을 결정적으로 만든다(대소문자·공백 정규화로 "Acme"↔"acme" 충돌 처리).
공개 챗봇 URL용 **Slug는 가입에서 받지 않고** 어드민에서 따로 설정한다(name=로그인, slug=공개 URL 분리).

**C. 권한 비트 기반 인가.** 엔드포인트는 역할이 아니라 **Permission 비트**로 가드한다(예: `agents.manage`,
`tenant_key.rotate`, `slug.change`). **Role은 비트 묶음의 프리셋**: **Admin = 전체**, **Member = 전체 −
{agents.manage, tenant_key.rotate, slug.change}**. 즉 Member는 일상 운영(문서·HITL·프롬프트/HITL 설정·Provider
키·전 조회)을 다 하되, *되돌리기 어렵거나 라이브를 끊는* 조직 단위 3종만 막힌다.

> **추가(#28)**: 흐름 비트 `flow.edit`·`flow.deploy`·`flow.approve`를 Admin 전용으로 더했다(Member = 전체 − 6종). 상담 응답을 바꾸는 일이라 MCP의 「Admin만 편집·배포」(#74)와 같은 판정이어야 해서, MCP `can_edit`도 `flow.edit` 비트를 본다 — 판정은 `apps/tenants/permissions.py` 한 곳. Member에게 `flow.edit`를 주고 싶어지면 이 모듈의 `ADMIN_ONLY`만 바꾸면 HTTP와 MCP가 함께 바뀐다.

**D. Lockout 방지·복구.** 한 조직은 **항상 최소 1명의 활성 Admin**을 유지한다(마지막 Admin 비활성화·강등
차단). **TENANT_KEY 인증 = Admin 등가**로 두어, 활성 Admin이 0이 되는 사고에도 TENANT_KEY로 새 Admin을
만들어 복구하는 break-glass 채널을 유지한다.

## Considered Options
- **이메일 기반 로그인/가입(B 대안)**: 기각. 검증·비번 재설정에 메일 발송 인프라를 새로 구축해야 하고,
  "팀원은 username+임시비번 그대로" 요구와 결이 어긋난다(팀원도 email 필요해짐). 인프라가 생기면 비번
  재설정·검증을 그때 얹는다.
- **Slug + username 로그인(B 대안)**: 검토. slug가 이미 unique이나, slug 변경(공개 URL 단절)이 로그인
  식별자까지 흔들어 결합이 과하다. name과 slug의 역할을 분리해 두는 편이 깔끔.
- **역할 하드코딩(role 문자열로 if 분기)**: 기각. 비트 가드로 두면 나중에 per-agent 세분화를 도입해도
  엔드포인트 가드 지점이 불변이다(C의 동기).
- **Member에서 Provider 키 편집도 차단**: 보류. 키는 한 PATCH 엔드포인트에 prompt/scope/hitl과 섞여 있어
  필드 단위 authz가 필요하고, 과금키 교체는 "재입력=복구"라 slug/키회전 같은 *즉시 단절*과 성격이 다르다.
  필요해지면 필드 authz를 추가한다.

## Consequences
- `Tenant.name`이 **전역 unique**가 된다 — 표시명이 곧 로그인 식별자라 두 조직이 같은 이름을 못 쓴다(소규모
  단계에선 수용 가능, 스쿼팅 여지는 trade-off).
- 비번 재설정의 self-serve 경로가 아직 없다 — Admin lockout 복구는 **TENANT_KEY break-glass**, 팀원은 Admin이
  임시비번 재발급. 이메일 인프라 도입 시 자연스럽게 보강된다.
- 공개 가입이라 스팸/남용 가능 — v1은 Operator 정지/삭제 + 가벼운 IP 레이트리밋으로만 막는다(이메일 게이트 없음).
- 인가가 비트로 모이므로, 추후 Owner 계층·per-agent 커스텀 권한을 가드 변경 없이 얹을 수 있다.
