"""흐름 HTTP API(#28) — 편집기(#29)가 부르는 ``/api/tenant/flows/*``·``/api/tenant/deploy-requests/*``.

#76 공용 서비스 층(``apps.flows.services``)을 감싸기만 한다 — 편집 규칙·승인 규칙은 여기서 다시 쓰지 않는다.
- 읽기는 모든 멤버, 쓰기는 권한 비트(``flow.edit``·``flow.deploy``·``flow.approve``, ADR-0025). TENANT_KEY는 Admin 등가.
- 서비스 오류(``FlowServiceError``)는 ``config.api``의 예외 처리기가 ``{detail, code, ...}``와 상태 코드로 바꾼다.
- 시험 대화는 SSE(POST + Authorization 헤더 — ``fetch`` 스트림으로 읽는다. EventSource는 POST를 못 한다).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.flows import services
from apps.flows.models import DeployRequest, Flow, FlowVersion
from apps.flows.schema import JSON, NODE_CONFIG_MODELS
from apps.flows.services import PatchOp
from apps.tenants.auth import active_membership_for_token, member_or_tenant_key_auth
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import FLOW_APPROVE, FLOW_DEPLOY, FLOW_EDIT, has_permission, require_permission

flow_router = Router(tags=["flows"], auth=member_or_tenant_key_auth)
deploy_request_router = Router(tags=["flows"], auth=member_or_tenant_key_auth)

# 서비스 오류 code → HTTP 상태. 기본 400.
_STATUS_BY_CODE = {
    "flow_not_found": 404,
    "version_not_found": 404,
    "request_not_found": 404,
    "session_not_found": 404,
    "duplicate_name": 409,
    "request_not_pending": 409,
    "approval_required": 409,
    "self_approval": 403,
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
    deployed_version: int | None
    updated_at: datetime
    has_pending_request: bool = False


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
        "deployed_version": deployed.number if deployed else None,
        "updated_at": flow.updated_at,
        "has_pending_request": pending,
    }


def _draft_out(result: services.DraftResult) -> dict[str, object]:
    return {"graph": result.graph, "errors": [e.as_dict() for e in result.errors], "valid": result.valid}


def _version_out(version: FlowVersion, deployed_id: uuid.UUID | None) -> dict[str, object]:
    creator = version.created_by
    return {
        "number": version.number,
        "note": version.note,
        "hash": version.hash,
        "created_at": version.created_at,
        "created_by_name": (creator.name or creator.email) if creator else None,
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
    tenant_id = _tenant_id(request.auth)
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
    tenant_id = _tenant_id(request.auth)
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


def _sse(event: str, data: dict[str, object]) -> str:
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

    이벤트: ``start``{flow_id} → ``trace``{노드 항목}×N → ``reply``{text} → ``done``{ran, handoff, note, errors, session_id}.
    초안이 무효이면 ``trace``·``reply`` 없이 ``done``(ran=false, errors)만 온다. 판단 기록 전체는 ``session_id``로
    ``GET /api/tenant/sessions/{id}/decision-traces``에서 다시 읽는다. 체험 턴을 1 쓴다(ADR-0026).
    """
    subject = await _authenticate(request)
    if subject is None:
        return HttpResponse(json.dumps({"detail": "Unauthorized"}), status=401, content_type="application/json")
    require_permission(subject, FLOW_EDIT)
    result = await services.run_test_turn(_tenant_id(subject), flow_id, body.message, session_id=body.session_id)

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"flow_id": flow_id})
        for entry in result.trace:
            yield _sse("trace", dict(entry))
        if result.ran:
            yield _sse("reply", {"text": result.reply})
        yield _sse(
            "done",
            {
                "ran": result.ran,
                "handoff": result.handoff,
                "note": result.note,
                "errors": [e.as_dict() for e in result.errors],
                "session_id": str(result.session_id) if result.session_id else None,
            },
        )

    response = StreamingHttpResponse(events(), content_type="text/event-stream")  # pyright: ignore[reportArgumentType]
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
