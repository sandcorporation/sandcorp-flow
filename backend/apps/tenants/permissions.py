"""권한 비트 인가 (ADR-0025) — deep module.

인가는 역할이 아니라 Permission 비트로 검사한다. Role(Admin/Member)은 비트 묶음의 프리셋일 뿐이라,
추후 per-member 세분화를 도입해도 엔드포인트 가드 지점은 불변이다.

- Admin = 전체 권한
- Member = 전체 − ADMIN_ONLY(조직 단위 3종 + 흐름 편집·배포·승인·공개 보기 4종 + 비밀·허용 도메인 2종 + Provider·웹훅 2종)
- Tenant(=TENANT_KEY 인증 주체) = Admin 등가(break-glass·프로그램 프로비저닝 유지)
"""

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

# Permission 비트 — Admin 전용(조직 단위·되돌리기 어려움·라이브 단절)
AGENTS_MANAGE = "agents.manage"       # 팀원 초대/비활성화/역할 변경
TENANT_KEY_ROTATE = "tenant_key.rotate"  # TENANT_KEY 재발급(위젯/HMAC 연동 단절)
SLUG_CHANGE = "slug.change"           # 공개 챗봇 URL slug 변경(임베드 URL 단절)

# 흐름(#28) — 라이브 상담 응답을 바꾸는 일이라 Admin 전용. MCP의 「Admin만 쓰기」(#74 결정 4)와 같은 판정이다
# (``apps.mcp.access.can_edit``이 이 비트를 본다). Member는 읽기·판단 기록 조회만.
FLOW_EDIT = "flow.edit"          # 흐름 만들기·이름·초안·버전 저장·시험 대화(체험 턴을 쓴다)
FLOW_DEPLOY = "flow.deploy"      # 배포·되돌리기·배포 요청
FLOW_APPROVE = "flow.approve"    # 배포 요청 승인/거절 + 「배포에 승인 필요」 설정
FLOW_PUBLIC_VIEW = "flow.public_view"  # 「공개 흐름 보기」 켜기·끄기(#39) — 흐름을 익명에게 보이는 일이라 Admin 전용

# HTTP 도구(#33, ADR-0029) — 비밀은 외부 API 자격 증명이고 허용 도메인은 서버가 나가는 길을 넓힌다. 흐름 편집과 같은
# 무게라 Admin 전용. Member는 비밀 이름·도메인 목록 읽기만(값은 누구에게도 안 보인다).
SECRETS_MANAGE = "secrets.manage"    # 비밀 만들기·값 교체·지우기
DOMAINS_MANAGE = "domains.manage"    # 허용 도메인 더하기·지우기

# 설정(#162) — Provider 키·base_url·종류·모델은 비용·키 유출 경로, 웹훅 주소는 상담 내용이 나가는 길이라 Admin 전용.
# 시스템 프롬프트·상담 문구류는 Member도 고친다.
PROVIDERS_MANAGE = "providers.manage"  # LLM·임베딩·OCR Provider 종류·base_url·API 키·모델·빠른 설정·모델 목록 불러오기
WEBHOOK_MANAGE = "webhook.manage"      # 에스컬레이션 웹훅 주소·종류

ADMIN_ONLY = frozenset({
    AGENTS_MANAGE, TENANT_KEY_ROTATE, SLUG_CHANGE, FLOW_EDIT, FLOW_DEPLOY, FLOW_APPROVE, FLOW_PUBLIC_VIEW, SECRETS_MANAGE, DOMAINS_MANAGE,
    PROVIDERS_MANAGE, WEBHOOK_MANAGE,
})


def has_permission(subject, perm: str) -> bool:
    """subject가 perm 권한을 갖는지. subject는 Membership(역할로 판정) 또는 Tenant(키 인증=Admin 등가)."""
    from apps.tenants.models import Membership, Tenant

    if isinstance(subject, Tenant):
        return True
    if isinstance(subject, Membership):
        if subject.role == ROLE_ADMIN:
            return True
        return perm not in ADMIN_ONLY
    return False


def require_permission(subject, perm: str) -> None:
    """가드: 권한이 없으면 403. 엔드포인트 상단에서 호출한다."""
    if not has_permission(subject, perm):
        from ninja.errors import HttpError
        raise HttpError(403, "권한이 없습니다.")
