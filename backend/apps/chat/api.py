from typing import TYPE_CHECKING, Any
from ninja import Router, Schema
from django.http import StreamingHttpResponse
from apps.chat.models import ChatSession, ChatMessage
from apps.tenants.auth import tenant_key_auth

if TYPE_CHECKING:
    from apps.flows.models import Flow
    from apps.tenants.models import Tenant

chat_router = Router(tags=["chat"])


class MessageIn(Schema):
    session_id: str
    content: str


class IdentityIn(Schema):
    visitor_id: str


@chat_router.post("/identity", auth=tenant_key_auth)
def issue_identity_hash(request, body: IdentityIn):
    """TENANT_KEY 인증으로 visitor_id의 검증 해시를 발급한다(유저당 1회, 캐시 가능)."""
    from apps.chat.identity import compute_identity_hash

    tenant = request.auth
    return {
        "visitor_id": body.visitor_id,
        "hash": compute_identity_hash(str(tenant.id), body.visitor_id),
    }


def _widget_flow(slug: str, flow: str) -> "tuple[Tenant, Flow | None] | None":
    """위젯 주소를 (테넌트, 흐름)으로 푼다(sync). ``flow``(embed_key)가 없으면 기본 흐름(없을 수 있다).

    slug가 없거나, 키가 없거나·임베드 꺼짐·보관·미배포·다른 테넌트 키면 모두 None — 부르는 쪽은 같은 404를 낸다(ADR-0034).
    """
    from apps.flows.models import Flow
    from apps.tenants.models import Tenant

    tenant = Tenant.resolve_slug(slug)
    if not tenant:
        return None
    if not flow:
        return tenant, Flow.default_for(tenant.id)
    keyed = Flow.open_by_key(tenant.id, flow)
    return (tenant, keyed) if keyed else None


@chat_router.get("/stream")
async def stream(request, slug: str, visitor_id: str = "", hash: str = "", flow: str = ""):
    from asgiref.sync import sync_to_async
    from apps.chat.sse import asse_event_stream

    resolved = await sync_to_async(_widget_flow)(slug, flow)
    if not resolved:
        return StreamingHttpResponse(status=404)
    tenant, chat_flow = resolved
    if not visitor_id:
        return StreamingHttpResponse(status=400)

    # 신원검증 토글이 켜진 Tenant는 유효한 HMAC 해시가 있어야 visitor_id 위조를 막는다.
    config = await sync_to_async(lambda: getattr(tenant, "config", None))()
    if config and config.require_identity_verification:
        from apps.chat.identity import verify_identity
        if not verify_identity(str(tenant.id), visitor_id, hash):
            return StreamingHttpResponse(status=401)

    # 세션 재사용 키는 (tenant, visitor, flow) — 같은 방문자라도 흐름마다 대화가 따로다(ADR-0034).
    session, _ = await ChatSession.objects.aget_or_create(
        tenant_id=tenant.id,
        visitor_id=visitor_id,
        flow=chat_flow,
        ended_at=None,
        is_test=False,
        defaults={"opened_by_key": bool(flow)},
    )

    existing = await sync_to_async(
        lambda: [{"role": m.role, "content": m.content}
                 for m in ChatMessage.objects.filter(session=session).order_by("created_at")]
    )()
    stream_kwargs: dict[str, Any]
    if existing:
        stream_kwargs = {"history": existing, "is_hitl": session.is_hitl}
    else:
        stream_kwargs = {"welcome_message": config.welcome_message if config else ""}
    if config and config.brand_name:
        stream_kwargs["brand_name"] = config.brand_name
    # presence: 이 SSE 연결을 어드민 콘솔 활성 계층에 반영(issue 138).
    stream_kwargs["tenant_id"] = str(tenant.id)

    response = StreamingHttpResponse(
        asse_event_stream(str(session.id), **stream_kwargs),  # pyright: ignore[reportArgumentType]
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["X-Session-Id"] = str(session.id)
    return response


@chat_router.get("/resolve", response={200: dict, 404: dict}, auth=None)
def resolve_widget(request, slug: str, flow: str = ""):
    """공개 위젯 진입 가드 — 위젯 주소가 열려 있으면 200(+공개 브랜드·흐름 이름), 아니면 404.

    위젯이 렌더 전에 호출해, 유효하지 않은 주소면 챗 셸 대신 안내를 띄운다(EventSource는 HTTP
    상태를 못 읽어 404와 일시 오류를 구분 못 하므로, 사전 검증이 필요하다). ``flow``는 키 주소의 embed_key다.
    없는 slug·닫힌 키는 모두 같은 404 몸통이다.
    """
    resolved = _widget_flow(slug, flow)
    if not resolved:
        return 404, {"detail": "Not found"}
    tenant, chat_flow = resolved
    config = getattr(tenant, "config", None)
    return 200, {"brand_name": (config.brand_name if config else ""), "flow_name": chat_flow.name if chat_flow else ""}


def _flow_closed(session: ChatSession) -> bool:
    """키 주소로 연 세션의 흐름이 닫혔는가(임베드 꺼짐·보관). 기본 흐름 주소로 연 세션은 막지 않는다."""
    from apps.flows.models import Flow

    if not session.opened_by_key or session.flow_id is None:
        return False
    flow = Flow.objects.filter(pk=session.flow_id).first()
    return flow is None or not flow.embeddable


@chat_router.post("/message", response={202: dict, 403: dict, 404: dict, 429: dict})
async def send_message(request, body: MessageIn):
    from django.conf import settings
    from asgiref.sync import sync_to_async
    from apps.chat.rate_limit import allow_message

    try:
        session = await ChatSession.objects.aget(id=body.session_id, ended_at=None)
    except ChatSession.DoesNotExist:
        return 404, {"detail": "Session not found"}
    if await sync_to_async(_flow_closed)(session):
        return 403, {"detail": "Flow not open"}  # 위젯은 「지금 열려 있지 않은 상담입니다」를 보이고 입력을 막는다

    if not await sync_to_async(allow_message)(
        str(session.tenant_id),
        session.visitor_id,
        per_visitor=settings.CHAT_RATE_LIMIT_PER_VISITOR,
        per_tenant=settings.CHAT_RATE_LIMIT_PER_TENANT,
    ):
        return 429, {"detail": "Rate limit exceeded"}

    await ChatMessage.objects.acreate(
        session=session,
        role=ChatMessage.ROLE_USER,
        content=body.content,
    )

    if session.is_hitl:
        from apps.chat.sse import publish_visitor_message
        await sync_to_async(publish_visitor_message)(str(session.tenant_id), str(session.id), body.content)
    else:
        from apps.chat.chat_task import dispatch_chat
        await dispatch_chat(str(session.id), body.content)  # taskiq 워커로 1턴 enqueue(issue 194/195)

    return 202, {"status": "processing"}
