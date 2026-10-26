"""흐름 편집 공용 서비스 층(#76, 스펙 #74 결정 4·6·7).

MCP 도구(``apps/mcp/tools/flows.py``)·흐름 HTTP API(#28)·편집기(#29)가 모두 이 함수들을 그대로 감싼다 — 호출부마다
편집 규칙을 다시 쓰지 않는다. 권한(Admin만 쓰기)은 호출부가 판정하고, **테넌트 범위는 여기서** 모든 함수의 첫 인자
``tenant_id``로 강제한다(``for_tenant``). 남의 테넌트 흐름은 ``FlowNotFound``(존재 여부를 흘리지 않음).

- 초안(``FlowDraft``)은 틀려도 저장한다(작업본, #22). 저장·패치는 검증 결과(``FlowError`` 목록)를 함께 돌려준다.
- 버전 저장은 스키마 검증을 통과한 초안만, 배포는 거기에 더해 실행기가 돌릴 수 있는(핸들러 있는) 흐름만.
  둘 다 실패는 ``FlowInvalid(errors)`` — 예외지만 오류는 「노드 id·코드·이유」 목록이라 호출부가 그대로 보여 준다.
- 되돌리기 = 이전 버전을 다시 배포(``rollback``). 버전은 불변이라 되돌려도 버전이 새로 생기지 않는다.
- 시험 한 턴(``run_test_turn``)은 ``run_test_turn`` 머리말 참조.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from apps.flows.models import DeployRequest, Flow, FlowDraft, FlowVersion
from apps.flows.schema import DEFAULT_HANDLE, JSON, FlowError, NodeKind, validate_flow

if TYPE_CHECKING:
    from apps.chat.models import ChatSession

type JSONObject = dict[str, JSON]

EMPTY_GRAPH: JSONObject = {"nodes": [], "edges": []}


# ── 오류 ─────────────────────────────────────────────────────────────────────


class FlowServiceError(Exception):
    """호출부가 사유를 그대로 보여 줄 수 있는 실패. ``code``는 기계용, ``message``는 사람용."""

    code = "flow_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class FlowNotFound(FlowServiceError):
    code = "flow_not_found"


class VersionNotFound(FlowServiceError):
    code = "version_not_found"


class PatchError(FlowServiceError):
    """패치 연산을 적용할 수 없다(없는 노드 지우기 등). 묶음 전체가 적용되지 않는다."""

    code = "invalid_patch"

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"ops[{index}]: {message}")
        self.index = index


class FlowInvalid(FlowServiceError):
    """검증을 통과하지 못해 버전 저장·배포를 할 수 없다."""

    code = "flow_invalid"

    def __init__(self, message: str, errors: list[FlowError]) -> None:
        super().__init__(message)
        self.errors = errors


# ── 결과 ─────────────────────────────────────────────────────────────────────


class TryDeployRefused(FlowServiceError):
    """체험 테넌트(Try Session, #134)는 배포하지 않는다 — 편집·버전·시험 대화만."""

    code = "try_restricted"


def _refuse_try_deploy(tenant_id: uuid.UUID) -> None:
    from apps.tenants import try_tenant

    try:
        try_tenant.ensure_allowed(tenant_id, "deploy")
    except try_tenant.TryRestricted as exc:
        raise TryDeployRefused("체험 흐름은 배포할 수 없습니다. 가입한 뒤 내 테넌트에서 배포하세요.") from exc


class ApprovalRequired(FlowServiceError):
    """이 테넌트는 배포에 승인이 필요하다 — ``submit_deploy``로 요청을 만들어야 한다."""

    code = "approval_required"


@dataclass(frozen=True)
class DraftResult:
    graph: JSON
    errors: list[FlowError]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class FlowDetail:
    flow: Flow
    draft: DraftResult
    versions: list[FlowVersion]


@dataclass(frozen=True)
class SaveVersionResult:
    version: FlowVersion
    created: bool
    """False면 초안이 마지막 버전과 같아 그 버전을 돌려줬다."""


# ── 읽기 ─────────────────────────────────────────────────────────────────────


def list_flows(tenant_id: uuid.UUID) -> list[Flow]:
    return list(Flow.objects.for_tenant(tenant_id).active().select_related("deployed_version").order_by("created_at"))


def get_flow(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> Flow:
    try:
        return Flow.objects.for_tenant(tenant_id).active().select_related("deployed_version").get(pk=flow_id)
    except (Flow.DoesNotExist, DjangoValidationError, ValueError, TypeError) as exc:  # UUID가 아닌 문자열
        raise FlowNotFound(f"흐름 '{flow_id}'이 이 테넌트에 없습니다.") from exc


def _draft_graph(flow: Flow) -> JSON:
    draft = FlowDraft.objects.filter(flow=flow).first()
    return draft.graph if draft is not None else copy.deepcopy(EMPTY_GRAPH)


def flow_detail(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> FlowDetail:
    """초안(검증 결과 포함)·배포 중 버전·버전 목록을 한 번에."""
    flow = get_flow(tenant_id, flow_id)
    graph = _draft_graph(flow)
    versions = list(FlowVersion.objects.for_tenant(tenant_id).filter(flow=flow).order_by("number"))
    return FlowDetail(flow=flow, draft=DraftResult(graph, validate_for_tenant(tenant_id, graph)), versions=versions)


def get_version(tenant_id: uuid.UUID, flow_id: uuid.UUID | str, number: int) -> FlowVersion:
    flow = get_flow(tenant_id, flow_id)
    version = FlowVersion.objects.for_tenant(tenant_id).filter(flow=flow, number=number).first()
    if version is None:
        raise VersionNotFound(f"흐름 '{flow.name}'에 버전 {number}이 없습니다.")
    return version


# ── 만들기·초안 저장 ─────────────────────────────────────────────────────────


def create_flow(tenant_id: uuid.UUID, name: str, graph: JSON | None = None) -> Flow:
    """빈(또는 주어진) 초안으로 흐름을 만든다. 배포 중 버전은 없고 공개 흐름도 아니다."""
    name = name.strip()
    if not name:
        raise FlowServiceError("흐름 이름이 비었습니다.", code="invalid_name")
    try:
        with transaction.atomic():
            flow = Flow.objects.create(tenant_id=tenant_id, name=name)
            FlowDraft.objects.create(flow=flow, graph=graph if graph is not None else copy.deepcopy(EMPTY_GRAPH))
    except IntegrityError as exc:
        raise FlowServiceError(f"이름이 '{name}'인 흐름이 이미 있습니다.", code="duplicate_name") from exc
    return flow


class FlowInUse(FlowServiceError):
    """위젯이 쓰는 공개 흐름이라 보관할 수 없다."""

    code = "flow_in_use"


def archive_flow(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> Flow:
    """흐름을 보관한다(soft delete). 위젯이 쓰는 공개 흐름은 ``FlowInUse`` — 먼저 다른 흐름을 배포해야 한다.

    버전·세션·판단 기록은 지우지 않는다. 기다리던 배포 요청은 ``superseded``로 닫는다(승인해도 갈 곳이 없다).
    """
    pk = get_flow(tenant_id, flow_id).pk
    with transaction.atomic():
        flow = Flow.objects.select_for_update().get(pk=pk)
        if flow.archived_at is not None:  # 잠금을 기다리는 사이 다른 호출이 먼저 보관했다
            raise FlowNotFound(f"흐름 '{flow_id}'이 이 테넌트에 없습니다.")
        if flow.is_public:
            raise FlowInUse(
                f"'{flow.name}'은(는) 위젯이 쓰고 있는 흐름이라 보관할 수 없습니다. 먼저 다른 흐름을 배포해 위젯이 그 흐름으로 돌게 하세요."
            )
        flow.archived_at = timezone.now()
        flow.save(update_fields=["archived_at", "updated_at"])
        DeployRequest.objects.filter(flow=flow, status=DeployRequest.PENDING).update(status=DeployRequest.SUPERSEDED)
    return flow


def get_archived_flow(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> Flow:
    """보관된 흐름 하나. 없거나 보관 상태가 아니면 ``FlowNotFound``."""
    try:
        return Flow.objects.for_tenant(tenant_id).filter(archived_at__isnull=False).select_related("deployed_version").get(pk=flow_id)
    except (Flow.DoesNotExist, DjangoValidationError, ValueError, TypeError) as exc:
        raise FlowNotFound(f"보관된 흐름 '{flow_id}'이 이 테넌트에 없습니다.") from exc


def restore_flow(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> Flow:
    """보관한 흐름을 되돌린다. 보관 사이 같은 이름의 흐름이 생겼으면 ``duplicate_name``."""
    flow = get_archived_flow(tenant_id, flow_id)
    flow.archived_at = None
    try:
        with transaction.atomic():
            flow.save(update_fields=["archived_at", "updated_at"])
    except IntegrityError as exc:
        raise FlowServiceError(f"이름이 '{flow.name}'인 흐름이 이미 있어 되돌릴 수 없습니다. 그 흐름 이름을 먼저 바꾸세요.", code="duplicate_name") from exc
    return flow


def rename_flow(tenant_id: uuid.UUID, flow_id: uuid.UUID | str, name: str) -> Flow:
    name = name.strip()
    if not name:
        raise FlowServiceError("흐름 이름이 비었습니다.", code="invalid_name")
    flow = get_flow(tenant_id, flow_id)
    flow.name = name
    try:
        with transaction.atomic():
            flow.save(update_fields=["name", "updated_at"])
    except IntegrityError as exc:
        raise FlowServiceError(f"이름이 '{name}'인 흐름이 이미 있습니다.", code="duplicate_name") from exc
    return flow


def _locked_draft(flow: Flow) -> FlowDraft:
    draft, _ = FlowDraft.objects.select_for_update().get_or_create(flow=flow, defaults={"graph": EMPTY_GRAPH})
    return draft


def save_draft(tenant_id: uuid.UUID, flow_id: uuid.UUID | str, graph: JSON) -> DraftResult:
    """초안을 통째로 바꾼다(편집기 저장). 틀려도 저장하고 검증 결과를 돌려준다."""
    flow = get_flow(tenant_id, flow_id)
    with transaction.atomic():
        draft = _locked_draft(flow)
        draft.graph = graph
        draft.save(update_fields=["graph", "updated_at"])
    return DraftResult(graph, validate_for_tenant(tenant_id, graph))


# ── 패치 ─────────────────────────────────────────────────────────────────────


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class UpsertNode(_Op):
    """노드를 더하거나(새 id) 고친다(있는 id — 준 필드만 바꾸고 config는 통째로 바꾼다)."""

    op: Literal["upsert_node"]
    id: str = Field(min_length=1, max_length=64, description="노드 id")
    kind: str | None = Field(default=None, description="노드 종류(새 노드면 필수)")
    code: str | None = Field(default=None, description="N-01 꼴 코드(새 노드면 필수)")
    name: str | None = None
    position: dict[str, float] | None = Field(default=None, description="{x, y}")
    config: dict[str, JSON] | None = Field(default=None, description="노드 config 전체(부분 병합 아님)")


class DeleteNode(_Op):
    """노드와 그 노드에 닿은 선을 모두 지운다."""

    op: Literal["delete_node"]
    id: str


class Connect(_Op):
    """선 하나. 같은 (from, handle)에 선이 있으면 그 선의 to를 바꾼다(한 갈래에 선 하나)."""

    op: Literal["connect"]
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    handle: str = DEFAULT_HANDLE


class Disconnect(_Op):
    """(from, handle)에서 나가는 선을 지운다."""

    op: Literal["disconnect"]
    source: str = Field(alias="from")
    handle: str = DEFAULT_HANDLE


type PatchOp = Annotated[UpsertNode | DeleteNode | Connect | Disconnect, Field(discriminator="op")]


def _graph_lists(graph: JSON) -> tuple[list[JSONObject], list[JSONObject]]:
    """초안을 고칠 수 있는 (nodes, edges) 사본으로. 모양이 깨진 초안은 깨진 부분을 빈 목록으로 본다."""
    data = copy.deepcopy(graph) if isinstance(graph, dict) else {}
    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    nodes = [n for n in raw_nodes if isinstance(n, dict)] if isinstance(raw_nodes, list) else []
    edges = [e for e in raw_edges if isinstance(e, dict)] if isinstance(raw_edges, list) else []
    return nodes, edges


def _edge_handle(edge: JSONObject) -> JSON:
    return edge.get("handle") or DEFAULT_HANDLE


def apply_ops(graph: JSON, ops: list[UpsertNode | DeleteNode | Connect | Disconnect]) -> JSONObject:
    """패치 연산을 차례로 적용한 새 흐름 JSON. 적용할 수 없는 연산이 있으면 ``PatchError``(아무것도 안 바뀜).

    스키마 검증은 하지 않는다 — 초안은 틀려도 된다. 연산 자체가 말이 안 되는 경우(없는 노드 지우기, 새 노드에
    kind·code 없음, 없는 노드에 선 잇기, 없는 선 끊기)만 거부한다.
    """
    nodes, edges = _graph_lists(graph)
    for index, op in enumerate(ops):
        ids = {node_id for n in nodes if isinstance(node_id := n.get("id"), str)}
        match op:
            case UpsertNode():
                fields: JSONObject = {}
                for key in ("kind", "code", "name", "config"):
                    value = getattr(op, key)
                    if value is not None:
                        fields[key] = value
                if op.position is not None:
                    fields["position"] = dict(op.position)
                existing = next((n for n in nodes if n.get("id") == op.id), None)
                if existing is not None:
                    existing.update(fields)
                    continue
                if op.kind is None or op.code is None:
                    raise PatchError(index, f"새 노드 '{op.id}'에는 kind와 code가 필요합니다.")
                nodes.append({"id": op.id, "name": "", "position": {"x": 0, "y": 0}, "config": {}, **fields})
            case DeleteNode():
                if op.id not in ids:
                    raise PatchError(index, f"노드 '{op.id}'가 없습니다.")
                nodes = [n for n in nodes if n.get("id") != op.id]
                edges = [e for e in edges if e.get("from") != op.id and e.get("to") != op.id]
            case Connect():
                for end in (op.source, op.target):
                    if end not in ids:
                        raise PatchError(index, f"노드 '{end}'가 없습니다. 먼저 upsert_node로 만드세요.")
                edges = [e for e in edges if not (e.get("from") == op.source and _edge_handle(e) == op.handle)]
                edge: JSONObject = {"from": op.source, "to": op.target}
                if op.handle != DEFAULT_HANDLE:
                    edge["handle"] = op.handle
                edges.append(edge)
            case Disconnect():
                kept = [e for e in edges if not (e.get("from") == op.source and _edge_handle(e) == op.handle)]
                if len(kept) == len(edges):
                    raise PatchError(index, f"'{op.source}'의 '{op.handle}' 갈래에 선이 없습니다.")
                edges = kept
    nodes_json: list[JSON] = list(nodes)
    edges_json: list[JSON] = list(edges)
    return {"nodes": nodes_json, "edges": edges_json}


def patch_draft(
    tenant_id: uuid.UUID, flow_id: uuid.UUID | str, ops: list[UpsertNode | DeleteNode | Connect | Disconnect]
) -> DraftResult:
    """패치 묶음을 초안에 원자적으로 적용한다(하나라도 적용 못 하면 아무것도 저장 안 함). 검증 결과를 돌려준다."""
    flow = get_flow(tenant_id, flow_id)
    with transaction.atomic():
        draft = _locked_draft(flow)
        graph = apply_ops(draft.graph, ops)
        draft.graph = graph
        draft.save(update_fields=["graph", "updated_at"])
    return DraftResult(graph, validate_for_tenant(tenant_id, graph))


# ── 버전 저장·배포·되돌리기 ──────────────────────────────────────────────────


def validate_for_tenant(tenant_id: uuid.UUID, graph: JSON) -> list[FlowError]:
    """스키마 검증(``validate_flow``) + 이 테넌트에 없는 ``{{secret.이름}}`` 참조(``unknown_secret``, #33).

    비밀 참조는 테넌트마다 다르므로 스키마가 아니라 여기서 본다. 스키마 오류가 있으면 그것만 돌려준다.
    """
    from apps.tenants.tenant_secrets import secret_names, secret_refs

    errors = validate_flow(graph)
    if errors:
        return errors
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    known: set[str] | None = None
    for node in nodes if isinstance(nodes, list) else []:
        refs = secret_refs(node)
        if not refs:
            continue
        if known is None:
            known = secret_names(tenant_id)
        node_id = node.get("id") if isinstance(node, dict) else None
        for name in sorted(refs - known):
            errors.append(FlowError(
                node_id if isinstance(node_id, str) else None,
                "unknown_secret",
                f"비밀 '{name}'이(가) 등록돼 있지 않습니다(설정 → 비밀에서 먼저 등록하세요).",
            ))
    return errors


def unsupported_node_errors(graph: JSON) -> list[FlowError]:
    """실행기에 핸들러가 아직 없는 노드마다 오류 하나(스키마는 통과한 흐름이라고 본다)."""
    from apps.flows.executor import HANDLERS

    errors: list[FlowError] = []
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        if isinstance(kind, str) and kind in NodeKind.__members__.values() and NodeKind(kind) not in HANDLERS:
            node_id = node.get("id")
            errors.append(FlowError(
                node_id if isinstance(node_id, str) else None,
                "no_handler",
                f"실행기에 아직 '{kind}' 노드 핸들러가 없습니다(배포·시험 불가).",
            ))
    return errors


VERSION_NOTE_MAX = 200  # 한 줄 변경 설명 — 편집기 버전 이력에 그대로 보이는 길이


def save_version(
    tenant_id: uuid.UUID,
    flow_id: uuid.UUID | str,
    *,
    created_by_id: uuid.UUID | None = None,
    note: str | None = None,
) -> SaveVersionResult:
    """초안을 새 Flow Version으로 굳힌다. 검증 실패면 ``FlowInvalid``. 마지막 버전과 같으면 그 버전을 돌려준다(메모는 무시).

    ``note``는 새 버전을 만들 때만 붙는다(불변). ``VERSION_NOTE_MAX``자를 넘으면 ``note_too_long``.
    """
    from apps.flows.schema import flow_hash

    note = (note or "").strip()
    if len(note) > VERSION_NOTE_MAX:
        raise FlowServiceError(f"버전 메모는 {VERSION_NOTE_MAX}자 이내여야 합니다(지금 {len(note)}자).", code="note_too_long")

    flow = get_flow(tenant_id, flow_id)
    with transaction.atomic():
        Flow.objects.select_for_update().only("id").get(pk=flow.pk)  # 번호 경쟁 막기
        graph = _draft_graph(flow)
        errors = validate_for_tenant(tenant_id, graph)
        if errors:
            raise FlowInvalid("초안이 검증을 통과하지 못해 버전으로 저장할 수 없습니다.", errors)
        last = FlowVersion.objects.filter(flow=flow).order_by("-number").first()
        if last is not None and last.hash == flow_hash(graph):
            return SaveVersionResult(last, created=False)
        version = FlowVersion.objects.create(flow=flow, graph=graph, note=note, created_by_id=created_by_id)
    return SaveVersionResult(version, created=True)


def deploy_requires_approval(flow: Flow) -> bool:
    """이 흐름의 테넌트가 「배포에 승인 필요」를 켰는지(``TenantConfig.deploy_requires_approval``)."""
    from apps.tenants.models import TenantConfig

    return TenantConfig.objects.filter(tenant_id=flow.tenant_id, deploy_requires_approval=True).exists()


def _apply_deploy(tenant_id: uuid.UUID, flow_id: uuid.UUID | str, number: int) -> Flow:
    """승인 규칙 없이 그 버전을 배포 중으로 둔다(승인 처리·되돌리기가 쓴다). 핸들러 없는 노드가 있으면 ``FlowInvalid``.
    체험 테넌트면 ``TryDeployRefused``(배포 경로가 모두 여기를 지난다)."""
    _refuse_try_deploy(tenant_id)
    version = get_version(tenant_id, flow_id, number)
    errors = unsupported_node_errors(version.graph)
    if errors:
        raise FlowInvalid(f"버전 {number}에 실행기가 아직 돌릴 수 없는 노드가 있어 배포할 수 없습니다.", errors)
    with transaction.atomic():
        flow = Flow.objects.select_for_update().get(pk=version.flow_id)
        flow.deployed_version = version
        flow.save(update_fields=["deployed_version", "updated_at"])
    return flow


def deploy_version(tenant_id: uuid.UUID, flow_id: uuid.UUID | str, number: int) -> Flow:
    """그 버전을 곧바로 배포한다. 승인이 필요한 테넌트면 ``ApprovalRequired``(우회 금지 — ``submit_deploy`` 사용)."""
    flow = get_flow(tenant_id, flow_id)
    if deploy_requires_approval(flow):
        raise ApprovalRequired("이 테넌트는 배포에 관리자 승인이 필요합니다. 배포 요청을 만드세요.")
    return _apply_deploy(tenant_id, flow_id, number)


@dataclass(frozen=True)
class DeployOutcome:
    flow: Flow
    request: DeployRequest | None = None
    """승인이 필요해 배포 대신 만든 요청. 바로 배포됐으면 None."""

    @property
    def deployed(self) -> bool:
        return self.request is None


def submit_deploy(
    tenant_id: uuid.UUID, flow_id: uuid.UUID | str, number: int, *, requested_by_id: uuid.UUID | None = None
) -> DeployOutcome:
    """배포 진입점(HTTP·MCP 공통): 승인 불필요면 배포, 필요면 배포 요청을 만든다(앞의 대기 요청은 ``superseded``).

    요청을 만들 때도 버전 존재·핸들러 있음을 검사한다 — 승인자가 배포할 수 없는 버전을 받지 않게.
    """
    _refuse_try_deploy(tenant_id)  # 배포 요청(승인 대기)도 만들지 않는다
    flow = get_flow(tenant_id, flow_id)
    if not deploy_requires_approval(flow):
        return DeployOutcome(_apply_deploy(tenant_id, flow_id, number))
    version = get_version(tenant_id, flow_id, number)
    errors = unsupported_node_errors(version.graph)
    if errors:
        raise FlowInvalid(f"버전 {number}에 실행기가 아직 돌릴 수 없는 노드가 있어 배포를 요청할 수 없습니다.", errors)
    with transaction.atomic():
        Flow.objects.select_for_update().only("id").get(pk=flow.pk)
        DeployRequest.objects.filter(flow=flow, status=DeployRequest.PENDING).update(status=DeployRequest.SUPERSEDED)
        request = DeployRequest.objects.create(flow=flow, version=version, requested_by_id=requested_by_id)
    return DeployOutcome(flow, request)


def list_deploy_requests(tenant_id: uuid.UUID, status: str | None = None) -> list[DeployRequest]:
    qs = DeployRequest.objects.for_tenant(tenant_id).select_related("flow", "version")
    if status:
        qs = qs.filter(status=status)
    return list(qs.order_by("-created_at"))


def _get_request(tenant_id: uuid.UUID, request_id: uuid.UUID | str) -> DeployRequest:
    try:
        return DeployRequest.objects.for_tenant(tenant_id).select_related("flow", "version").get(pk=request_id)
    except (DeployRequest.DoesNotExist, DjangoValidationError, ValueError, TypeError) as exc:
        raise FlowServiceError(f"배포 요청 '{request_id}'이 이 테넌트에 없습니다.", code="request_not_found") from exc


def _decide(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID | str,
    status: str,
    decided_by_id: uuid.UUID | None,
    note: str | None,
) -> DeployRequest:
    from django.utils import timezone

    note = (note or "").strip()
    if len(note) > VERSION_NOTE_MAX:
        raise FlowServiceError(f"결정 메모는 {VERSION_NOTE_MAX}자 이내여야 합니다.", code="note_too_long")
    with transaction.atomic():
        request = DeployRequest.objects.select_for_update().get(pk=_get_request(tenant_id, request_id).pk)
        if request.status != DeployRequest.PENDING:
            raise FlowServiceError("이미 처리됐거나 새 요청으로 대체된 배포 요청입니다.", code="request_not_pending")
        if status == DeployRequest.APPROVED:
            if decided_by_id is not None and decided_by_id == request.requested_by_id:
                raise FlowServiceError("자기가 올린 배포 요청은 승인할 수 없습니다. 다른 Admin이 승인해야 합니다.", code="self_approval")
            _apply_deploy(tenant_id, request.flow_id, request.version.number)  # 실패(FlowInvalid)면 요청은 대기로 남는다
        request.status = status
        request.decided_by_id = decided_by_id
        request.decided_at = timezone.now()
        request.decision_note = note
        request.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return request


def approve_deploy_request(
    tenant_id: uuid.UUID, request_id: uuid.UUID | str, *, decided_by_id: uuid.UUID | None = None, note: str | None = None
) -> DeployRequest:
    """승인하고 그 버전을 배포한다. 요청자 본인은 불가(``self_approval``) — 사용자 없는 결정(TENANT_KEY)은 가능."""
    return _decide(tenant_id, request_id, DeployRequest.APPROVED, decided_by_id, note)


def reject_deploy_request(
    tenant_id: uuid.UUID, request_id: uuid.UUID | str, *, decided_by_id: uuid.UUID | None = None, note: str | None = None
) -> DeployRequest:
    return _decide(tenant_id, request_id, DeployRequest.REJECTED, decided_by_id, note)


def rollback(tenant_id: uuid.UUID, flow_id: uuid.UUID | str) -> Flow:
    """배포 중 버전 바로 앞 번호의 버전을 다시 배포한다. 긴급 복구라 승인 규칙을 타지 않는다(이미 있던 버전이다)."""
    flow = get_flow(tenant_id, flow_id)
    current = flow.deployed_version
    if current is None:
        raise VersionNotFound("배포 중인 버전이 없어 되돌릴 수 없습니다.")
    previous = FlowVersion.objects.filter(flow=flow, number__lt=current.number).order_by("-number").first()
    if previous is None:
        raise VersionNotFound(f"버전 {current.number}보다 앞선 버전이 없습니다.")
    return _apply_deploy(tenant_id, flow.pk, previous.number)


# ── 시험 한 턴 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TestTurnResult:
    ran: bool
    reply: str = ""
    trace: list[JSONObject] = field(default_factory=list)
    errors: list[FlowError] = field(default_factory=list)
    session_id: uuid.UUID | None = None
    handoff: bool = False
    note: str = ""


TEST_VISITOR_PREFIX = "flow-test:"
TRIAL_EXHAUSTED_NOTE = "체험 턴이 남지 않았거나 플랫폼 키가 없어 LLM 없이 멈췄습니다. LLM Provider를 넣거나 체험 한도를 확인하세요."


def _prepare_test_turn(
    tenant_id: uuid.UUID, flow_id: uuid.UUID | str, message: str, session_id: uuid.UUID | str | None
) -> tuple[JSON, list[FlowError], ChatSession | None, bool]:
    """(초안, 막는 오류, 시험 세션, 체험 턴 허락) — 오류가 있으면 세션을 만들지 않는다."""
    from apps.chat.models import ChatMessage, ChatSession
    from apps.tenants import trial
    from apps.tenants.models import TenantConfig

    flow = get_flow(tenant_id, flow_id)
    graph = _draft_graph(flow)
    errors = validate_for_tenant(tenant_id, graph) or unsupported_node_errors(graph)
    if errors:
        return graph, errors, None, False

    if session_id is None:
        session = ChatSession.objects.create(
            tenant_id=tenant_id, visitor_id=f"{TEST_VISITOR_PREFIX}{flow.pk}", is_test=True
        )
    else:
        try:
            session = ChatSession.objects.get(pk=session_id, tenant_id=tenant_id, is_test=True)
        except (ChatSession.DoesNotExist, DjangoValidationError, ValueError, TypeError) as exc:
            raise FlowServiceError(f"시험 세션 '{session_id}'이 없습니다.", code="session_not_found") from exc
    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content=message)
    config = TenantConfig.objects.get(tenant_id=tenant_id)
    return graph, [], session, trial.admit_turn(config)


async def run_test_turn(
    tenant_id: uuid.UUID, flow_id: uuid.UUID | str, message: str, *, session_id: uuid.UUID | str | None = None
) -> TestTurnResult:
    """초안으로 방문자 메시지 한 턴을 돌려 답과 지나간 노드를 돌려준다(편집 중 시험).

    - 초안이 검증을 통과하지 못했거나 핸들러 없는 노드가 있으면 돌리지 않고 ``errors``로 돌려준다(도구 오류 아님).
    - 대화는 ``is_test`` 세션에 쌓인다 — 운영 세션 목록·위젯 세션 재사용·방문자 메모리에 섞이지 않는다.
      ``session_id``로 같은 시험 세션을 이어 가면 ``await_reply`` 뒤 다음 턴도 시험할 수 있다.
    - 실행기를 ``dry_run``으로 돌린다: 사람 넘김은 넘겼다고만 표시(``handoff``)하고 Escalation·웹훅·SSE 알림이 없다.
    - LLM은 운영과 같은 Provider 선택을 탄다. 플랫폼 키(체험) 테넌트면 **시험 한 턴도 체험 턴 1을 쓴다**(ADR-0026) —
      남은 턴이 없으면 LLM 없이 ``ran=False``와 사유를 돌려준다.
    """
    from asgiref.sync import sync_to_async

    from apps.flows.executor import run_flow_turn_async

    graph, errors, session, admitted = await sync_to_async(_prepare_test_turn)(tenant_id, flow_id, message, session_id)
    if session is None:
        return TestTurnResult(ran=False, errors=errors, note="초안을 고친 뒤 다시 시험하세요.")
    if not admitted:
        return TestTurnResult(ran=False, session_id=session.id, note=TRIAL_EXHAUSTED_NOTE)

    state = await run_flow_turn_async(session, message, graph, dry_run=True)
    from apps.flows.trace import prepare

    # 시험 한 턴의 trace도 저장하는 판단 기록과 같은 항목 형식(가림·자름 포함)이다. 가림이 그 테넌트 비밀을 DB에서
    # 읽을 수 있어(#118) sync로 돌린다.
    entries = (await sync_to_async(prepare)(state.get("trace", []), tenant_id=tenant_id))[0]
    trace: list[JSONObject] = [dict(e) for e in entries]  # type: ignore[misc]
    handoff = any(t["kind"] == NodeKind.HANDOFF.value for t in trace)
    return TestTurnResult(
        ran=True,
        reply=str(state.get("assistant_response") or ""),
        trace=trace,
        session_id=session.id,
        handoff=handoff,
        note="사람 넘김 노드에 닿았습니다(시험이라 실제로 넘기지 않음)." if handoff else "",
    )
