"""MCP 쓰기 감사 로그 쓰기·읽기·정리(#208).

- `begin(...)` : 도구를 부르기 **전에** 한 줄을 `started`로 쓴다. 실패하면 예외 — 디스패처는 도구를 실행하지 않는다
                 (fail-closed: 기록 없는 쓰기를 만들지 않는다).
- `finish(...)` : 결과 코드와 풀린 테넌트를 채운다. 실패해도 도구 결과는 그대로 돌려준다(행은 `started`로 남아 「결과 모름」).
- `page(...)` : 테넌트 하나의 기록을 최신 순으로 id 키셋 커서 페이지(HTTP·MCP 읽기 경로가 같이 쓴다).
- `prune_audit_events()` : 보존 기간이 지난 행 삭제.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.flows.schema import JSON
from apps.mcp.models import McpAuditEvent
from apps.tenants.models import Membership, User
from apps.tenants.resource_token import AccessToken

ARGUMENTS_CAP = 4000  # 인자 JSON 문자 수 상한. 넘으면 앞부분만 문자열로 남긴다.


class InvalidCursor(ValueError):
    pass


def cap_arguments(arguments: JSON) -> JSON:
    text = json.dumps(arguments, ensure_ascii=False)
    if len(text) <= ARGUMENTS_CAP:
        return arguments
    return {"_truncated": True, "preview": text[:ARGUMENTS_CAP]}


def _begin(tool: str, token: AccessToken, issuer: str, arguments: JSON) -> int:
    with transaction.atomic():  # 실패가 바깥 트랜잭션을 망가뜨리지 않게(세이브포인트)
        user = User.objects.filter(iss=issuer, sub=token.sub).first()
        event = McpAuditEvent.objects.create(
            user=user,
            token_sub=token.sub,
            client_id=token.client_id,
            jti=token.jti,
            tool=tool,
            arguments=cap_arguments(arguments),
        )
    return event.pk


def _finish(event_id: int, result_code: str, tenant_id: uuid.UUID | None) -> None:
    with transaction.atomic():
        McpAuditEvent.objects.filter(pk=event_id).update(result_code=result_code[:64], tenant_id=tenant_id)


async def begin(tool: str, token: AccessToken, issuer: str, arguments: JSON) -> int:
    return await sync_to_async(_begin)(tool, token, issuer, arguments)


async def finish(event_id: int, result_code: str, membership: Membership | None) -> None:
    await sync_to_async(_finish)(event_id, result_code, membership.tenant_id if membership else None)


# ── 읽기 ─────────────────────────────────────────────────────────────────────


def _encode_cursor(event_id: int) -> str:
    return base64.urlsafe_b64encode(f"a:{event_id}".encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        prefix, _, number = base64.urlsafe_b64decode(cursor.encode()).decode().partition(":")
        event_id = int(number)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor(cursor) from exc
    if prefix != "a" or event_id < 1:
        raise InvalidCursor(cursor)
    return event_id


def event_out(event: McpAuditEvent) -> dict[str, JSON]:
    return {
        "id": event.pk,
        "created_at": event.created_at.isoformat(),
        "tool": event.tool,
        "user_sub": event.token_sub,
        "client_id": event.client_id,
        "jti": event.jti,
        "arguments": event.arguments,
        "result_code": event.result_code,
        "confirm_stage": event.confirm_stage,
    }


def page(tenant_id: uuid.UUID, cursor: str | None, limit: int) -> tuple[list[McpAuditEvent], str | None]:
    """최신 순 한 쪽과 다음 쪽 커서(없으면 None). 잘못된 커서는 `InvalidCursor`."""
    query = McpAuditEvent.objects.filter(tenant_id=tenant_id).order_by("-id")
    if cursor:
        query = query.filter(id__lt=_decode_cursor(cursor))
    rows = list(query[: limit + 1])
    if len(rows) > limit:
        return rows[:limit], _encode_cursor(rows[limit - 1].pk)
    return rows, None


# ── 정리 ─────────────────────────────────────────────────────────────────────


def prune_audit_events() -> int:
    cutoff = timezone.now() - timedelta(days=settings.MCP_AUDIT_RETENTION_DAYS)
    deleted, _ = McpAuditEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted
