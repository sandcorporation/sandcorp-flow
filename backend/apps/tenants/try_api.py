"""Try Session 공개 API(#135, ADR-0030) — 인증 없음, 세션 쿠키(``sf_try``)로 자기 체험 테넌트만.

편집기 API(``/api/tenant/flows/…``)와 같은 모양의 얇은 어댑터다: 쿠키 → ``TrySession`` → 체험 테넌트 id를 정한 뒤
흐름 서비스·편집기 응답 조립(``flow_detail_body``·``stream_test_turn``)을 그대로 부른다. 흐름 id가 다른 세션의 것이면
테넌트 범위 검사가 404로 막는다.

- 쓰기(POST·PUT·PATCH)는 Origin이 같은 호스트이거나 ``TRY_ALLOWED_ORIGINS``일 때만(쿠키 SameSite=Strict와 겹쳐 CSRF 방어).
- 시험 대화: 전체 하루 비용이 상한이면 LLM·턴 없이 ``done{code: try_daily_budget}``, 턴을 다 쓰면 ``try_turns_exhausted``.
  돈 턴의 판단 기록 비용을 하루 누적에 더한다.
- 배포는 ``try_restricted``(403).
- 「이 흐름으로 가입」(#42): ``POST /claim/`` — 쿠키 + 고객 멤버 세션 Bearer 둘 다. 쿠키 경로(``/api/try/``) 아래라
  같은 사이트 fetch에 실린다(SameSite=Strict — sandcorp-auth 왕복 뒤 착지한 페이지의 fetch는 같은 사이트 요청이다).
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from ninja import Router, Schema
from pydantic import Field

from apps.flows import services
from apps.flows.api import DraftOut, DraftPatchIn, DraftPutIn, FlowDetailOut, TestTurnIn, _draft_out, flow_detail_body, stream_test_turn
from apps.flows.example_catalog import EXAMPLES, example_graph
from apps.flows.schema import JSON, NODE_CONFIG_MODELS
from apps.tenants import trial, try_claim, try_session, try_tenant
from apps.tenants.auth import active_membership_for_token
from apps.tenants.models import Tenant, TenantConfig, TrySession
from apps.tenants.onboarding import client_ip
from apps.tenants.try_session import TryRefused

try_router = Router(tags=["try"], auth=None)


class TryTemplateOut(Schema):
    id: str
    name: str
    documents: list[str]


class TryTemplateDetailOut(Schema):
    id: str
    name: str
    documents: list[str]
    graph: JSON


class TryStartIn(Schema):
    template: str = Field(min_length=1, max_length=64)
    graph: dict | None = Field(default=None, description="있으면 사본 초안을 이 그래프로 시작한다(PUT draft와 같은 검증). 없으면 템플릿 그대로.")


class TrySessionOut(Schema):
    flow_id: str
    template: str
    expires_at: datetime
    turns_remaining: int


class TryStartOut(TrySessionOut):
    draft: DraftOut | None = None  # ``graph``를 실어 보냈을 때만 — 그 초안의 검증 결과


# ── 쿠키·Origin ──────────────────────────────────────────────────────────────


def _check_origin(request: HttpRequest) -> None:
    origin = request.headers.get("Origin", "")
    if origin and (origin in settings.TRY_ALLOWED_ORIGINS or urlsplit(origin).netloc == request.get_host()):
        return
    raise TryRefused("허용되지 않은 출처의 요청입니다.", code=try_session.BAD_ORIGIN)


def _session(request: HttpRequest) -> TrySession:
    return try_session.resolve(request.COOKIES.get(try_session.COOKIE_NAME))


def _session_out(session: TrySession) -> dict[str, object]:
    tenant: Tenant = session.tenant
    return {
        "flow_id": str(session.flow_id),
        "template": tenant.try_example_id,
        "expires_at": tenant.try_expires_at,
        "turns_remaining": tenant.trial_turns_remaining,
    }


# ── 템플릿·세션 ──────────────────────────────────────────────────────────────


@try_router.get("/templates/", response=list[TryTemplateOut])
def try_templates(request):
    """`/try?template=…` 진입용 예제 목록(인증 없음)."""
    return [{"id": ex.id, "name": ex.name, "documents": [d.name for d in ex.docs]} for ex in EXAMPLES.values()]


@try_router.get("/templates/{template_id}/", response=TryTemplateDetailOut)
def try_template_detail(request, template_id: str):
    """「흐름 보기」 — 세션을 만들지 않고 예제의 흐름 그래프를 읽기 전용으로 준다(IP 한도를 쓰지 않는다)."""
    ex = EXAMPLES.get(template_id)
    if ex is None:
        raise TryRefused("알 수 없는 예제입니다.", code=try_session.UNKNOWN_TEMPLATE)
    return {"id": ex.id, "name": ex.name, "documents": [d.name for d in ex.docs], "graph": example_graph(ex.id)}


@try_router.get("/node-schema/", response=dict[str, dict[str, JSON]])
def try_node_schema(request):
    """편집기 노드 설정 폼의 틀(``/api/tenant/flows/node-schema/``와 같은 것)."""
    return {"kinds": {k.value: {"config": m.model_json_schema()} for k, m in NODE_CONFIG_MODELS.items()}}


@try_router.post("/sessions/", response={201: TryStartOut})
def try_start(request, body: TryStartIn, response: HttpResponse):
    """「고쳐 보기」 — 체험 테넌트(예제 사본)를 만들고 세션 쿠키를 준다. IP당 하루 ``TRY_SESSIONS_PER_IP_PER_DAY``."""
    _check_origin(request)
    started = try_session.start(body.template, client_ip(request), graph=body.graph)
    response.set_cookie(
        try_session.COOKIE_NAME,
        started.raw_token,
        max_age=settings.TRY_TENANT_TTL_HOURS * 3600,
        httponly=True,
        secure=not settings.DEBUG,  # dev(http localhost)에선 False, 운영(https)에선 True
        samesite="Strict",
        path=try_session.COOKIE_PATH,
    )
    return 201, {
        "flow_id": str(started.session.flow_id),
        "template": started.template,
        "expires_at": started.expires_at,
        "turns_remaining": started.turns_remaining,
        "draft": _draft_out(started.draft) if started.draft is not None else None,
    }


@try_router.get("/session/", response=TrySessionOut | None)
def try_session_get(request):
    """내 체험 세션. 쿠키가 없거나 위조·만료면 **200 null** — 「아직 없음」을 묻는 의도된 질의라 오류가 아니다(#163).
    다른 엔드포인트는 같은 상황에 401 ``try_no_session``을 그대로 낸다(진짜 거부)."""
    try:
        return _session_out(_session(request))
    except TryRefused as e:
        if e.code != try_session.NO_SESSION:
            raise
        return None


# ── 사본 읽기·초안 저장(편집기와 같은 모양) ─────────────────────────────────


@try_router.get("/flows/{flow_id}/", response=FlowDetailOut)
def try_flow_get(request, flow_id: str):
    return flow_detail_body(_session(request).tenant_id, flow_id)


@try_router.put("/flows/{flow_id}/draft/", response=DraftOut)
def try_put_draft(request, flow_id: str, body: DraftPutIn):
    session = _session(request)
    _check_origin(request)
    return _draft_out(services.save_draft(session.tenant_id, flow_id, body.graph))


@try_router.patch("/flows/{flow_id}/draft/", response=DraftOut)
def try_patch_draft(request, flow_id: str, body: DraftPatchIn):
    session = _session(request)
    _check_origin(request)
    return _draft_out(services.patch_draft(session.tenant_id, flow_id, list(body.ops)))


@try_router.post("/flows/{flow_id}/deploy/", response={403: dict[str, str]})
def try_deploy(request, flow_id: str):
    """체험은 배포하지 않는다 — 편집기의 배포 버튼이 받을 사유를 그대로 준다."""
    _session(request)
    raise TryRefused("체험 흐름은 배포할 수 없습니다. 가입한 뒤 내 테넌트에서 배포하세요.", code=try_tenant.RESTRICTED)


# ── 「이 흐름으로 가입」 — 내 테넌트로 옮기기(#42) ─────────────────────────


class TryClaimOut(Schema):
    tenant_id: str
    flow_id: str
    flow_name: str


def _bearer_membership(request: HttpRequest):
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return active_membership_for_token(token.strip()) if scheme.lower() == "bearer" and token.strip() else None


@try_router.post("/claim/", response={201: TryClaimOut})
def try_claim_flow(request, response: HttpResponse):
    """체험 사본을 Bearer(고객 멤버 세션)의 테넌트에 새 초안 흐름으로 옮기고 체험 테넌트·세션을 지운다.
    거부: ``try_login_required``(401) · ``try_no_session``(401, 없음·만료·이미 옮김) · ``try_claim_forbidden``(403, Admin 아님)."""
    _check_origin(request)
    claimed = try_claim.claim(request.COOKIES.get(try_session.COOKIE_NAME), _bearer_membership(request))
    response.delete_cookie(try_session.COOKIE_NAME, path=try_session.COOKIE_PATH)
    return 201, {"tenant_id": str(claimed.tenant_id), "flow_id": str(claimed.flow_id), "flow_name": claimed.flow_name}


# ── 시험 대화(SSE) ───────────────────────────────────────────────────────────


def _admit_turn_request(request: HttpRequest, flow_id: str) -> tuple[uuid.UUID, bool]:
    """(체험 테넌트 id, 하루 비용 여유) — 쿠키·Origin·흐름 범위를 확인한다(LLM 전)."""
    session = _session(request)
    _check_origin(request)
    services.get_flow(session.tenant_id, flow_id)
    return session.tenant_id, try_session.budget_open()


def _refusal_code(tenant_id: uuid.UUID) -> tuple[str, str]:
    config = TenantConfig.objects.get(tenant_id=tenant_id)
    if trial.uses_platform_key(config) and not trial.platform_key_ready():
        return try_session.UNAVAILABLE, try_session.UNAVAILABLE_NOTE
    return try_session.TURNS_EXHAUSTED, try_session.TURNS_EXHAUSTED_NOTE


@try_router.post("/flows/{flow_id}/test/")
async def try_test_conversation(request: HttpRequest, flow_id: str, body: TestTurnIn) -> StreamingHttpResponse:
    """초안으로 시험 한 턴(SSE, 편집기와 같은 이벤트). 돌지 않은 사유는 ``done.code``:
    ``try_daily_budget``(전체 하루 비용, 「내일 다시」) · ``try_turns_exhausted``(세션 턴) · ``try_unavailable``(플랫폼 키 없음)."""
    tenant_id, budget_open = await sync_to_async(_admit_turn_request)(request, flow_id)
    if not budget_open:
        refused = services.TestTurnResult(ran=False, note=try_session.DAILY_BUDGET_NOTE)
        return stream_test_turn(flow_id, refused, code=try_session.DAILY_BUDGET)

    begun = await services.begin_test_turn(tenant_id, flow_id, body.message, session_id=body.session_id)
    if isinstance(begun, services.PreparedTestTurn):
        return stream_test_turn(flow_id, begun, after=_add_turn_spend)
    if begun.note == services.TRIAL_EXHAUSTED_NOTE:
        code, note = await sync_to_async(_refusal_code)(tenant_id)
        return stream_test_turn(flow_id, dataclasses.replace(begun, note=note), code=code)
    return stream_test_turn(flow_id, begun)


async def _add_turn_spend(result: services.TestTurnResult) -> None:
    """돈 턴의 비용을 하루 체험 비용에 더한다 — 스트림과 따로 도는 턴 작업이 끝에 부른다(연결이 끊겨도 더한다)."""
    if result.ran:
        await sync_to_async(try_session.add_spend)(try_session.turn_cost(result.trace))
