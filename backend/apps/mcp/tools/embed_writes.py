"""임베드 쓰기 도구(#250, 우산 #245 E5) — ``set_flow_embed``·``set_default_flow``·``set_embed_origins``.

- 스코프 ``flow.deploy``, 권한 ``FLOW_DEPLOY``(Admin), 2단계 확인, 감사 로그(디스패처). HTTP ``/flows/{id}/embed/``·
  ``/default/``·``/embed-origins/``·``PATCH /config/``와 **같은 서비스·검증 함수**(``apps.flows.services``·
  ``embed_policy.normalize_embed_origins``)를 부른다 — 두 길의 규칙이 갈라지지 않는다.
- 미리보기에는 「이 주소에 붙인 코드가 어떻게 되는지」(``effect``)와 현재→새 값을 싣는다. 미리보기 단계에서도 허용 사이트를
  검증해 틀린 값은 확인 토큰을 받기 전에 막는다.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.flows import services
from apps.flows.embed_policy import EmbedOriginsInvalid, normalize_embed_origins
from apps.flows.models import Flow
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.mcp.tools.flows import FlowArgs, _flow_row, _service, _slug, _tenant_id
from apps.tenants.models import TenantConfig
from apps.tenants.permissions import FLOW_DEPLOY


class SetFlowEmbedArgs(FlowArgs):
    enabled: bool = Field(description="키 주소 임베드를 켜려면 true, 끄려면 false")
    origins: list[str] | None = Field(
        default=None,
        description="이 흐름의 허용 사이트(scheme://host[:port], 최대 20개). 빼면 그대로, []이면 비워 테넌트 목록을 따른다.",
    )


class SetEmbedOriginsArgs(TenantArgs):
    origins: list[str] = Field(description="테넌트 허용 사이트(scheme://host[:port], 최대 20개). []이면 어디서나 붙는다.")


def _normalized(origins: list[str]) -> list[str]:
    try:
        return normalize_embed_origins(origins)
    except EmbedOriginsInvalid as exc:
        raise ToolError("invalid_embed_origins", exc.message) from exc


def _path(flow: Flow, slug: str) -> str:
    return f"/chatbot/{slug}/{flow.embed_key}/"


def _flow_origins_effect(origins: list[str]) -> str:
    if not origins:
        return "이 흐름의 허용 사이트를 비워 테넌트 목록을 따른다"
    return f"허용 사이트는 {', '.join(origins)}뿐이다(다른 사이트에 붙인 iframe은 브라우저가 막는다)"


# ── set_flow_embed ───────────────────────────────────────────────────────────


def _embed_effect(flow: Flow, slug: str, enabled: bool, origins: list[str] | None) -> str:
    path = _path(flow, slug)
    if enabled:
        parts = [f"{path}에 붙인 코드가 열려 이 흐름이 답한다"]
        if flow.deployed_version_id is None:
            parts.append("(아직 배포한 버전이 없어 실제로는 켜지지 않는다 — 먼저 배포)")
    elif flow.is_public:
        parts = [f"기본 흐름이라 {path}는 끄더라도 계속 열려 있다"]
    else:
        parts = [f"{path}에 붙인 코드는 「열려 있지 않음」 화면을 보이고, 이미 열린 대화는 메시지를 더 보낼 수 없다"]
    if origins is not None:
        parts.append(_flow_origins_effect(origins))
    return ". ".join(parts)


async def _preview_set_flow_embed(ctx: ToolContext, args: SetFlowEmbedArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant
    new_origins = _normalized(args.origins) if args.origins is not None else None
    flow = await _service(lambda: services.get_flow(tenant.id, args.flow_id))
    current = {"enabled": flow.embed_enabled, "origins": list(flow.embed_origins)}
    new = {"enabled": args.enabled, "origins": current["origins"] if new_origins is None else new_origins}
    preview: JSONObject = {
        "flow": {"id": str(flow.id), "name": flow.name},
        "embed_path": _path(flow, tenant.slug),
        "current": current,
        "new": new,
        "effect": _embed_effect(flow, tenant.slug, args.enabled, new_origins),
    }
    if current == new:
        preview["no_change"] = True
    return preview


@register(
    "set_flow_embed",
    "흐름의 키 주소(/chatbot/<slug>/<embed_key>/) 임베드를 켜거나 끄고, 주면 그 흐름의 허용 사이트도 바꾼다(Admin 전용, flow.deploy). "
    "켜려면 배포한 버전이 있어야 한다(실패: not_embeddable). 기본 흐름은 끄더라도 키 주소가 열려 있다. origins는 틀리면 아무것도 안 바뀐다"
    "(실패: invalid_embed_origins). 미리보기는 현재→새 값과 「이 주소에 붙인 코드가 어떻게 되는지」(effect). "
    "인자: flow_id, enabled, origins?(빼면 그대로, []이면 비워 테넌트 목록을 따름). 실패: flow_not_found.",
    SetFlowEmbedArgs,
    scope="flow.deploy",
    permission=FLOW_DEPLOY,
    writes=True,
    preview=_preview_set_flow_embed,
)
async def set_flow_embed(ctx: ToolContext, args: SetFlowEmbedArgs) -> JSONObject:
    tenant_id, slug = _tenant_id(ctx), _slug(ctx)
    new_origins = _normalized(args.origins) if args.origins is not None else None  # 켜기 전에 검증 — 반쯤 적용되지 않게

    def apply() -> JSONObject:
        flow = services.set_embed_enabled(tenant_id, args.flow_id, args.enabled)
        if new_origins is not None:
            flow = services.set_embed_origins(tenant_id, args.flow_id, new_origins)
        return _flow_row(flow, slug)  # 관계 조회가 있어 동기 쪽에서 만든다

    return await _service(apply)


# ── set_default_flow ─────────────────────────────────────────────────────────


async def _preview_set_default_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant

    def load() -> tuple[Flow, Flow | None]:
        flow = services.get_flow(tenant.id, args.flow_id)
        return flow, Flow.objects.for_tenant(tenant.id).filter(is_public=True, archived_at__isnull=True).first()

    flow, current = await _service(load)
    base = f"/chatbot/{tenant.slug}/"
    preview: JSONObject = {
        "current_default": {"id": str(current.id), "name": current.name} if current else None,
        "new_default": {"id": str(flow.id), "name": flow.name},
    }
    if flow.is_public:
        preview["no_change"] = True
        preview["effect"] = f"이미 기본 흐름이라 {base}는 그대로 이 흐름이 답한다"
        return preview
    parts = [f"{base}에 붙인 코드는 이제부터 새 대화를 「{flow.name}」이 받는다(열려 있던 대화는 끊기지 않고 옛 흐름으로 이어진다)"]
    if current is not None and not current.embed_enabled:
        parts.append(f"옛 기본 흐름 「{current.name}」은 임베드를 켜 두지 않았으니 키 주소 {_path(current, tenant.slug)}가 닫힌다")
    if flow.deployed_version_id is None:
        parts.append("(아직 배포한 버전이 없어 바뀌지 않는다 — 먼저 배포)")
    preview["effect"] = ". ".join(parts)
    return preview


@register(
    "set_default_flow",
    "기본 흐름(위젯 /chatbot/<slug>/가 도는 흐름)을 바꾼다(Admin 전용, flow.deploy). 옛 기본 흐름 해제와 새 흐름 지정이 한 번에 일어난다. "
    "배포한 버전이 있는 흐름만(실패: not_embeddable). 미리보기는 현재→새 기본 흐름과 「기존 주소가 어떻게 되는지」(effect). "
    "인자: flow_id. 실패: flow_not_found.",
    FlowArgs,
    scope="flow.deploy",
    permission=FLOW_DEPLOY,
    writes=True,
    preview=_preview_set_default_flow,
)
async def set_default_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    tenant_id, slug = _tenant_id(ctx), _slug(ctx)
    return await _service(lambda: _flow_row(services.set_default_flow(tenant_id, args.flow_id), slug))


# ── set_embed_origins (테넌트) ───────────────────────────────────────────────


async def _preview_set_embed_origins(ctx: ToolContext, args: SetEmbedOriginsArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant
    new = _normalized(args.origins)
    current = list(await sync_to_async(lambda: TenantConfig.objects.get(tenant=tenant).embed_origins)())
    base = f"/chatbot/{tenant.slug}/"
    if new:
        effect = (
            f"{base}와 자기 목록이 없는 흐름의 키 주소는 {', '.join(new)}에서만 붙는다"
            "(자기 목록이 있는 흐름은 그 목록을 따른다; 다른 사이트의 iframe은 브라우저가 막는다)"
        )
    else:
        effect = f"{base}와 자기 목록이 없는 흐름의 키 주소를 어디서나 붙일 수 있게 된다(자기 목록이 있는 흐름은 그 목록을 따른다)"
    preview: JSONObject = {"current": current, "new": new, "effect": effect}
    if current == new:
        preview["no_change"] = True
    return preview


@register(
    "set_embed_origins",
    "테넌트의 허용 사이트 목록(기본 흐름 주소와 자기 목록이 없는 흐름의 키 주소를 붙일 수 있는 사이트)을 통째로 바꾼다(Admin 전용, flow.deploy). "
    "비우면 어디서나 붙는다. 검증은 REST와 같다(실패: invalid_embed_origins). 미리보기는 현재→새 목록과 「붙인 코드가 어떻게 되는지」(effect). "
    "응답: {embed_origins: {before, after}}. 인자: origins(최대 20개), tenant?.",
    SetEmbedOriginsArgs,
    scope="flow.deploy",
    permission=FLOW_DEPLOY,
    writes=True,
    preview=_preview_set_embed_origins,
)
async def set_embed_origins(ctx: ToolContext, args: SetEmbedOriginsArgs) -> JSONObject:
    tenant = ctx.require_membership().tenant

    def apply() -> JSONObject:
        before = list(TenantConfig.objects.get(tenant=tenant).embed_origins)
        try:
            after = services.set_tenant_embed_origins(tenant.id, args.origins)
        except services.FlowServiceError as exc:
            raise ToolError(exc.code, exc.message) from exc
        return {"embed_origins": {"before": before, "after": after}}

    return await sync_to_async(apply)()
