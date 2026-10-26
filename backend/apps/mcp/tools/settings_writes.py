"""테넌트 설정 쓰기 도구(#212, 우산 #207) — ``update_settings``와 두 토글.

- ``update_settings``: 권한 비트 없음(Member도 — HTTP ``PATCH /config/``가 이 칸들을 멤버에게 여는 것과 같다). 입력 모델이
  허용 필드만 선언하고(``extra="forbid"``) Provider·웹훅·slug·키는 받지 않는다. 검증은 HTTP와 같은 서비스 함수
  (``apps.tenants.config_rules``)를 쓴다. 응답은 바뀐 칸의 before/after.
- ``set_deploy_approval``·``set_public_flow_view``: Admin 전용 2단계(``flow.admin``). 값이 이미 같으면 미리보기에 ``no_change``.
"""

from __future__ import annotations

from typing import Literal

from asgiref.sync import sync_to_async
from pydantic import ConfigDict, Field

from apps.flows.schema import JSON
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.tenants.config_rules import hours_changes_error, topic_scope_error
from apps.tenants.models import Tenant, TenantConfig
from apps.tenants.permissions import FLOW_APPROVE, FLOW_PUBLIC_VIEW


class UpdateSettingsArgs(TenantArgs):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str | None = Field(default=None, description="시스템 프롬프트")
    agent_display_name: str | None = Field(default=None, description="상담원 표시 이름")
    brand_name: str | None = Field(default=None, description="브랜드 이름")
    welcome_message: str | None = Field(default=None, description="첫 인사 문구")
    topic_scope_enabled: bool | None = Field(default=None, description="주제 범위 제어 켜기(켜려면 scope_description 필요)")
    scope_description: str | None = Field(default=None, description="응대 범위 설명")
    scope_refusal_message: str | None = Field(default=None, description="범위 밖 질문에 대한 안내 문구")
    hitl_enabled: bool | None = Field(default=None, description="사람 상담원 넘김 켜기")
    hitl_timezone: str | None = Field(default=None, description="상담 시간 시간대(IANA, 예 Asia/Seoul)")
    hitl_schedule: dict[str, JSON] | None = Field(default=None, description="요일별 상담 시간(get_settings의 모양 그대로)")
    hitl_holidays: list[JSON] | None = Field(default=None, description="휴무일 목록(get_settings의 모양 그대로)")
    require_identity_verification: bool | None = Field(default=None, description="방문자 본인 확인 요구")


class ToggleArgs(TenantArgs):
    enabled: bool = Field(description="켜려면 true, 끄려면 false")


def _jsonable(value: object) -> JSON:
    return value if isinstance(value, (str, int, float, bool, dict, list)) or value is None else str(value)  # type: ignore[return-value]


def _apply(tenant: Tenant, changes: dict[str, object]) -> dict[str, JSON]:
    """``changes``(None 제외)를 검증·저장하고 바뀐 칸의 before/after를 돌려준다. 같은 값은 빠진다."""
    config = TenantConfig.objects.get(tenant=tenant)
    diff: dict[str, JSON] = {}
    for field, value in changes.items():
        before = getattr(config, field)
        if value != before:
            diff[field] = {"before": _jsonable(before), "after": _jsonable(value)}
    scope_enabled = changes.get("topic_scope_enabled")
    scope_description = changes.get("scope_description")
    error = topic_scope_error(
        config,
        scope_enabled if isinstance(scope_enabled, bool) else None,
        scope_description if isinstance(scope_description, str) else None,
    )
    if error:
        raise ToolError("invalid_settings", error)
    hours_problem = hours_changes_error(
        changes.get("hitl_timezone"), changes.get("hitl_schedule"), changes.get("hitl_holidays")
    )
    if hours_problem:
        raise ToolError("invalid_settings", hours_problem)
    if diff:
        for field in diff:
            setattr(config, field, changes[field])
        config.save(update_fields=list(diff))
    return diff


@register(
    "update_settings",
    "테넌트 설정 일부를 고친다(준 칸만 바뀜, 안 준 칸은 그대로). 문구 칸은 빈 문자열 \"\" 을 주면 비우고, 인자를 빼면 그대로 둔다. 칸: system_prompt·agent_display_name·brand_name·welcome_message·"
    "topic_scope_enabled·scope_description·scope_refusal_message·hitl_enabled·hitl_timezone·hitl_schedule·hitl_holidays·"
    "require_identity_verification. Provider·웹훅·slug·키·배포 승인·공개 보기는 이 도구로 못 고친다(invalid_arguments). "
    "주제 범위를 켜려면 범위 설명이 있어야 하고, 상담 시간 칸은 형식이 맞아야 한다(invalid_settings; 시간표는 {mon:{enabled,start:\"09:00\",end:\"18:00\"}}, 휴일은 \"YYYY-MM-DD\" 목록). 응답: {changed: {칸: {before, after}}}(같은 값은 빠짐).",
    UpdateSettingsArgs,
    scope="flow.write",
    permission=None,
    writes=True,
    destructive=False,
    idempotent=True,
)
async def update_settings(ctx: ToolContext, args: UpdateSettingsArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant
    changes = args.model_dump(exclude={"tenant"}, exclude_none=True)
    return {"changed": await sync_to_async(_apply)(tenant, changes)}


# ── 토글 두 개 ───────────────────────────────────────────────────────────────

type ToggleField = Literal["deploy_requires_approval", "public_flow_view"]


def _current(tenant: Tenant, field: ToggleField) -> bool:
    return bool(getattr(TenantConfig.objects.get(tenant=tenant), field))


def _effect(field: ToggleField, enabled: bool, tenant: Tenant) -> str:
    if field == "public_flow_view":
        if enabled:
            return f"익명이 /flows/{tenant.slug}에서 흐름을 볼 수 있게 됨"
        return f"익명이 /flows/{tenant.slug}에서 흐름을 더는 볼 수 없게 됨"
    if enabled:
        return "Admin 배포도 다른 Admin의 승인을 받아야 반영됨(배포 요청이 생김)"
    return "Admin 배포가 곧바로 반영됨(승인 단계 없음)"


async def _preview_toggle(ctx: ToolContext, args: ToggleArgs, field: ToggleField) -> JSONObject:
    tenant = ctx.require_membership().tenant
    current = await sync_to_async(_current)(tenant, field)
    preview: JSONObject = {
        "setting": field,
        "current": current,
        "new": args.enabled,
        "effect": _effect(field, args.enabled, tenant),
    }
    if current == args.enabled:
        preview["no_change"] = True
    return preview


async def _toggle(ctx: ToolContext, args: ToggleArgs, field: ToggleField) -> JSONObject:
    tenant = ctx.require_membership().tenant
    return {"changed": await sync_to_async(_apply)(tenant, {field: args.enabled})}


async def _preview_deploy_approval(ctx: ToolContext, args: ToggleArgs) -> JSONObject:
    return await _preview_toggle(ctx, args, "deploy_requires_approval")


async def _preview_public_flow_view(ctx: ToolContext, args: ToggleArgs) -> JSONObject:
    return await _preview_toggle(ctx, args, "public_flow_view")


@register(
    "set_deploy_approval",
    "「배포에 다른 Admin의 승인 필요」 설정을 켜거나 끈다(Admin 전용). 미리보기는 현재 값→새 값과 영향. 인자: enabled.",
    ToggleArgs,
    scope="flow.admin",
    permission=FLOW_APPROVE,
    writes=True,
    preview=_preview_deploy_approval,
)
async def set_deploy_approval(ctx: ToolContext, args: ToggleArgs) -> JSONObject:
    return await _toggle(ctx, args, "deploy_requires_approval")


@register(
    "set_public_flow_view",
    "「공개 흐름 보기」(익명이 /flows/{slug}에서 흐름을 봄)를 켜거나 끈다(Admin 전용). 미리보기는 현재 값→새 값과 영향. 인자: enabled.",
    ToggleArgs,
    scope="flow.admin",
    permission=FLOW_PUBLIC_VIEW,
    writes=True,
    preview=_preview_public_flow_view,
)
async def set_public_flow_view(ctx: ToolContext, args: ToggleArgs) -> JSONObject:
    return await _toggle(ctx, args, "public_flow_view")
