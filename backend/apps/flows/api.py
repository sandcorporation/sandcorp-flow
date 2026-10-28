"""흐름 HTTP API(#28) — 편집기(#29)가 부르는 ``/api/tenant/flows/*``·``/api/tenant/deploy-requests/*``.

#76 공용 서비스 층(``apps.flows.services``)을 감싸기만 한다 — 편집 규칙·승인 규칙은 여기서 다시 쓰지 않는다.
- 읽기는 모든 멤버, 쓰기는 권한 비트(``flow.edit``·``flow.deploy``·``flow.approve``, ADR-0025). TENANT_KEY는 Admin 등가.
- 서비스 오류(``FlowServiceError``)는 ``config.api``의 예외 처리기가 ``{detail, code, ...}``와 상태 코드로 바꾼다.
- 시험 대화는 SSE(POST + Authorization 헤더 — ``fetch`` 스트림으로 읽는다. EventSource는 POST를 못 한다).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import datetime
from typing import Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.flows import node_events, services
from apps.flows.node_events import NodeEventName
from apps.flows.models import DeployRequest, Flow, FlowVersion
from apps.flows.schema import JSON, NODE_CONFIG_MODELS
from apps.flows.services import PatchOp
from apps.tenants.auth import active_membership_for_token, member_or_tenant_key_auth
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import FLOW_APPROVE, FLOW_DEPLOY, FLOW_EDIT, has_permission, require_permission

logger = logging.getLogger(__name__)

flow_router = Router(tags=["flows"], auth=member_or_tenant_key_auth)
deploy_request_router = Router(tags=["flows"], auth=member_or_tenant_key_auth)

# 서비스 오류 code → HTTP 상태. 기본 400.
_STATUS_BY_CODE = {
    "flow_not_found": 404,
    "version_not_found": 404,
    "request_not_found": 404,
    "session_not_found": 404,
    "duplicate_name": 409,
    "flow_in_use": 409,
    "not_embeddable": 409,
    "request_not_pending": 409,
    "approval_required": 409,
    "self_approval": 403,
    "try_restricted": 403,
    "try_no_session": 401,
    "try_login_required": 401,
    "try_claim_forbidden": 403,
    "try_bad_origin": 403,
    "try_ip_limit": 429,
    "unknown_template": 404,
    "flow_invalid": 422,
    "invalid_patch": 422,
    "invalid_name": 422,
    "note_too_long": 422,
}


def error_body(exc: services.FlowServiceError) -> tuple[int, dict[str, JSON]]:
    body: dict[str, JSON] = {"detail": exc.message, "code": exc.code}
    if isinstance(exc, services.FlowInvalid):
        body["errors"] = [dict(e.as_dict()) for e in exc.errors]
    if isinstance(exc, services.PatchError):
        body["index"] = exc.index
    return _STATUS_BY_CODE.get(exc.code, 400), body


# ── 스키마 ───────────────────────────────────────────────────────────────────


class FlowErrorOut(Schema):
    node_id: str | None = None
    code: str
    message: str


class DraftOut(Schema):
    graph: dict
    errors: list[FlowErrorOut]
    valid: bool


class FlowOut(Schema):
    id: str
    name: str
    is_public: bool
    """「기본 흐름」(위젯 ``/chatbot/<slug>/``가 도는 흐름). 이름은 E1이 정한 대로 둔다 — ``is_default`` 별칭은 두지 않는다(ADR-0034)."""
    embed_key: str
    embed_enabled: bool
    embed_path: str | None = None
    """키 주소 ``/chatbot/<slug>/<embed_key>/``(origin 없이). 슬러그가 없는 테넌트는 None."""
    deployed_version: int | None
    updated_at: datetime
    has_pending_request: bool = False


class EmbedIn(Schema):
    enabled: bool


class VersionOut(Schema):
    number: int
    note: str
    hash: str
    created_at: datetime
    created_by_name: str | None = None
    deployed: bool


class VersionDetailOut(VersionOut):
    graph: dict


class DeployRequestOut(Schema):
    id: str
    flow_id: str
    flow_name: str
    version: int
    status: Literal["pending", "approved", "rejected", "superseded"]
    requested_by_name: str | None = None
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    decision_note: str
    created_at: datetime


class FlowDetailOut(Schema):
    flow: FlowOut
    draft: DraftOut
    versions: list[VersionOut]
    pending_request: DeployRequestOut | None = None


class SaveVersionOut(Schema):
    version: VersionOut
    created: bool


class DeployOut(Schema):
    deployed: bool
    flow: FlowOut
    request: DeployRequestOut | None = None


class FlowCreateIn(Schema):
    name: str = Field(max_length=255)
    graph: dict | None = None


class FlowRenameIn(Schema):
    name: str = Field(max_length=255)


class DraftPutIn(Schema):
    graph: dict


class DraftPatchIn(Schema):
    ops: list[PatchOp] = Field(min_length=1)


class SaveVersionIn(Schema):
    note: str | None = None


class DeployIn(Schema):
    version: int = Field(ge=1)


class DecisionIn(Schema):
    note: str | None = None


class TestTurnIn(Schema):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


# ── 변환 ─────────────────────────────────────────────────────────────────────


def _tenant_id(subject: Membership | Tenant) -> uuid.UUID:
    return subject.tenant_id if isinstance(subject, Membership) else subject.id


def _user_id(subject: Membership | Tenant) -> uuid.UUID | None:
    return subject.user_id if isinstance(subject, Membership) else None  # TENANT_KEY는 사용자 없음


def _flow_out(flow: Flow, pending: bool = False) -> dict[str, object]:
    deployed = flow.deployed_version
    return {
        "id": str(flow.id),
        "name": flow.name,
        "is_public": flow.is_public,
        "embed_key": flow.embed_key,
        "embed_enabled": flow.embed_enabled,
        "embed_path": f"/chatbot/{slug}/{flow.embed_key}/" if (slug := flow.tenant.slug) else None,
        "deployed_version": deployed.number if deployed else None,
        "updated_at": flow.updated_at,
        "has_pending_request": pending,
    }


def _draft_out(result: services.DraftResult) -> dict[str, object]:
    return {"graph": result.graph, "errors": [e.as_dict() for e in result.errors], "valid": result.valid}


SEED_CREATOR = "시스템(기본 흐름)"  # 만든 이 없는 v1은 기본 흐름 시드(온보딩·마이그레이션)가 만든 것


def _version_out(version: FlowVersion, deployed_id: uuid.UUID | None) -> dict[str, object]:
    creator = version.created_by
    return {
        "number": version.number,
        "note": version.note,
        "hash": version.hash,
        "created_at": version.created_at,
        "created_by_name": (creator.name or creator.email) if creator else (SEED_CREATOR if version.number == 1 else None),
        "deployed": version.pk == deployed_id,
    }


def _request_out(req: DeployRequest) -> dict[str, object]:
    return {
        "id": str(req.id),
        "flow_id": str(req.flow_id),
        "flow_name": req.flow.name,
        "version": req.version.number,
        "status": req.status,
        "requested_by_name": (req.requested_by.name or req.requested_by.email) if req.requested_by else None,
        "decided_by_name": (req.decided_by.name or req.decided_by.email) if req.decided_by else None,
        "decided_at": req.decided_at,
        "decision_note": req.decision_note,
        "created_at": req.created_at,
    }


def _pending_for(tenant_id: uuid.UUID, flow_id: uuid.UUID) -> DeployRequest | None:
    return (
        DeployRequest.objects.for_tenant(tenant_id)
        .select_related("flow", "version", "requested_by", "decided_by")
        .filter(flow_id=flow_id, status=DeployRequest.PENDING)
        .first()
    )


def _deploy_request(tenant_id: uuid.UUID, request_id: uuid.UUID) -> DeployRequest:
    return (
        DeployRequest.objects.select_related("flow", "version", "requested_by", "decided_by")
        .get(pk=request_id, flow__tenant_id=tenant_id)
    )


# ── 흐름 ─────────────────────────────────────────────────────────────────────


@flow_router.get("/", response=list[FlowOut])
def flows_list(request):
    tenant_id = _tenant_id(request.auth)
    pending = set(
        DeployRequest.objects.for_tenant(tenant_id).filter(status=DeployRequest.PENDING).values_list("flow_id", flat=True)
    )
    return [_flow_out(f, f.pk in pending) for f in services.list_flows(tenant_id)]


@flow_router.post("/", response={201: FlowOut})
def flows_create(request, body: FlowCreateIn):
    require_permission(request.auth, FLOW_EDIT)
    return 201, _flow_out(services.create_flow(_tenant_id(request.auth), body.name, body.graph))


@flow_router.get("/node-schema/", response=dict[str, dict[str, JSON]])
def flows_node_schema(request):
    """편집기 노드 설정 폼의 틀 — 종류별 config 의 JSON 스키마(필드·enum·필수·기본값). get_flow_schema 와 같은 모델에서 뽑는다."""
    return {"kinds": {k.value: {"config": m.model_json_schema()} for k, m in NODE_CONFIG_MODELS.items()}}


@flow_router.get("/{flow_id}/", response=FlowDetailOut)
def flows_get(request, flow_id: str):
    return flow_detail_body(_tenant_id(request.auth), flow_id)


def flow_detail_body(tenant_id: uuid.UUID, flow_id: str) -> dict[str, object]:
    """``FlowDetailOut`` 본문 — 편집기와 체험(``apps.tenants.try_api``)이 같이 쓴다."""
    detail = services.flow_detail(tenant_id, flow_id)
    deployed_id = detail.flow.deployed_version_id
    versions = FlowVersion.objects.for_tenant(tenant_id).filter(flow=detail.flow).select_related("created_by").order_by("number")
    pending = _pending_for(tenant_id, detail.flow.pk)
    return {
        "flow": _flow_out(detail.flow, pending is not None),
        "draft": _draft_out(detail.draft),
        "versions": [_version_out(v, deployed_id) for v in versions],
        "pending_request": _request_out(pending) if pending else None,
    }


@flow_router.patch("/{flow_id}/", response=FlowOut)
def flows_rename(request, flow_id: str, body: FlowRenameIn):
    require_permission(request.auth, FLOW_EDIT)
    return _flow_out(services.rename_flow(_tenant_id(request.auth), flow_id, body.name))


@flow_router.delete("/{flow_id}/", response={204: None})
def flows_archive(request, flow_id: str):
    """흐름 보관(soft delete). 위젯이 쓰는 공개 흐름은 409 — 버전·세션·판단 기록은 남는다."""
    require_permission(request.auth, FLOW_EDIT)
    services.archive_flow(_tenant_id(request.auth), flow_id)
    return 204, None


@flow_router.post("/{flow_id}/restore/", response=FlowOut)
def flows_restore(request, flow_id: str):
    """보관한 흐름을 되돌린다(화면 없음 — API만)."""
    require_permission(request.auth, FLOW_EDIT)
    return _flow_out(services.restore_flow(_tenant_id(request.auth), flow_id))


@flow_router.post("/{flow_id}/embed/", response=FlowOut)
def flows_set_embed(request, flow_id: str, body: EmbedIn):
    """키 주소 임베드 켬/끔. 배포 버전이 없으면 켤 수 없다(409 ``not_embeddable``, 이유는 ``detail``)."""
    require_permission(request.auth, FLOW_DEPLOY)
    return _flow_out(services.set_embed_enabled(_tenant_id(request.auth), flow_id, body.enabled))


@flow_router.post("/{flow_id}/default/", response=FlowOut)
def flows_set_default(request, flow_id: str):
    """기본 흐름 바꾸기 — 위젯 ``/chatbot/<slug>/``가 이 흐름으로 돈다. 배포됨·보관 아님만(409 ``not_embeddable``)."""
    require_permission(request.auth, FLOW_DEPLOY)
    return _flow_out(services.set_default_flow(_tenant_id(request.auth), flow_id))


# ── 초안 ─────────────────────────────────────────────────────────────────────


@flow_router.put("/{flow_id}/draft/", response=DraftOut)
def flows_put_draft(request, flow_id: str, body: DraftPutIn):
    """편집기 통째 저장. 틀려도 저장하고 검증 결과를 돌려준다."""
    require_permission(request.auth, FLOW_EDIT)
    return _draft_out(services.save_draft(_tenant_id(request.auth), flow_id, body.graph))


@flow_router.patch("/{flow_id}/draft/", response=DraftOut)
def flows_patch_draft(request, flow_id: str, body: DraftPatchIn):
    """노드/선 패치(MCP update_flow와 같은 연산 목록). 하나라도 적용 못 하면 422, 아무것도 저장되지 않는다."""
    require_permission(request.auth, FLOW_EDIT)
    return _draft_out(services.patch_draft(_tenant_id(request.auth), flow_id, list(body.ops)))


# ── 버전·배포·되돌리기 ───────────────────────────────────────────────────────


@flow_router.get("/{flow_id}/versions/", response=list[VersionOut])
def flows_list_versions(request, flow_id: str):
    tenant_id = _tenant_id(request.auth)
    flow = services.get_flow(tenant_id, flow_id)
    versions = FlowVersion.objects.for_tenant(tenant_id).filter(flow=flow).select_related("created_by").order_by("number")
    return [_version_out(v, flow.deployed_version_id) for v in versions]


@flow_router.get("/{flow_id}/versions/{number}/", response=VersionDetailOut)
def flows_get_version(request, flow_id: str, number: int):
    tenant_id = _tenant_id(request.auth)
    flow = services.get_flow(tenant_id, flow_id)
    version = services.get_version(tenant_id, flow_id, number)
    return {**_version_out(version, flow.deployed_version_id), "graph": version.graph}


@flow_router.post("/{flow_id}/versions/", response={200: SaveVersionOut, 201: SaveVersionOut})
def flows_save_version(request, flow_id: str, body: SaveVersionIn):
    """초안을 새 버전으로 굳힌다(note는 만들 때만). 마지막 버전과 같으면 200 created=false."""
    require_permission(request.auth, FLOW_EDIT)
    tenant_id = _tenant_id(request.auth)
    result = services.save_version(tenant_id, flow_id, created_by_id=_user_id(request.auth), note=body.note)
    flow = services.get_flow(tenant_id, flow_id)
    payload = {"version": _version_out(result.version, flow.deployed_version_id), "created": result.created}
    return (201 if result.created else 200), payload


@flow_router.post("/{flow_id}/deploy/", response={200: DeployOut, 202: DeployOut})
def flows_deploy(request, flow_id: str, body: DeployIn):
    """배포. 테넌트가 승인을 요구하면 배포하지 않고 배포 요청을 만들어 202로 돌려준다."""
    require_permission(request.auth, FLOW_DEPLOY)
    outcome = services.submit_deploy(_tenant_id(request.auth), flow_id, body.version, requested_by_id=_user_id(request.auth))
    tenant_id = _tenant_id(request.auth)
    payload = {
        "deployed": outcome.deployed,
        "flow": _flow_out(outcome.flow, outcome.request is not None),
        "request": _request_out(_deploy_request(tenant_id, outcome.request.pk)) if outcome.request else None,
    }
    return (200 if outcome.deployed else 202), payload


@flow_router.post("/{flow_id}/rollback/", response=FlowOut)
def flows_rollback(request, flow_id: str):
    """배포 중 버전의 바로 앞 버전을 다시 배포한다(긴급 복구라 승인 규칙 제외)."""
    require_permission(request.auth, FLOW_DEPLOY)
    return _flow_out(services.rollback(_tenant_id(request.auth), flow_id))


# ── 배포 요청 ────────────────────────────────────────────────────────────────


@deploy_request_router.get("/", response=list[DeployRequestOut])
def deploy_requests_list(request, status: Literal["pending", "approved", "rejected", "superseded"] | None = None):
    return deploy_request_rows(_tenant_id(request.auth), status)


def deploy_request_rows(tenant_id: uuid.UUID, status: str | None = None) -> list[dict[str, object]]:
    """테넌트의 배포 요청(최신순). 배포 요청 화면 API와 MCP `list_deploy_requests`가 같이 쓴다."""
    ids = [r.pk for r in services.list_deploy_requests(tenant_id, status=status)]
    reqs = DeployRequest.objects.select_related("flow", "version", "requested_by", "decided_by").filter(pk__in=ids)
    return [_request_out(r) for r in reqs.order_by("-created_at")]


def _decide(request, request_id: str, body: DecisionIn, *, approve: bool) -> dict[str, object]:
    require_permission(request.auth, FLOW_APPROVE)
    tenant_id = _tenant_id(request.auth)
    decide = services.approve_deploy_request if approve else services.reject_deploy_request
    done = decide(tenant_id, request_id, decided_by_id=_user_id(request.auth), note=body.note)
    return _request_out(_deploy_request(tenant_id, done.pk))


@deploy_request_router.post("/{request_id}/approve/", response=DeployRequestOut)
def deploy_requests_approve(request, request_id: str, body: DecisionIn):
    """승인하면 그 버전이 배포된다. 요청자 본인은 403(다른 Admin 또는 TENANT_KEY)."""
    return _decide(request, request_id, body, approve=True)


@deploy_request_router.post("/{request_id}/reject/", response=DeployRequestOut)
def deploy_requests_reject(request, request_id: str, body: DecisionIn):
    return _decide(request, request_id, body, approve=False)


# ── 시험 대화(SSE) ───────────────────────────────────────────────────────────


HEARTBEAT = ": heartbeat\n\n"
"""SSE 주석 줄 — 소비자는 ``data:`` 없는 블록을 건너뛴다(#253)."""


def _sse(event: str, data: Mapping[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _authenticate(request: HttpRequest) -> Membership | Tenant | None:
    """비동기 뷰라 동기 인증기를 직접 쓰지 않는다(DB 접근) — 같은 규칙을 ``sync_to_async``로."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    def lookup() -> Membership | Tenant | None:
        return active_membership_for_token(token) or Tenant.verify_key(token)

    return await sync_to_async(lookup)()


@flow_router.post("/{flow_id}/test/", auth=None)
async def flows_test_conversation(request: HttpRequest, flow_id: str, body: TestTurnIn):
    """초안으로 시험 한 턴을 돌려 SSE로 흘려보낸다. 실제 사람 넘김·알림은 없고 세션은 ``is_test``다.

    이벤트: ``start``{flow_id} → (``node_start`` → ``node_end``|``node_error``)×N(턴 도중, 실시간) → ``trace``{노드 항목}×N
    → ``reply``{text} → ``done``{ran, handoff, note, code, errors, session_id}. 노드 이벤트 필드는 ``stream_test_turn`` 참조.
    초안이 무효이면 ``done``(ran=false, errors)만 온다. 노드가 예외를 내면 ``node_error`` 뒤 ``done``(ran=false,
    code=``turn_error``). 판단 기록 전체는 ``session_id``로 ``GET /api/tenant/sessions/{id}/decision-traces``에서 다시
    읽는다. 체험 턴을 1 쓴다(ADR-0026).
    """
    subject = await _authenticate(request)
    if subject is None:
        return HttpResponse(json.dumps({"detail": "Unauthorized"}), status=401, content_type="application/json")
    require_permission(subject, FLOW_EDIT)
    begun = await services.begin_test_turn(_tenant_id(subject), flow_id, body.message, session_id=body.session_id)
    return stream_test_turn(flow_id, begun)


TURN_ERROR = "turn_error"
"""``done.code`` — 턴이 노드 예외 등으로 멈췄다(``node_error``가 먼저 온다)."""
TURN_ERROR_NOTE = "시험 한 턴이 오류로 멈췄습니다. 오류 난 노드를 확인하세요."

_running_turns: set[asyncio.Task[services.TestTurnResult]] = set()
"""스트림과 따로 도는 시험 턴 작업 — 연결이 끊겨 스트림이 닫혀도 끝까지 돌도록 강한 참조를 쥔다."""

_TurnQueue = asyncio.Queue[tuple[NodeEventName, dict[str, JSON]] | None]


async def _run_turn(
    prepared: services.PreparedTestTurn,
    queue: _TurnQueue,
    after: Callable[[services.TestTurnResult], Awaitable[None]] | None,
) -> services.TestTurnResult:
    """시험 턴을 끝까지 돌리며 노드 이벤트를 큐에 넣는다. 끝나면(성공·예외 모두) ``None``을 넣어 스트림에 알린다."""
    try:
        result = await services.finish_test_turn(prepared, on_node_event=lambda name, data: queue.put_nowait((name, data)))
        if after is not None:
            try:
                await after(result)
            except Exception:  # noqa: BLE001 — 뒤처리(체험 비용 적립) 실패가 이미 돈 턴의 결과를 지우지 않게
                logger.exception("시험 턴 뒤처리 실패 session=%s", prepared.session.id)
        return result
    except Exception:
        logger.exception("시험 턴 실패 session=%s", prepared.session.id)
        raise
    finally:
        queue.put_nowait(None)


def _forget_turn(task: asyncio.Task[services.TestTurnResult]) -> None:
    _running_turns.discard(task)
    if not task.cancelled():
        task.exception()  # 이미 로그로 남겼다 — 스트림이 먼저 닫혀 아무도 결과를 읽지 않아도 경고가 나지 않게


def stream_test_turn(
    flow_id: str,
    turn: services.TestTurnResult | services.PreparedTestTurn,
    *,
    code: str | None = None,
    after: Callable[[services.TestTurnResult], Awaitable[None]] | None = None,
) -> StreamingHttpResponse:
    """시험 한 턴을 SSE로 — 편집기와 체험(``apps.tenants.try_api``)이 같이 쓴다. ``code``는 돌지 않은 사유(체험 한도).

    ``turn``이 돌지 않은 결과면 ``start`` → ``done``. 준비된 턴(``PreparedTestTurn``)이면 턴을 스트림과 따로 도는
    작업으로 띄우고, 실행기 노드 이벤트를 큐에서 꺼내 그때그때 보낸다(#239, ADR-0035):

    - ``node_start`` {node_id, kind, step} — handler 직전. ``step``은 턴 안 0부터의 순번(= ``trace`` 순번).
    - ``node_end`` {node_id, kind, step, handle, duration_ms, input_tokens, output_tokens, cost_usd, unpriced_models,
      summary} — trace 항목을 만든 직후. ``summary``는 종류별 한 줄(``node_events.summary``, 시작 노드는 null).
    - ``node_error`` {node_id, kind, step, message} — handler 예외. 뒤에 ``done``(ran=false, code=``turn_error``).

    노드 이벤트 문자열은 판단 기록과 같은 비밀 가림·자름을 거친다. 연결이 끊겨도 작업은 끝까지 돌아 판단 기록이 남고
    ``after``(체험 비용 적립 같은 뒤처리)도 돈다.
    """

    def done(result: services.TestTurnResult, code: str | None) -> str:
        return _sse(
            "done",
            {
                "ran": result.ran,
                "handoff": result.handoff,
                "note": result.note,
                "code": code,
                "errors": [e.as_dict() for e in result.errors],
                "session_id": str(result.session_id) if result.session_id else None,
            },
        )

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"flow_id": flow_id})
        if isinstance(turn, services.TestTurnResult):
            result = turn
        else:
            # 가림 함수는 턴을 띄우기 전에 한 번 만든다 — 이벤트마다 sync 스레드로 건너가면 그 스레드를 쥔 턴(지식 검색)
            # 뒤에 줄 서서 node_start가 노드가 끝날 때 함께 나갔다(#256, ADR-0035 덧붙임).
            clean = await sync_to_async(node_events.cleaner)(turn.tenant_id)
            queue: _TurnQueue = asyncio.Queue()
            task = asyncio.create_task(_run_turn(turn, queue, after))
            _running_turns.add(task)
            task.add_done_callback(_forget_turn)
            interval = settings.TEST_SSE_HEARTBEAT_SECONDS
            while True:
                try:
                    # 하트비트는 이벤트 루프에서만 — sync_to_async를 끼우면 지식 검색의 DB 쿼리 뒤에 줄 선다(#256, #253)
                    item = await asyncio.wait_for(queue.get(), interval)
                except asyncio.TimeoutError:
                    yield HEARTBEAT
                    continue
                if item is None:
                    break
                name, data = item
                yield _sse(name, clean(name, data))
            try:
                result = await task
            except Exception:  # noqa: BLE001 — _run_turn이 로그로 남겼다. 화면에는 node_error 뒤 done으로 알린다
                failed = services.TestTurnResult(ran=False, session_id=turn.session.id, note=TURN_ERROR_NOTE)
                yield done(failed, TURN_ERROR)
                return
        for entry in result.trace:
            yield _sse("trace", dict(entry))
        if result.ran:
            yield _sse("reply", {"text": result.reply})
        yield done(result, code)

    response = StreamingHttpResponse(events(), content_type="text/event-stream")  # pyright: ignore[reportArgumentType]
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
