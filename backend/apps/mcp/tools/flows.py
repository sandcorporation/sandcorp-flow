"""흐름 도구(#76, 스펙 #74 결정 4·6·7) — ``apps/flows/services.py``를 얇게 감싼다. 편집 규칙은 서비스 층에만 둔다.

- 읽기: ``list_flows``·``get_flow``(``flow.read``). 쓰기: ``create_flow``·``update_flow``·``save_version``·
  ``deploy_version``·``test_turn``·``archive_flow``(``flow.write`` + Admin — 디스패처가 판정).
- 수정은 ``update_flow`` 하나가 연산 목록(upsert_node·delete_node·connect·disconnect)을 받아 **원자적으로** 적용한다.
  도구를 연산마다 나누지 않은 까닭: 노드를 만들고 선을 잇는 일은 거의 항상 함께라 한 번 호출로 끝나고, 중간에 실패해도
  흐름이 반쯤 바뀐 채로 남지 않는다. 검증은 묶음 끝에 한 번(중간 상태의 오류를 보지 않는다).
- 검증 실패는 도구 오류가 아니라 결과의 ``errors``(``{node_id, code, message}``) — 에이전트가 읽고 고친다.
  도구 오류(``isError``)는 고칠 대상이 흐름이 아닐 때만: 없는 흐름·버전, 적용할 수 없는 연산, 권한.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from asgiref.sync import sync_to_async
from pydantic import Field

from apps.flows import services
from apps.flows.models import Flow, FlowVersion
from apps.flows.schema import JSON, FlowError
from apps.flows.services import PatchOp
from apps.mcp.access import TenantArgs
from apps.tenants.permissions import FLOW_DEPLOY, FLOW_EDIT
from apps.mcp.registry import JSONObject, ToolContext, ToolError, register


class FlowArgs(TenantArgs):
    flow_id: str = Field(description="흐름 id(list_flows로 확인)")


class SaveVersionArgs(FlowArgs):
    note: str | None = Field(default=None, description="버전 메모(선택, 200자 이내). 새 버전을 만들 때만 붙고 나중에 못 바꾼다.")


class GetFlowArgs(FlowArgs):
    version: int | None = Field(default=None, description="버전 번호. 주면 초안 대신 그 버전의 JSON을 돌려준다.")


class CreateFlowArgs(TenantArgs):
    name: str = Field(min_length=1, max_length=255, description="흐름 이름(테넌트 안에서 유일)")


class UpdateFlowArgs(FlowArgs):
    ops: list[PatchOp] = Field(min_length=1, description="차례로 적용할 연산들. 하나라도 적용 못 하면 아무것도 안 바뀐다.")


class DeployArgs(FlowArgs):
    version: int = Field(ge=1, description="배포할 버전 번호(되돌리기는 이전 번호)")


class TestTurnArgs(FlowArgs):
    message: str = Field(min_length=1, max_length=4000, description="방문자 메시지")
    session_id: str | None = Field(default=None, description="이어 갈 시험 세션 id(앞 test_turn 결과). 없으면 새 세션.")


def _tenant_id(ctx: ToolContext) -> uuid.UUID:
    return ctx.require_membership().tenant.id


async def _service[T](fn: Callable[[], T]) -> T:
    """서비스 호출 — 고칠 대상이 흐름이 아닌 실패는 도구 오류로."""
    try:
        return await sync_to_async(fn)()
    except services.FlowInvalid:
        raise
    except services.FlowServiceError as exc:
        raise ToolError(exc.code, exc.message) from exc


def _errors(errors: list[FlowError]) -> list[JSON]:
    return [dict(e.as_dict()) for e in errors]


def _flow_row(flow: Flow) -> JSONObject:
    deployed = flow.deployed_version
    return {
        "id": str(flow.id),
        "name": flow.name,
        "is_public": flow.is_public,
        "deployed_version": deployed.number if deployed else None,
        "updated_at": flow.updated_at.isoformat(),
    }


def _version_row(version: FlowVersion, deployed_id: uuid.UUID | None) -> JSONObject:
    return {
        "number": version.number,
        "deployed": version.pk == deployed_id,
        "hash": version.hash[:12],
        "note": version.note,
        "created_at": version.created_at.isoformat(),
    }


@register(
    "list_flows",
    "테넌트의 흐름 목록: id·이름·공개 여부(위젯이 도는 흐름)·배포 중 버전 번호. 인자: tenant.",
    TenantArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def list_flows(ctx: ToolContext, args: TenantArgs) -> JSONObject:
    flows = await _service(lambda: services.list_flows(_tenant_id(ctx)))
    return {"flows": [_flow_row(f) for f in flows]}


@register(
    "get_flow",
    "흐름 하나를 통째로: 초안 JSON(graph)과 그 검증 결과(valid·errors), 버전 목록(배포 중 표시). "
    "version을 주면 초안 대신 그 버전의 JSON. 인자: flow_id, version?. 실패: flow_not_found·version_not_found.",
    GetFlowArgs,
    scope="flow.read",
    permission=None,
    idempotent=True,
)
async def get_flow(ctx: ToolContext, args: GetFlowArgs) -> JSONObject:
    tenant_id = _tenant_id(ctx)
    detail = await _service(lambda: services.flow_detail(tenant_id, args.flow_id))
    deployed_id = detail.flow.deployed_version_id
    out: JSONObject = {
        **_flow_row(detail.flow),
        "versions": [_version_row(v, deployed_id) for v in detail.versions],
    }
    if args.version is None:
        out["draft"] = {"graph": detail.draft.graph, "valid": detail.draft.valid, "errors": _errors(detail.draft.errors)}
    else:
        number = args.version
        version = await _service(lambda: services.get_version(tenant_id, args.flow_id, number))
        out["version"] = {**_version_row(version, deployed_id), "graph": version.graph}
    return out


@register(
    "create_flow",
    "빈 초안으로 새 흐름을 만든다(배포·공개 아님). 인자: name. 실패: duplicate_name.",
    CreateFlowArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
    destructive=False,
)
async def create_flow(ctx: ToolContext, args: CreateFlowArgs) -> JSONObject:
    flow = await _service(lambda: services.create_flow(_tenant_id(ctx), args.name))
    return _flow_row(flow)


@register(
    "update_flow",
    "초안을 연산 목록으로 고친다(원자적). 연산: upsert_node{id, kind?, code?, name?, position?, config?} — 새 노드는 "
    "kind·code 필수, 있는 노드는 준 필드만 바꾸고 config는 통째로 바꿈; delete_node{id} — 닿은 선도 지움; "
    "connect{from, to, handle?} — 같은 갈래의 선은 바꿈; disconnect{from, handle?}. handle 기본 next. "
    "초안은 틀려도 저장되고 결과에 valid·errors[{node_id, code, message}]가 온다. "
    "실패(아무것도 안 바뀜): invalid_patch(ops[i] 사유), flow_not_found.",
    UpdateFlowArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
)
async def update_flow(ctx: ToolContext, args: UpdateFlowArgs) -> JSONObject:
    result = await _service(lambda: services.patch_draft(_tenant_id(ctx), args.flow_id, list(args.ops)))
    graph = result.graph
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    edges = graph.get("edges") if isinstance(graph, dict) else None
    return {
        "valid": result.valid,
        "errors": _errors(result.errors),
        "nodes": len(nodes) if isinstance(nodes, list) else 0,
        "edges": len(edges) if isinstance(edges, list) else 0,
    }


@register(
    "save_version",
    "초안을 바뀌지 않는 새 버전으로 저장한다(배포는 따로). 초안이 마지막 버전과 같으면 그 버전을 돌려준다(created=false). "
    "초안이 검증을 통과하지 못하면 saved=false와 errors. 인자: flow_id, note(선택 메모, 200자 이내; 실패: note_too_long).",
    SaveVersionArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
    destructive=False,
    idempotent=True,
)
async def save_version(ctx: ToolContext, args: SaveVersionArgs) -> JSONObject:
    user_id = ctx.require_membership().user_id
    try:
        result = await _service(lambda: services.save_version(_tenant_id(ctx), args.flow_id, created_by_id=user_id, note=args.note))
    except services.FlowInvalid as exc:
        return {"saved": False, "message": exc.message, "errors": _errors(exc.errors)}
    return {"saved": True, "version": result.version.number, "created": result.created}


@register(
    "deploy_version",
    "버전을 배포 중으로 둔다 — 이 흐름이 공개 흐름이면 새 대화부터 이 버전으로 돈다. 되돌리기는 이전 번호를 배포. "
    "실행기에 핸들러가 없는 노드가 있으면 deployed=false와 errors(code no_handler). "
    "테넌트가 「배포에 승인 필요」를 켰으면 배포 대신 요청을 만들어 deployed=false, approval_required=true, request_id를 돌려준다. "
    "인자: flow_id, version. 실패: version_not_found.",
    DeployArgs,
    scope="flow.deploy",
    legacy_scope="flow.write",  # #207 호환 기간
    permission=FLOW_DEPLOY,
    writes=True,
)
async def deploy_version(ctx: ToolContext, args: DeployArgs) -> JSONObject:
    user_id = ctx.require_membership().user_id
    try:
        outcome = await _service(
            lambda: services.submit_deploy(_tenant_id(ctx), args.flow_id, args.version, requested_by_id=user_id)
        )
    except services.FlowInvalid as exc:
        return {"deployed": False, "message": exc.message, "errors": _errors(exc.errors)}
    if outcome.request is not None:  # 이 테넌트는 배포에 승인이 필요하다 — 다른 Admin이 승인하면 배포된다
        return {
            "deployed": False,
            "approval_required": True,
            "request_id": str(outcome.request.pk),
            "message": "이 테넌트는 배포에 승인이 필요해 배포 요청을 만들었습니다. 다른 Admin이 승인하면 배포됩니다.",
            **_flow_row(outcome.flow),
        }
    return {"deployed": True, **_flow_row(outcome.flow)}


@register(
    "test_turn",
    "초안으로 방문자 메시지 한 턴을 돌려 답(reply)과 지나간 노드(trace)를 돌려준다. 시험 세션이라 운영 대화 목록에 "
    "섞이지 않고, 사람 넘김은 실제로 넘기지 않고 handoff=true로만 표시한다. 체험 테넌트면 체험 턴 1을 쓴다. "
    "초안이 무효이거나 핸들러 없는 노드가 있으면 ran=false와 errors. 인자: flow_id, message, session_id?(이어 가기). "
    "실패: flow_not_found·session_not_found.",
    TestTurnArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
    destructive=False,
)
async def test_turn(ctx: ToolContext, args: TestTurnArgs) -> JSONObject:
    try:
        result = await services.run_test_turn(_tenant_id(ctx), args.flow_id, args.message, session_id=args.session_id)
    except services.FlowServiceError as exc:
        raise ToolError(exc.code, exc.message) from exc
    trace: list[JSON] = list(result.trace)
    return {
        "ran": result.ran,
        "reply": result.reply,
        "trace": trace,
        "handoff": result.handoff,
        "session_id": str(result.session_id) if result.session_id else None,
        "errors": _errors(result.errors),
        "note": result.note,
    }


@register(
    "archive_flow",
    "흐름을 보관한다(목록·조회에서 빠짐; 버전·세션·판단 기록은 남는다). 위젯이 쓰는 공개 흐름은 거부. "
    "인자: flow_id. 실패: flow_not_found·flow_in_use.",
    FlowArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
)
async def archive_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    flow = await _service(lambda: services.archive_flow(_tenant_id(ctx), args.flow_id))
    return {"archived": True, "id": str(flow.id), "name": flow.name}


@register(
    "rollback_flow",
    "배포 중 버전 바로 앞 번호의 버전을 다시 배포한다(긴급 복구라 확인 단계·배포 승인 규칙을 타지 않는다). "
    "배포 중인 버전이 없거나 앞선 버전이 없으면 version_not_found. 인자: flow_id.",
    FlowArgs,
    scope="flow.deploy",
    legacy_scope="flow.write",  # #207 호환 기간
    permission=FLOW_DEPLOY,
    writes=True,
)
async def rollback_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    flow = await _service(lambda: services.rollback(_tenant_id(ctx), args.flow_id))
    return {"deployed": True, **_flow_row(flow)}


async def _preview_restore_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    flow = await _service(lambda: services.get_archived_flow(_tenant_id(ctx), args.flow_id))
    return {
        "action": "보관한 흐름을 되돌린다(흐름 목록에 다시 보인다. 같은 이름의 흐름이 그사이 생겼으면 실패한다)",
        "id": str(flow.id),
        "name": flow.name,
        "archived_at": flow.archived_at.isoformat() if flow.archived_at else None,
    }


@register(
    "restore_flow",
    "보관한 흐름(archive_flow)을 되돌린다. 미리보기는 흐름 이름·보관 시각. 인자: flow_id. 실패: flow_not_found·duplicate_name.",
    FlowArgs,
    scope="flow.write",
    permission=FLOW_EDIT,
    writes=True,
    preview=_preview_restore_flow,
)
async def restore_flow(ctx: ToolContext, args: FlowArgs) -> JSONObject:
    flow = await _service(lambda: services.restore_flow(_tenant_id(ctx), args.flow_id))
    return {"restored": True, "id": str(flow.id), "name": flow.name}
