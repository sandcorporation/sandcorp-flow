from typing import List, Optional
from ninja import Router, Schema
from django.shortcuts import get_object_or_404
from apps.tenants.auth import member_auth
from apps.memory.models import VisitorMemory
from apps.memory.manager import upsert_memory, delete_memory

memory_router = Router(tags=["memory"], auth=member_auth)
session_router = Router(tags=["sessions"], auth=member_auth)


class DetailOut(Schema):
    detail: str


class SessionMessageOut(Schema):
    id: str
    role: str
    content: str
    created_at: str


class SessionListItemOut(Schema):
    session_id: str
    visitor_id: str
    is_hitl: bool
    escalation_status: str = ""   # "" | pending | claimed (활성 escalation이 있으면 그 상태)
    active: bool                   # presence(SSE 연결됨)
    created_at: str
    last_activity: str


# 콘솔 기본 작업 집합: 최근 N일 세션 + escalation/활성은 창과 무관하게 항상 포함(issue 139).
SESSION_WINDOW_DAYS = 7


@session_router.get("/", response=List[SessionListItemOut])
def list_sessions(request, limit: int = 50, offset: int = 0):
    """세션 콘솔용 전체 세션 목록 — escalation(pending→claimed) → 활성(SSE) → 나머지(최근순).

    escalation·활성 세션은 최근창 밖이라도 상단에 고정. 나머지는 최근 N일 + 페이지네이션.
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.memory import services

    cutoff = timezone.now() - timedelta(days=SESSION_WINDOW_DAYS)
    rows = services.list_session_rows(request.auth.tenant.id, since=cutoff)
    return [
        {
            "session_id": r.session_id,
            "visitor_id": r.visitor_id,
            "is_hitl": r.is_hitl,
            "escalation_status": r.escalation_status,
            "active": r.active,
            "created_at": r.created_at.isoformat(),
            "last_activity": r.last_activity.isoformat(),
        }
        for r in rows[offset:offset + limit]
    ]


class TakeoverOut(Schema):
    escalation_id: str


@session_router.post("/{session_id}/takeover", response={200: TakeoverOut, 404: DetailOut, 409: DetailOut})
def takeover_session(request, session_id: str):
    """상담원이 임의 세션의 상담을 직접 시작한다(issue 140).

    자동-claimed Escalation(trigger=agent)을 만들고 is_hitl을 켜 방문자 메시지를 사람에게
    라우팅하며, 방문자에게 '상담원 연결됨'을 알린다. 멱등(같은 상담원 재진입은 기존 escalation
    반환) + 동시성(다른 상담원이 이미 잡은 세션은 409). 미claim된 AI escalation은 이어받는다.
    select_for_update로 세션을 잠가 동시 takeover 경쟁을 직렬화한다(영업시간과 무관).
    """
    from django.db import transaction
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation, EscalationClaim
    from apps.events.store import record_event
    from apps.events.types import SESSION_TAKEN_OVER

    tenant = request.auth.tenant
    agent = request.auth

    with transaction.atomic():
        try:
            session = ChatSession.objects.select_for_update().get(id=session_id, tenant_id=tenant.id)
        except ChatSession.DoesNotExist:
            return 404, {"detail": "Not found"}

        active = (
            Escalation.objects
            .filter(session=session, status__in=[Escalation.STATUS_PENDING, Escalation.STATUS_CLAIMED])
            .first()
        )
        if active is not None:
            claim = EscalationClaim.objects.filter(escalation=active).first()
            if claim is None:
                # 미claim(AI pending) → 이 상담원이 이어받는다
                EscalationClaim.objects.create(escalation=active, claimed_by=agent)
                active.status = Escalation.STATUS_CLAIMED
                active.save(update_fields=["status"])
            elif claim.claimed_by_id != agent.id:
                return 409, {"detail": "Already claimed by another agent"}
            esc = active  # 같은 상담원 재진입은 멱등
        else:
            esc = Escalation.objects.create(
                session=session,
                trigger_type=Escalation.TRIGGER_AGENT,
                status=Escalation.STATUS_CLAIMED,
            )
            EscalationClaim.objects.create(escalation=esc, claimed_by=agent)

        session.is_hitl = True
        session.save(update_fields=["is_hitl"])
        # 방문자 hitl_start·콘솔 델타는 SessionTakenOver 이벤트의 소비자가 발행한다(issue 151).
        record_event(
            SESSION_TAKEN_OVER, aggregate_id=str(session.id), tenant_id=str(tenant.id),
            payload={"escalation_id": str(esc.id), "claimed_by": str(agent.id)},
        )

    return 200, {"escalation_id": str(esc.id)}


class VisitorOut(Schema):
    visitor_id: str


class VisitorSessionOut(Schema):
    session_id: str
    visitor_id: str
    is_hitl: bool
    created_at: str


# checkpoint의 200 body는 LangGraph channel_values라 그래프 상태에 따라 구조가 달라지는
# 동적 맵이다 → 고정 Schema가 부적합하므로 dict로 유지(ADR-0014 와이어 불변). 404만 정형화.
@session_router.get("/{session_id}/checkpoint", response={200: dict, 404: DetailOut})
def get_session_checkpoint(request, session_id: str):
    from apps.chat.models import ChatSession
    from apps.agent.graph import _create_checkpointer

    tenant = request.auth.tenant
    try:
        ChatSession.objects.get(id=session_id, tenant_id=tenant.id)
    except ChatSession.DoesNotExist:
        return 404, {"detail": "Not found"}

    saver, conn = _create_checkpointer()
    try:
        checkpoint = saver.get({"configurable": {"thread_id": session_id}})
    finally:
        conn.close()

    if checkpoint is None:
        return 404, {"detail": "No checkpoint"}

    return 200, checkpoint.get("channel_values", {})


# 200 body: {"turns": [{user_message, chunks, chunk_count}, ...]} — 턴별 GraphRAG 검색 결과.
# rag_chunks는 휴지 체크포인트에선 비지만 히스토리에 남아 있어, 테넌트가 검색 과정을 본다(issue 207).
@session_router.get("/{session_id}/retrievals", response={200: dict, 404: DetailOut})
def get_session_retrievals(request, session_id: str):
    from apps.chat.models import ChatSession
    from apps.agent.graph import session_retrievals

    tenant = request.auth.tenant
    try:
        ChatSession.objects.get(id=session_id, tenant_id=tenant.id)
    except ChatSession.DoesNotExist:
        return 404, {"detail": "Not found"}

    return 200, {"turns": session_retrievals(str(session_id))}


@session_router.get("/{session_id}/messages/", response={200: List[SessionMessageOut], 404: DetailOut})
def get_session_messages(request, session_id: str):
    from apps.chat.models import ChatSession, ChatMessage

    tenant = request.auth.tenant
    try:
        session = ChatSession.objects.get(id=session_id, tenant_id=tenant.id)
    except ChatSession.DoesNotExist:
        return 404, {"detail": "Not found"}

    messages = ChatMessage.objects.filter(session=session).order_by("created_at")
    return 200, [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@memory_router.get("/", response=List[VisitorOut])
def list_visitors(request, search: Optional[str] = None):
    from apps.chat.models import ChatSession

    tenant = request.auth.tenant
    qs = ChatSession.objects.filter(tenant_id=tenant.id, is_test=False)
    if search:
        qs = qs.filter(visitor_id__icontains=search)
    visitor_ids = list(qs.values_list("visitor_id", flat=True).distinct())
    return [{"visitor_id": vid} for vid in visitor_ids]


class MemoryOut(Schema):
    id: str
    key: str
    value: str


class MemoryIn(Schema):
    key: str
    value: str


@memory_router.get("/{visitor_id}/sessions/", response=List[VisitorSessionOut])
def list_visitor_sessions(request, visitor_id: str):
    from apps.chat.models import ChatSession

    tenant = request.auth.tenant
    sessions = ChatSession.objects.filter(
        tenant_id=tenant.id, visitor_id=visitor_id, is_test=False
    ).order_by("-created_at")
    return [
        {
            "session_id": str(s.id),
            "visitor_id": s.visitor_id,
            "is_hitl": s.is_hitl,
            "created_at": s.created_at.isoformat(),
        }
        for s in sessions
    ]


@memory_router.get("/{visitor_id}/memory/", response=List[MemoryOut])
def list_memories(request, visitor_id: str):
    tenant = request.auth.tenant
    memories = VisitorMemory.objects.filter(tenant_id=tenant.id, visitor_id=visitor_id)
    return [{"id": str(m.id), "key": m.key, "value": m.value} for m in memories]


@memory_router.patch("/{visitor_id}/memory/{memory_id}", response=MemoryOut)
def update_memory(request, visitor_id: str, memory_id: str, body: MemoryIn):
    tenant = request.auth.tenant
    memory = get_object_or_404(VisitorMemory, id=memory_id, tenant_id=tenant.id, visitor_id=visitor_id)
    memory.key = body.key
    memory.value = body.value
    memory.save()
    return {"id": str(memory.id), "key": memory.key, "value": memory.value}


@memory_router.delete("/{visitor_id}/memory/{memory_id}", response={204: None, 404: DetailOut})
def delete_memory_entry(request, visitor_id: str, memory_id: str):
    tenant = request.auth.tenant
    deleted = delete_memory(str(tenant.id), visitor_id, memory_id)
    if not deleted:
        return 404, {"detail": "Not found"}
    return 204, None
