"""권한 비트 인가 (ADR-0025) — deep module.

인가는 역할이 아니라 Permission 비트로 검사한다. Role(Admin/Member)은 비트 묶음의 프리셋일 뿐이라,
추후 per-member 세분화를 도입해도 엔드포인트 가드 지점은 불변이다.

- Admin = 전체 권한
- Member = 전체 − ADMIN_ONLY(조직 단위 3종 + 흐름 편집·배포·승인 3종)
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

ADMIN_ONLY = frozenset({AGENTS_MANAGE, TENANT_KEY_ROTATE, SLUG_CHANGE, FLOW_EDIT, FLOW_DEPLOY, FLOW_APPROVE})


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
