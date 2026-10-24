"""MCP 호출자의 테넌트·역할 판정(#74 결정 1·4·5).

- `caller_memberships(token, issuer)` : 토큰 `(iss, sub)` → User → 활성 멤버십(멤버십·테넌트 모두 활성).
                                        대표(acct 없음)도 같다 — Operator 권한은 MCP에 없다.
- `resolve_membership(memberships, slug)` : `tenant` 인자 해석. 하나면 생략 가능, 없거나 남의 테넌트면 `ToolError`.
- `can_edit(membership)` / `require_editor(membership)` : Admin만 편집·배포.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from pydantic import BaseModel, Field

from apps.mcp.registry import ToolError
from apps.tenants.members import active_memberships
from apps.tenants.permissions import FLOW_EDIT, has_permission
from apps.tenants.models import Membership, User
from apps.tenants.resource_token import AccessToken


class TenantArgs(BaseModel):
    """테넌트를 다루는 도구 입력의 바탕. 디스패처가 `tenant`를 멤버십으로 푼다."""

    tenant: str | None = Field(
        default=None,
        description="테넌트 slug(list_tenants로 확인). 멤버십이 하나면 생략한다.",
    )


async def caller_memberships(token: AccessToken, issuer: str) -> list[Membership]:
    user = await User.objects.filter(iss=issuer, sub=token.sub).afirst()
    if user is None:  # MCP는 사용자를 만들지 않는다 — 처음 보는 sub면 멤버십이 없다
        return []
    return await sync_to_async(active_memberships)(user)


def resolve_membership(memberships: list[Membership], slug: str | None) -> Membership:
    if not memberships:
        raise ToolError("no_membership", "이 계정은 활성 멤버십이 있는 Flow 테넌트가 없습니다.")
    if slug is None:
        if len(memberships) == 1:
            return memberships[0]
        slugs = ", ".join(m.tenant.slug or "(slug 없음)" for m in memberships)
        raise ToolError("tenant_required", f"멤버십이 여럿입니다. tenant 인자로 하나를 고르세요: {slugs}")
    wanted = slug.strip().lower()
    for membership in memberships:
        if (membership.tenant.slug or "").lower() == wanted:
            return membership
    # 없는 테넌트와 남의 테넌트를 구분하지 않는다(존재 여부를 흘리지 않음).
    raise ToolError("tenant_not_found", f"'{slug}'는 내 멤버십 테넌트가 아닙니다. list_tenants로 확인하세요.")


def can_edit(membership: Membership) -> bool:
    return has_permission(membership, FLOW_EDIT)  # 판정은 권한 비트 한 곳(HTTP 흐름 API와 같은 규칙)


def require_editor(membership: Membership) -> None:
    if not can_edit(membership):
        raise ToolError("admin_required", "편집·배포는 테넌트 Admin만 할 수 있습니다. 이 테넌트에서 내 역할은 Member입니다.")
