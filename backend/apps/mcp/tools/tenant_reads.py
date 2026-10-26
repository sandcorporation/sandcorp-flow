"""테넌트 설정·팀·비밀 이름·사용량·배포 요청·방문자 읽기 도구(#211, 우산 #207) — 모두 `flow.read`.

데이터는 같은 일을 하는 HTTP 핸들러와 같은 함수·쿼리를 쓴다(권한 비트도 같다. 예외: `list_deploy_requests`는 HTTP가
멤버 누구에게나 열어 두지만 우산 결정대로 FLOW_APPROVE).

**비밀 처리 원칙** — 이 모듈의 어떤 응답에도 API 키 값·가림 문자열(`********`)·base_url·webhook_url·TENANT_KEY·
초대 토큰/URL·비밀 값/암호문이 없다. 설정은 허용 목록으로 만든다(`get_settings`): 새 설정 칸이 생겨도 기본은 빠진다.
"""

from __future__ import annotations

import json
from typing import Literal

from asgiref.sync import sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from pydantic import Field

from apps.flows.api import deploy_request_rows
from apps.flows.schema import JSON
from apps.mcp.access import TenantArgs
from apps.mcp.pagination import page_of
from apps.mcp.registry import JSONObject, ToolContext, register
from apps.memory import services as memory_services
from apps.tenants import tenant_secrets as secrets_service
from apps.tenants.api import _config_out, _invite_out, _member_out
from apps.tenants.members import pending_invites
from apps.tenants.models import Membership
from apps.tenants.permissions import AGENTS_MANAGE, FLOW_APPROVE
from apps.usage.api import tenant_usage_report


def jsonable[T](value: T) -> JSON:
    """HTTP 핸들러가 돌려주는 값(datetime·Decimal 포함)을 JSON 값으로 — Ninja가 HTTP에서 하는 직렬화와 같은 인코더."""
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


class PageArgs(TenantArgs):
    limit: int = Field(default=50, ge=1, le=200, description="한 쪽 건수(최대 200)")
    cursor: str | None = Field(default=None, description="앞 결과의 next_cursor")


# ── 설정 ──────────────────────────────────────────────────────────────────────

# 응답에 그대로 싣는 비비밀 칸(허용 목록).
_PLAIN_SETTINGS = (
    "slug", "system_prompt", "agent_display_name", "welcome_message", "brand_name",
    "hitl_enabled", "hitl_timezone", "hitl_schedule", "hitl_holidays",
    "require_identity_verification", "deploy_requires_approval", "public_flow_view",
    "topic_scope_enabled", "scope_description", "scope_refusal_message",
)


def settings_view(config_out: dict[str, JSON]) -> JSONObject:
    """HTTP `GET /api/tenant/config/`와 같은 값(`_config_out`)에서 비밀을 뺀 모양. 키는 설정 여부만, 주소는 아예 없다."""
    view: JSONObject = {k: config_out[k] for k in _PLAIN_SETTINGS}
    view["providers"] = {
        "llm": {
            "provider_type": config_out["llm_provider_type"], "model": config_out["model_id"],
            "extraction_model": config_out["extraction_model"], "api_key_set": bool(config_out["llm_api_key"]),
        },
        "embed": {
            "provider_type": config_out["embed_provider_type"], "model": config_out["embed_model"],
            "embed_dim": config_out["embed_dim"], "api_key_set": bool(config_out["embed_api_key"]),
        },
        "ocr": {
            "provider_type": config_out["ocr_provider_type"], "model": config_out["ocr_model"],
            "api_key_set": bool(config_out["ocr_api_key"]),
        },
    }
    view["webhook"] = {"webhook_set": bool(config_out["webhook_url"]), "webhook_type": config_out["webhook_type"]}
    return view


@register(
    "get_settings",
    "테넌트 설정(비밀 제외): 시스템 프롬프트·상담 문구·주제 범위·사람 넘김(hitl_*)·본인 확인·배포 승인 필요·공개 흐름 보기, "
    "providers{llm|embed|ocr: provider_type·model·api_key_set}, webhook{webhook_set·webhook_type}. "
    "API 키 값·base_url·웹훅 주소·TENANT_KEY는 주지 않는다(설정 여부 bool만). 인자: tenant?.",
    TenantArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def get_settings(ctx: ToolContext, args: TenantArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant
    return await sync_to_async(lambda: settings_view(_config_out(tenant.config)))()


# ── 팀 ────────────────────────────────────────────────────────────────────────


@register(
    "list_members",
    "팀원 목록(만든 순): id·이름·이메일·역할(admin|member)·활성 여부(is_active). 인자: tenant?, limit?, cursor?(next_cursor). "
    "실패: invalid_cursor.",
    PageArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_members(ctx: ToolContext, args: PageArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant

    def load() -> list[JSONObject]:
        rows = Membership.objects.select_related("user").filter(tenant=tenant).order_by("created_at")
        return [
            {k: v for k, v in _member_out(m).items() if k in ("id", "name", "email", "role", "is_active")} for m in rows
        ]

    members = await sync_to_async(load)()
    page, next_cursor = page_of(members, args.cursor, args.limit)
    return {"members": page, "next_cursor": next_cursor}


@register(
    "list_invites",
    "아직 쓸 수 있는 팀원 초대(최신순): id·역할·만료 시각(expires_at)·만든 시각(created_at)만. 초대 토큰·링크는 주지 않는다. "
    "Admin만. 인자: tenant?, limit?, cursor?. 실패: admin_required, invalid_cursor.",
    PageArgs,
    scope="flow.read",
    permission=AGENTS_MANAGE,
    idempotent=True,
)
async def list_invites(ctx: ToolContext, args: PageArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant
    invites = await sync_to_async(lambda: [_invite_out(i) for i in pending_invites(tenant)])()
    page, next_cursor = page_of(invites, args.cursor, args.limit)
    return {"invites": page, "next_cursor": next_cursor}


# ── 비밀 이름·허용 도메인 ─────────────────────────────────────────────────────


@register(
    "list_secrets",
    "HTTP 도구용 비밀의 이름 목록(이름순): name·created_at·updated_at. 값·암호문은 누구에게도 주지 않는다. "
    "인자: tenant?, limit?, cursor?. 실패: invalid_cursor.",
    PageArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_secrets(ctx: ToolContext, args: PageArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    rows = await sync_to_async(secrets_service.list_secrets)(tenant_id)
    secrets: list[JSONObject] = [
        {"name": s.name, "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat()} for s in rows
    ]
    page, next_cursor = page_of(secrets, args.cursor, args.limit)
    return {"secrets": page, "next_cursor": next_cursor}


@register(
    "list_allowed_domains",
    "HTTP 도구가 나갈 수 있는 허용 도메인 패턴 목록: id·pattern·created_at. 인자: tenant?, limit?, cursor?. 실패: invalid_cursor.",
    PageArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_allowed_domains(ctx: ToolContext, args: PageArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    rows = await sync_to_async(secrets_service.list_domains)(tenant_id)
    domains: list[JSONObject] = [
        {"id": str(d.id), "pattern": d.pattern, "created_at": d.created_at.isoformat()} for d in rows
    ]
    page, next_cursor = page_of(domains, args.cursor, args.limit)
    return {"domains": page, "next_cursor": next_cursor}


# ── 사용량 ────────────────────────────────────────────────────────────────────


class GetUsageArgs(TenantArgs):
    days: int = Field(default=30, ge=1, le=365, description="최근 며칠(기본 30, 최대 365)")


@register(
    "get_usage",
    "테넌트의 토큰 사용량·비용(USD): 합계(total_tokens·cost_usd)·call_type별(by_call_type)·일별(daily). "
    "단가를 모르는 모델은 합계에서 빠지고 unknown_cost_models에 나온다. 인자: tenant?, days?(기본 30).",
    GetUsageArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def get_usage(ctx: ToolContext, args: GetUsageArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    report = await sync_to_async(lambda: jsonable(tenant_usage_report(tenant_id, args.days)))()
    assert isinstance(report, dict)
    return report


# ── 배포 요청 ─────────────────────────────────────────────────────────────────


class ListDeployRequestsArgs(PageArgs):
    status: Literal["pending", "approved", "rejected", "superseded"] | None = Field(
        default=None, description="이 상태만(기본: 전부)"
    )


@register(
    "list_deploy_requests",
    "배포 요청 목록(최신순): id·flow_id·flow_name·version·status(pending|approved|rejected|superseded)·요청자·결정자·메모·시각. "
    "Admin만(승인 권한). 인자: tenant?, status?, limit?, cursor?. 실패: admin_required, invalid_cursor.",
    ListDeployRequestsArgs,
    scope="flow.read",
    permission=FLOW_APPROVE,
    idempotent=True,
)
async def list_deploy_requests(ctx: ToolContext, args: ListDeployRequestsArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    rows = await sync_to_async(lambda: jsonable(deploy_request_rows(tenant_id, args.status)))()
    assert isinstance(rows, list)
    page, next_cursor = page_of(rows, args.cursor, args.limit)
    return {"deploy_requests": page, "next_cursor": next_cursor}


# ── 방문자 ────────────────────────────────────────────────────────────────────


class ListVisitorsArgs(PageArgs):
    search: str | None = Field(default=None, description="방문자 식별값에 들어 있는 글자(대소문자 무시)")


@register(
    "list_visitors",
    "상담한 적 있는 방문자 식별값 목록(시험 세션 제외, 정렬됨): visitor_ids. 인자: tenant?, search?, limit?, cursor?. "
    "방문자의 대화는 list_sessions, 기억은 get_visitor_memory.",
    ListVisitorsArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_visitors(ctx: ToolContext, args: ListVisitorsArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    ids = await sync_to_async(memory_services.visitor_ids)(tenant_id, args.search)
    page, next_cursor = page_of(ids, args.cursor, args.limit)
    return {"visitor_ids": page, "next_cursor": next_cursor}


class GetVisitorMemoryArgs(PageArgs):
    visitor_id: str = Field(description="방문자 식별값(list_visitors로 확인)")


@register(
    "get_visitor_memory",
    "방문자 하나의 기억 항목(키 순): id·key·value. 기억이 없으면(또는 없는 방문자면) 빈 목록. "
    "인자: visitor_id, tenant?, limit?, cursor?. 실패: invalid_cursor.",
    GetVisitorMemoryArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def get_visitor_memory(ctx: ToolContext, args: GetVisitorMemoryArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    rows: list[JSONObject] = [
        {**m} for m in await sync_to_async(memory_services.visitor_memories)(tenant_id, args.visitor_id)
    ]
    page, next_cursor = page_of(rows, args.cursor, args.limit)
    return {"visitor_id": args.visitor_id, "memories": page, "next_cursor": next_cursor}
