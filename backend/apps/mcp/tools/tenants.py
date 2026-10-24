"""`list_tenants` — 내 멤버십과 역할."""

from __future__ import annotations

from pydantic import BaseModel

from apps.mcp.access import caller_memberships, can_edit
from apps.mcp.registry import JSONObject, ToolContext, register
from apps.tenants.models import Membership


class NoArgs(BaseModel):
    pass


@register(
    "list_tenants",
    "내가 멤버인 활성 Flow 테넌트 목록. 테넌트마다 slug(다른 도구의 tenant 인자)·이름·역할(admin|member)·"
    "편집 가능 여부(can_edit, Admin만 true)를 돌려준다. 인자 없음. 멤버십이 없으면 빈 목록.",
    NoArgs,
    scope="flow.read",
)
async def list_tenants(ctx: ToolContext, args: NoArgs) -> JSONObject:
    memberships = await caller_memberships(ctx.token, ctx.issuer)
    return {"tenants": [_row(m) for m in memberships]}


def _row(membership: Membership) -> JSONObject:
    tenant = membership.tenant
    return {"slug": tenant.slug, "name": tenant.name, "role": membership.role, "can_edit": can_edit(membership)}
