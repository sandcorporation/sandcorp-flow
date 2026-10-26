"""감사 로그 읽기 도구(#208) — `list_audit_events`. HTTP `GET /api/tenant/audit-events/`와 같은 권한(AUDIT_READ)·같은 쪽."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.mcp import audit
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.tenants.permissions import AUDIT_READ


class ListAuditEventsArgs(TenantArgs):
    limit: int = Field(default=50, ge=1, le=200, description="한 쪽 건수(최대 200)")
    cursor: str | None = Field(default=None, description="앞 결과의 next_cursor(이어 읽기)")


@register(
    "list_audit_events",
    "이 테넌트에서 MCP로 부른 쓰기 도구 기록(최신 순): 시각·도구·부른 사람(user_sub)·커넥터(client_id)·토큰(jti)·인자(길면 잘림)·"
    "결과(ok / 오류 코드 / internal / started=결과 모름). Admin만. 인자: limit?, cursor?(next_cursor). 실패: invalid_cursor.",
    ListAuditEventsArgs,
    scope="flow.read",
    permission=AUDIT_READ,
    idempotent=True,
)
async def list_audit_events(ctx: ToolContext, args: ListAuditEventsArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant_id
    try:
        rows, next_cursor = await sync_to_async(audit.page)(tenant_id, args.cursor, args.limit)
    except audit.InvalidCursor as exc:
        raise ToolError("invalid_cursor", "cursor는 앞 결과의 next_cursor 값이어야 합니다.") from exc
    return {"events": [audit.event_out(e) for e in rows], "next_cursor": next_cursor}
