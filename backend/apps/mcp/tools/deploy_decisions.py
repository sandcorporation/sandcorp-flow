"""배포 요청 승인·거절 도구(#213, 우산 #207) — 2단계, `flow.deploy`(호환 분기 없음), 권한 비트 `flow.approve`.

서비스 `approve_deploy_request`/`reject_deploy_request`를 그대로 쓴다. MCP에서는 자기가 낸 요청을 승인하지 못한다
(미리보기부터 `self_approval`) — 서비스도 같은 규칙으로 막지만, 확인 토큰을 받기 전에 끊는다. 거절(철회)은 허용한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import Field

from apps.flows import services
from apps.flows.models import DeployRequest
from apps.flows.schema import JSON
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, register
from apps.mcp.tools.flows import _service
from apps.tenants.permissions import FLOW_APPROVE


class DecideArgs(TenantArgs):
    request_id: str = Field(description="배포 요청 id(list_deploy_requests로 확인)")
    note: str | None = Field(default=None, description="결정 메모(선택, 200자 이내)")


def _who(request: DeployRequest) -> JSON:
    user = request.requested_by
    return {"name": user.name, "email": user.email} if user else None


def _preview(action: str, *, approving: bool) -> Callable[[ToolContext, DecideArgs], Awaitable[JSONObject]]:
    async def preview(ctx: ToolContext, args: DecideArgs) -> JSONObject:
        membership = ctx.require_membership()
        tenant_id = membership.tenant_id

        def load() -> JSONObject:
            request = services.get_pending_deploy_request(tenant_id, args.request_id)
            if approving and request.requested_by_id is not None and request.requested_by_id == membership.user_id:
                raise services.FlowServiceError(
                    "자기가 올린 배포 요청은 MCP에서 승인할 수 없습니다. 다른 Admin이 승인하거나, 거절(철회)하세요.",
                    code="self_approval",
                )
            current = request.flow.deployed_version
            return {
                "action": action,
                "request_id": str(request.id),
                "flow_name": request.flow.name,
                "requested_version": request.version.number,
                "current_version": current.number if current else None,
                "requested_by": _who(request),
                "requested_at": request.created_at.isoformat(),
                "status": request.status,
            }

        return await _service(load)

    return preview


def _row(request: DeployRequest) -> JSONObject:
    return {"request_id": str(request.id), "status": request.status, "flow_id": str(request.flow_id)}


@register(
    "approve_deploy_request",
    "대기 중인 배포 요청을 승인하고 그 버전을 배포한다. 미리보기는 흐름 이름·요청 버전·현재 배포 버전·요청자·요청 시각·상태. "
    "내가 낸 요청은 승인할 수 없다(self_approval; 거절로 철회). 인자: request_id, note?. "
    "실패: request_not_found·request_not_pending·self_approval·flow_invalid·admin_required.",
    DecideArgs,
    scope="flow.deploy",
    permission=FLOW_APPROVE,
    writes=True,
    preview=_preview("대기 중인 배포 요청을 승인하고 그 버전을 배포한다", approving=True),
)
async def approve_deploy_request(ctx: ToolContext, args: DecideArgs) -> JSONObject:
    membership = ctx.require_membership()
    done = await _service(
        lambda: services.approve_deploy_request(
            membership.tenant_id, args.request_id, decided_by_id=membership.user_id, note=args.note
        )
    )
    return {"approved": True, **_row(done)}


@register(
    "reject_deploy_request",
    "대기 중인 배포 요청을 거절한다(배포 안 됨; 내가 낸 요청이면 철회). 미리보기는 승인과 같다. 인자: request_id, note?. "
    "실패: request_not_found·request_not_pending·admin_required.",
    DecideArgs,
    scope="flow.deploy",
    permission=FLOW_APPROVE,
    writes=True,
    preview=_preview("대기 중인 배포 요청을 거절한다(배포되지 않는다)", approving=False),
)
async def reject_deploy_request(ctx: ToolContext, args: DecideArgs) -> JSONObject:
    membership = ctx.require_membership()
    done = await _service(
        lambda: services.reject_deploy_request(
            membership.tenant_id, args.request_id, decided_by_id=membership.user_id, note=args.note
        )
    )
    return {"rejected": True, **_row(done)}
