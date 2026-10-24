"""상담 세션 읽기 도구(#77, 스펙 #74 결정 6·7) — ``list_sessions``·``get_session``.

세션 콘솔과 같은 서비스(``apps/memory/services.py``)를 쓴다. 시험 한 턴 세션(``is_test``)은 나오지 않는다.
방문자 식별값(``visitor_id``)은 테넌트 화면과 같은 값만 — 그 밖의 개인정보는 싣지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.mcp.access import TenantArgs
from apps.mcp.pagination import decode_cursor, encode_cursor
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register
from apps.memory import services

DEFAULT_WINDOW_DAYS = 7
MAX_MESSAGE_CHARS = 2000


class ListSessionsArgs(TenantArgs):
    since: datetime | None = Field(default=None, description="이 시각(ISO 8601) 이후 시작한 세션만. 기본: 최근 7일.")
    until: datetime | None = Field(default=None, description="이 시각(ISO 8601) 전에 시작한 세션만.")
    status: Literal["all", "escalated", "active"] = Field(
        default="all", description="all | escalated(사람 넘김 대기·처리 중) | active(연결 중)"
    )
    limit: int = Field(default=50, ge=1, le=100, description="한 쪽 건수(최대 100)")
    cursor: str | None = Field(default=None, description="앞 결과의 next_cursor")


class GetSessionArgs(TenantArgs):
    session_id: str = Field(description="세션 id(list_sessions로 확인)")
    limit: int = Field(default=50, ge=1, le=200, description="한 번에 읽을 메시지 수(최대 200)")
    cursor: str | None = Field(default=None, description="앞 결과의 next_cursor(이어 읽기)")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@register(
    "list_sessions",
    "상담 세션 목록(시험 세션 제외): 사람 넘김 대기→처리 중→연결 중→나머지 순, 같은 순위는 최근 활동순. "
    "각 행: session_id·visitor_id·is_hitl·escalation_status(''|pending|claimed)·active·created_at·last_activity. "
    "인자: since?(기본 7일 전)·until?·status?(all|escalated|active)·limit?·cursor?. 다음 쪽이 있으면 next_cursor.",
    ListSessionsArgs,
    scope="flow.read",
)
async def list_sessions(ctx: ToolContext, args: ListSessionsArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant.id
    offset = decode_cursor(args.cursor)
    since = _aware(args.since) if args.since else datetime.now(timezone.utc) - timedelta(days=DEFAULT_WINDOW_DAYS)
    until = _aware(args.until) if args.until else None
    rows = await sync_to_async(services.list_session_rows)(
        tenant_id, since=since, until=until, pin_live=False, status=args.status
    )
    page = rows[offset : offset + args.limit]
    return {
        "sessions": [
            {
                "session_id": r.session_id,
                "visitor_id": r.visitor_id,
                "is_hitl": r.is_hitl,
                "escalation_status": r.escalation_status,
                "active": r.active,
                "created_at": r.created_at.isoformat(),
                "last_activity": r.last_activity.isoformat(),
            }
            for r in page
        ],
        "next_cursor": encode_cursor(offset + args.limit) if len(rows) > offset + args.limit else None,
    }


@register(
    "get_session",
    "상담 세션 하나: 방문자·메시지(오래된 것부터, role=user|assistant|human_agent)·사람 넘김 이력(trigger·reason·"
    "status·시각)·대화가 고정된 흐름 버전(flow_version={flow, number}, 없으면 null). 긴 대화는 limit개씩 자르고 "
    "has_more=true면 next_cursor로 이어 읽는다. 메시지 하나가 2000자를 넘으면 잘라 truncated=true. "
    "인자: session_id, limit?, cursor?. 실패: session_not_found(없거나 남의 테넌트·시험 세션).",
    GetSessionArgs,
    scope="flow.read",
)
async def get_session(ctx: ToolContext, args: GetSessionArgs) -> JSONObject:
    tenant_id = ctx.require_membership().tenant.id
    offset = decode_cursor(args.cursor)

    def load():
        session = services.get_session(tenant_id, args.session_id)
        if session is None:
            return None
        messages = services.session_messages(session, offset=offset, limit=args.limit + 1)
        return session, messages, services.session_escalations(session)

    loaded = await sync_to_async(load)()
    if loaded is None:
        raise ToolError("session_not_found", "그런 상담 세션이 없습니다(list_sessions로 id를 확인하세요).")
    session, messages, escalations = loaded
    page = messages[: args.limit]
    version = session.flow_version
    has_more = len(messages) > args.limit
    return {
        "session_id": str(session.id),
        "visitor_id": session.visitor_id,
        "is_hitl": session.is_hitl,
        "is_test": session.is_test,
        "created_at": session.created_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "flow_version": {"flow": version.flow.name, "number": version.number} if version else None,
        "messages": [
            {
                "role": m.role,
                "content": m.content[:MAX_MESSAGE_CHARS],
                "truncated": len(m.content) > MAX_MESSAGE_CHARS,
                "created_at": m.created_at.isoformat(),
            }
            for m in page
        ],
        "has_more": has_more,
        "next_cursor": encode_cursor(offset + args.limit) if has_more else None,
        "escalations": [
            {
                "trigger": e.trigger_type,
                "reason": e.reason,
                "status": e.status,
                "created_at": e.created_at.isoformat(),
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in escalations
        ],
    }
