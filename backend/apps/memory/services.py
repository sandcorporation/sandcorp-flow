"""상담 세션 읽기 서비스 — 세션 콘솔 API와 MCP 읽기 도구가 같은 쿼리를 쓴다(#77).

시험 한 턴 세션(``is_test``)은 어느 쪽에도 나오지 않는다. 모든 함수 첫 인자는 ``tenant_id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db.models import Max, Q

from apps.chat import presence
from apps.chat.models import ChatMessage, ChatSession
from apps.escalation.models import Escalation


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    visitor_id: str
    is_hitl: bool
    escalation_status: str  # "" | pending | claimed (활성 escalation이 있으면 그 상태)
    active: bool  # presence(SSE 연결됨)
    created_at: datetime
    last_activity: datetime
    tier: int  # 0 대기 escalation → 1 처리 중 → 2 활성 → 3 나머지


def list_session_rows(
    tenant_id: uuid.UUID | str,
    *,
    since: datetime,
    until: datetime | None = None,
    pin_live: bool = True,
    status: str = "all",
) -> list[SessionRow]:
    """세션 목록 — 계층(대기→처리 중→활성→나머지) 우선, 같은 계층 안에서는 최근 활동순.

    ``pin_live``면 escalation·활성 세션은 기간 밖이라도 포함한다(콘솔 기본, issue 139).
    ``status``: ``all`` | ``escalated``(대기·처리 중 escalation) | ``active``(연결 중).
    """
    esc_map = {
        str(sid): st
        for sid, st in Escalation.objects.filter(
            session__tenant_id=tenant_id,
            status__in=[Escalation.STATUS_PENDING, Escalation.STATUS_CLAIMED],
        ).values_list("session_id", "status")
    }
    active_ids = presence.active_sessions(str(tenant_id))
    window = Q(created_at__gte=since)
    if until is not None:
        window &= Q(created_at__lt=until)
    if pin_live:
        window |= Q(id__in=set(esc_map) | active_ids)
    qs = ChatSession.objects.filter(tenant_id=tenant_id, is_test=False).filter(window).annotate(last_msg=Max("messages__created_at"))

    def tier(sid: str) -> int:
        st = esc_map.get(sid)
        if st == Escalation.STATUS_PENDING:
            return 0
        if st == Escalation.STATUS_CLAIMED:
            return 1
        return 2 if sid in active_ids else 3

    rows: list[SessionRow] = []
    for s in qs:
        sid = str(s.id)
        if status == "escalated" and sid not in esc_map:
            continue
        if status == "active" and sid not in active_ids:
            continue
        last = getattr(s, "last_msg", None) or s.created_at  # .annotate() 동적 필드
        rows.append(SessionRow(sid, s.visitor_id, s.is_hitl, esc_map.get(sid, ""), sid in active_ids, s.created_at, last, tier(sid)))
    rows.sort(key=lambda r: r.last_activity, reverse=True)
    rows.sort(key=lambda r: r.tier)  # stable
    return rows


def get_session(tenant_id: uuid.UUID | str, session_id: str) -> ChatSession | None:
    """운영 세션 하나. 다른 테넌트·시험 세션·잘못된 id는 None."""
    try:
        return ChatSession.objects.select_related("flow_version__flow").get(id=session_id, tenant_id=tenant_id, is_test=False)
    except (ChatSession.DoesNotExist, ValidationError):
        return None


def session_messages(session: ChatSession, *, offset: int = 0, limit: int | None = None) -> list[ChatMessage]:
    qs = ChatMessage.objects.filter(session=session).order_by("created_at", "id")
    return list(qs[offset:] if limit is None else qs[offset : offset + limit])


def session_escalations(session: ChatSession) -> list[Escalation]:
    """이 세션의 사람 넘김 이력(해결된 것 포함, 오래된 순)."""
    return list(Escalation.objects.filter(session=session).order_by("created_at"))


def visitor_ids(tenant_id: uuid.UUID, search: str | None = None) -> list[str]:
    """이 테넌트의 방문자 식별값(시험 세션 제외, 중복 없음). 방문자 화면 API와 MCP 읽기 도구가 같이 쓴다."""
    qs = ChatSession.objects.filter(tenant_id=tenant_id, is_test=False)
    if search:
        qs = qs.filter(visitor_id__icontains=search)
    return sorted(set(qs.values_list("visitor_id", flat=True)))


def visitor_memories(tenant_id: uuid.UUID, visitor_id: str) -> list[dict[str, str]]:
    """방문자 기억 항목 ``{id, key, value}``. 방문자 화면 API와 MCP 읽기 도구가 같이 쓴다."""
    from apps.memory.models import VisitorMemory

    rows = VisitorMemory.objects.filter(tenant_id=tenant_id, visitor_id=visitor_id).order_by("key")
    return [{"id": str(m.id), "key": m.key, "value": m.value} for m in rows]
