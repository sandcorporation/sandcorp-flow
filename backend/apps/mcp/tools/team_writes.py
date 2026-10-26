"""팀 쓰기 도구(#215, 우산 #207) — 역할 변경·비활성화·초대 취소. 모두 Admin(`agents.manage`)·`flow.admin`·2단계.

규칙은 HTTP 핸들러와 같은 서비스(`apps.tenants.members`)를 쓴다 — 마지막 Admin 보호는 미리보기에서부터 거부한다.
**초대 만들기는 MCP에 없다**(초대 링크가 대화 기록에 남지 않게, Admin 화면에서). 응답 어디에도 초대 토큰·링크가 없다.
"""

from __future__ import annotations

import uuid
from typing import Literal

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.tenants import members as members_service
from apps.tenants.models import Membership, TenantInvite
from apps.tenants.permissions import AGENTS_MANAGE


class MemberArgs(TenantArgs):
    member_id: str = Field(description="팀원(멤버십) id(list_members로 확인)")


class RoleArgs(MemberArgs):
    role: Literal["admin", "member"] = Field(description="새 역할")


class InviteArgs(TenantArgs):
    invite_id: str = Field(description="초대 id(list_invites로 확인)")


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _member(ctx: ToolContext, member_id: str) -> Membership:
    """내 테넌트의 멤버십(남의 테넌트·없는 id·형식 오류는 같은 `member_not_found`)."""
    wanted = _as_uuid(member_id)
    member = (
        Membership.objects.select_related("user").filter(id=wanted, tenant_id=ctx.require_membership().tenant_id).first()
        if wanted
        else None
    )
    if member is None:
        raise ToolError("member_not_found", "이 팀에 그 멤버가 없습니다. list_members로 id를 확인하세요.")
    return member


def _rule(fn: object, *args: object) -> None:
    try:
        fn(*args)  # type: ignore[operator]  # check_* 서비스 함수(서명이 서로 달라 object로 받는다)
    except members_service.MemberRuleError as exc:
        raise ToolError(exc.code, exc.message) from exc


def _who(member: Membership, caller: Membership) -> JSONObject:
    return {
        "member_id": str(member.id), "name": member.user.name, "email": member.user.email,
        "role": member.role, "is_active": member.is_active, "is_me": member.id == caller.id,
    }


# ── 역할 변경 ─────────────────────────────────────────────────────────────────


async def _preview_role(ctx: ToolContext, args: RoleArgs) -> JSONObject:
    caller = ctx.require_membership()

    def load() -> JSONObject:
        member = _member(ctx, args.member_id)
        _rule(members_service.check_role_change, member, args.role)
        out: JSONObject = {
            "action": "팀원의 역할을 바꾼다", **_who(member, caller), "current_role": member.role, "new_role": args.role,
        }
        if member.role == args.role:
            out["no_change"] = True
        if member.id == caller.id and args.role == Membership.ROLE_MEMBER:
            out["warning"] = "내 자신을 Member로 강등합니다. 이후 이 연결에서 Admin 도구(팀·설정·배포 승인 등)를 쓸 수 없고, 되돌리려면 다른 Admin이 필요합니다."
        return out

    return await sync_to_async(load)()


@register(
    "change_member_role",
    "팀원의 역할을 admin 또는 member로 바꾼다(Admin만). 미리보기는 대상 이름·이메일·현재 역할→새 역할이고, 자기 자신을 강등하면 `warning`이 붙는다. "
    "마지막 활성 Admin은 강등할 수 없다(미리보기에서부터 거부). 인자: member_id, role. "
    "실패: member_not_found·last_admin·admin_required.",
    RoleArgs,
    scope="flow.admin",
    permission=AGENTS_MANAGE,
    writes=True,
    preview=_preview_role,
)
async def change_member_role(ctx: ToolContext, args: RoleArgs) -> JSONObject:
    caller = ctx.require_membership()

    def run() -> JSONObject:
        member = _member(ctx, args.member_id)
        try:
            members_service.change_member_role(member, args.role)
        except members_service.MemberRuleError as exc:
            raise ToolError(exc.code, exc.message) from exc
        return {"changed": True, **_who(member, caller)}

    return await sync_to_async(run)()


# ── 비활성화 ──────────────────────────────────────────────────────────────────


async def _preview_deactivate(ctx: ToolContext, args: MemberArgs) -> JSONObject:
    caller = ctx.require_membership()

    def load() -> JSONObject:
        member = _member(ctx, args.member_id)
        _rule(members_service.check_deactivate, member)
        out: JSONObject = {
            "action": "팀원을 비활성화한다", **_who(member, caller),
            "effect": "즉시 접근이 끊긴다 — 로그인 세션(새로고침 토큰 폐기, 액세스 토큰은 다음 요청부터 거부)과 MCP 연결 모두. 다시 켜려면 새 초대로 합류해야 한다.",
        }
        if not member.is_active:
            out["no_change"] = True
        if member.id == caller.id:
            out["warning"] = "내 자신을 비활성화합니다. 실행하면 이 연결도 곧바로 끊깁니다."
        return out

    return await sync_to_async(load)()


@register(
    "deactivate_member",
    "팀원을 비활성화한다(Admin만). 즉시 로그인 세션과 MCP 접근이 끊긴다. 미리보기는 대상과 그 효과(자기 자신이면 `warning`). "
    "마지막 활성 Admin은 비활성화할 수 없다(미리보기에서부터 거부). 인자: member_id. 실패: member_not_found·last_admin·admin_required.",
    MemberArgs,
    scope="flow.admin",
    permission=AGENTS_MANAGE,
    writes=True,
    preview=_preview_deactivate,
)
async def deactivate_member(ctx: ToolContext, args: MemberArgs) -> JSONObject:
    caller = ctx.require_membership()

    def run() -> JSONObject:
        member = _member(ctx, args.member_id)
        try:
            members_service.deactivate_member(member)
        except members_service.MemberRuleError as exc:
            raise ToolError(exc.code, exc.message) from exc
        return {"deactivated": True, **_who(member, caller)}

    return await sync_to_async(run)()


# ── 초대 취소 ─────────────────────────────────────────────────────────────────


def _invite(ctx: ToolContext, invite_id: str) -> TenantInvite:
    wanted = _as_uuid(invite_id)
    invite = (
        TenantInvite.objects.filter(id=wanted, tenant_id=ctx.require_membership().tenant_id, revoked=False).first()
        if wanted
        else None
    )
    if invite is None:
        raise ToolError("invite_not_found", "그 초대가 없거나 이미 취소됐습니다. list_invites로 id를 확인하세요.")
    return invite


def _invite_row(invite: TenantInvite) -> JSONObject:
    return {
        "invite_id": str(invite.id), "role": invite.role,
        "expires_at": invite.expires_at.isoformat(), "created_at": invite.created_at.isoformat(),
    }


async def _preview_cancel(ctx: ToolContext, args: InviteArgs) -> JSONObject:
    invite = await sync_to_async(_invite)(ctx, args.invite_id)
    return {"action": "팀원 초대를 취소한다(링크가 더는 쓸 수 없게 된다)", **_invite_row(invite)}


@register(
    "cancel_invite",
    "아직 취소되지 않은 팀원 초대를 취소한다(Admin만). 미리보기는 역할·만료·만든 시각만 — 초대 토큰·링크는 어떤 응답에도 없다. "
    "초대를 새로 만드는 도구는 없다(Admin 화면에서). 인자: invite_id. 실패: invite_not_found·admin_required.",
    InviteArgs,
    scope="flow.admin",
    permission=AGENTS_MANAGE,
    writes=True,
    preview=_preview_cancel,
)
async def cancel_invite(ctx: ToolContext, args: InviteArgs) -> JSONObject:
    def run() -> JSONObject:
        invite = _invite(ctx, args.invite_id)
        members_service.revoke_invite(invite)
        return {"cancelled": True, **_invite_row(invite)}

    return await sync_to_async(run)()
