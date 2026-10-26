"""테넌트 HTTP `GET /api/tenant/audit-events/` — MCP 쓰기 감사 로그 읽기(#208). Admin(AUDIT_READ)만, 내 테넌트만."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.http import HttpRequest
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.flows.schema import JSON
from apps.mcp import audit
from apps.tenants.auth import member_or_tenant_key_auth
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import AUDIT_READ, require_permission

audit_router = Router(tags=["audit"], auth=member_or_tenant_key_auth)


class AuditEventOut(Schema):
    id: int
    created_at: datetime
    tool: str
    user_sub: str
    client_id: str
    jti: str
    arguments: JSON
    result_code: str
    confirm_stage: str | None


class AuditPageOut(Schema):
    items: list[AuditEventOut]
    next_cursor: str | None


class AuditPageQuery(Schema):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


def _tenant_id(subject: Membership | Tenant) -> uuid.UUID:
    return subject.tenant_id if isinstance(subject, Membership) else subject.id


@audit_router.get("/", response=AuditPageOut)
def audit_events_list(request: HttpRequest, query: Query[AuditPageQuery]) -> dict[str, JSON]:
    require_permission(request.auth, AUDIT_READ)
    try:
        rows, next_cursor = audit.page(_tenant_id(request.auth), query.cursor, query.limit)
    except audit.InvalidCursor as exc:
        raise HttpError(400, "cursor는 앞 응답의 next_cursor 값이어야 합니다.") from exc
    return {"items": [audit.event_out(e) for e in rows], "next_cursor": next_cursor}
