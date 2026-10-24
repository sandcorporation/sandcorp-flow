"""흐름 JSON 스키마와 검증(ADR-0027).

Flow Version·Draft가 담는 JSON의 모양을 pydantic으로 정의하고, 그래프 수준 검증(시작 노드 하나, 알 수 없는 종류,
끊긴 선, 도달 불가 노드, 조건 갈래 빈 곳, id·N-코드 중복, 턴 경계 없는 순환)을 한다. 검증 결과는 편집기가 노드에
표시할 수 있게 ``FlowError(node_id, code, message)`` 목록으로 돌려준다.

공개 진입점:
- ``validate_flow(data) -> list[FlowError]`` — 빈 목록이면 유효.
- ``parse_flow(data) -> FlowGraph`` — 유효하지 않으면 ``FlowValidationError``.
- ``flow_hash(data) -> str`` — 정규화 JSON(키 정렬·공백 제거)의 SHA-256.

노드 config는 종류마다 pydantic 모델의 「자리」(필수 최소 필드)만 둔다. 세부 필드는 각 노드 티켓이 채운다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None

DEFAULT_HANDLE = "next"
"""갈래가 하나뿐인 노드의 나가는 선 handle. 선의 ``handle``을 비우면 이 값으로 본다."""

ELSE_HANDLE = "else"
"""조건 노드의 기본 갈래(어느 조건에도 맞지 않을 때)."""

MAX_STEPS_PER_TURN = 50
"""실행기가 한 턴에 실행할 노드 수 상한(무한 루프 방지 안전장치, ADR-0027 순환 규칙)."""

CODE_RE = re.compile(r"^N-\d{2,}$")
SECRET_REF_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SENSITIVE_HEADER_RE = re.compile(r"authorization|cookie|token|secret|api[-_]?key|password", re.IGNORECASE)


class NodeKind(StrEnum):
    START = "start"
    AI_ANSWER = "ai_answer"
    CONDITION = "condition"
    HANDOFF = "handoff"
    KNOWLEDGE_SEARCH = "knowledge_search"
    AI_CLASSIFY = "ai_classify"
    GUARDRAIL = "guardrail"
    HTTP_TOOL = "http_tool"


# ── 노드 config 자리 ──────────────────────────────────────────────────────────


class NodeConfig(BaseModel):
    """노드 config의 공통 바탕. 모르는 필드는 거부한다(오타가 조용히 무시되지 않게)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_boundary: ClassVar[bool] = False
    """이 노드에서 턴이 끝나고 다음 방문자 메시지를 기다리는가. 순환은 이런 노드를 지나야 한다."""

    def handles(self) -> tuple[str, ...]:
        """이 노드가 낼 수 있는 나가는 선의 handle들."""
        return (DEFAULT_HANDLE,)

    def required_handles(self) -> tuple[str, ...]:
        """반드시 선이 이어져야 하는 handle들(비면 「빈 갈래」)."""
        return ()

    def waits_for_reply(self) -> bool:
        return self.turn_boundary


class StartConfig(NodeConfig):
    pass


class AiAnswerConfig(NodeConfig):
    instructions: str = ""
    await_reply: bool = False
    """참이면 답한 뒤 턴을 끝내고, 다음 턴은 이 노드의 나가는 선에서 이어진다(되묻기)."""

    def waits_for_reply(self) -> bool:
        return self.await_reply


class ConditionBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    when: str = Field(min_length=1)


class ConditionConfig(NodeConfig):
    branches: list[ConditionBranch] = Field(min_length=1)

    @field_validator("branches")
    @classmethod
    def _unique_names(cls, branches: list[ConditionBranch]) -> list[ConditionBranch]:
        names = [b.name for b in branches]
        if ELSE_HANDLE in names or DEFAULT_HANDLE in names:
            raise ValueError(f"갈래 이름 '{ELSE_HANDLE}'·'{DEFAULT_HANDLE}'은 예약어입니다")
        if len(set(names)) != len(names):
            raise ValueError("갈래 이름이 중복됩니다")
        return branches

    def handles(self) -> tuple[str, ...]:
        return (*(b.name for b in self.branches), ELSE_HANDLE)

    def required_handles(self) -> tuple[str, ...]:
        return self.handles()


class HandoffConfig(NodeConfig):
    turn_boundary: ClassVar[bool] = True
    reason: str = ""

    def handles(self) -> tuple[str, ...]:
        return ()


class KnowledgeSearchConfig(NodeConfig):
    top_k: int = Field(default=5, ge=1, le=20)


class AiClassifyConfig(NodeConfig):
    instructions: str = ""
    labels: list[str] = Field(min_length=1)


GuardrailRule = Literal["banned_words", "pii", "price_promise"]


class GuardrailConfig(NodeConfig):
    rules: list[GuardrailRule] = Field(min_length=1)
    action: Literal["block", "rewrite"] = "rewrite"


class HttpToolConfig(NodeConfig):
    """HTTP 도구(ADR-0029). 허용 목록·IP 차단은 실행 때(#34). 여기선 모양과 비밀 참조 규칙만."""

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: str | None = None

    @field_validator("url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        if not url.startswith("https://"):
            raise ValueError("url은 https:// 로 시작해야 합니다")
        if "@" in url.split("/", 3)[2]:
            raise ValueError("url에 사용자 정보(user@host)를 넣을 수 없습니다")
        if SECRET_REF_RE.search(url):
            raise ValueError("url에는 비밀 참조를 넣을 수 없습니다(헤더에 두세요)")
        return url

    @field_validator("headers")
    @classmethod
    def _secrets_by_reference(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if not SENSITIVE_HEADER_RE.search(name):
                continue
            # 인증 계열 헤더 값은 {{secret.이름}} 참조(앞의 스킴 한 단어 허용, 예: "Bearer {{secret.key}}")만.
            rest = SECRET_REF_RE.sub("", value).strip()
            if not SECRET_REF_RE.search(value) or (rest and not re.fullmatch(r"[A-Za-z]+", rest)):
                raise ValueError(f"헤더 '{name}'에는 비밀 원문 대신 {{{{secret.이름}}}} 참조를 넣으세요")
        return headers


NODE_CONFIG_MODELS: dict[NodeKind, type[NodeConfig]] = {
    NodeKind.START: StartConfig,
    NodeKind.AI_ANSWER: AiAnswerConfig,
    NodeKind.CONDITION: ConditionConfig,
    NodeKind.HANDOFF: HandoffConfig,
    NodeKind.KNOWLEDGE_SEARCH: KnowledgeSearchConfig,
    NodeKind.AI_CLASSIFY: AiClassifyConfig,
    NodeKind.GUARDRAIL: GuardrailConfig,
    NodeKind.HTTP_TOOL: HttpToolConfig,
}


# ── 문서 모양 ────────────────────────────────────────────────────────────────


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float
    y: float


class RawNode(BaseModel):
    """종류·config를 아직 해석하지 않은 노드(노드별 오류를 모으려고 두 단계로 읽는다)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    kind: str
    code: str
    name: str = ""
    position: Position = Position(x=0, y=0)
    config: dict[str, JSON] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: str = Field(alias="from", min_length=1)
    target: str = Field(alias="to", min_length=1)
    handle: str = DEFAULT_HANDLE

    @model_validator(mode="before")
    @classmethod
    def _null_handle(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("handle") is None:
            return {k: v for k, v in data.items() if k != "handle"}
        return data


class RawFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[RawNode]
    edges: list[Edge] = Field(default_factory=list)


@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    code: str
    name: str
    position: Position
    config: NodeConfig


@dataclass(frozen=True)
class FlowGraph:
    """검증을 통과한 흐름. 실행기(#23)가 이것을 StateGraph로 짓는다."""

    nodes: dict[str, Node]
    edges: tuple[Edge, ...]

    @property
    def start(self) -> Node:
        return next(n for n in self.nodes.values() if n.kind is NodeKind.START)

    def outgoing(self, node_id: str) -> dict[str, str]:
        """handle → 다음 노드 id."""
        return {e.handle: e.target for e in self.edges if e.source == node_id}


@dataclass(frozen=True)
class FlowError:
    """사람이 읽을 검증 오류. ``node_id``가 없으면 흐름 전체에 대한 오류."""

    node_id: str | None
    code: str
    message: str

    def as_dict(self) -> dict[str, str | None]:
        return {"node_id": self.node_id, "code": self.code, "message": self.message}


class FlowValidationError(ValueError):
    def __init__(self, errors: list[FlowError]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"[{e.code}] {e.node_id or '-'}: {e.message}" for e in errors))


# ── 정규화·해시 ──────────────────────────────────────────────────────────────


def canonical_json(data: JSON) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def flow_hash(data: JSON) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


# ── 검증 ─────────────────────────────────────────────────────────────────────


def _pydantic_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = str(err["msg"]).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def _build(data: object) -> tuple[FlowGraph | None, list[FlowError]]:
    try:
        raw = RawFlow.model_validate(data)
    except ValidationError as exc:
        return None, [FlowError(None, "invalid_shape", _pydantic_message(exc))]

    errors: list[FlowError] = []
    nodes: dict[str, Node] = {}
    seen_codes: dict[str, str] = {}
    seen_ids: set[str] = set()

    for rn in raw.nodes:
        if rn.id in seen_ids:
            errors.append(FlowError(rn.id, "duplicate_node_id", f"노드 id '{rn.id}'가 두 번 이상 쓰였습니다"))
            continue
        seen_ids.add(rn.id)
        if not CODE_RE.match(rn.code):
            errors.append(FlowError(rn.id, "invalid_code", f"N-코드 '{rn.code}'는 'N-01' 꼴이어야 합니다"))
        elif rn.code in seen_codes:
            errors.append(
                FlowError(rn.id, "duplicate_code", f"N-코드 '{rn.code}'를 노드 '{seen_codes[rn.code]}'가 이미 씁니다")
            )
        else:
            seen_codes[rn.code] = rn.id
        try:
            kind = NodeKind(rn.kind)
        except ValueError:
            errors.append(FlowError(rn.id, "unknown_kind", f"알 수 없는 노드 종류 '{rn.kind}'"))
            continue
        try:
            config = NODE_CONFIG_MODELS[kind].model_validate(rn.config)
        except ValidationError as exc:
            errors.append(FlowError(rn.id, "invalid_config", _pydantic_message(exc)))
            continue
        nodes[rn.id] = Node(rn.id, kind, rn.code, rn.name, rn.position, config)

    known_ids = {rn.id for rn in raw.nodes}
    starts = [n for n in raw.nodes if n.kind == NodeKind.START]
    if len(starts) != 1:
        errors.append(FlowError(None, "start_count", f"시작 노드는 정확히 하나여야 합니다(지금 {len(starts)}개)"))

    edges: list[Edge] = []
    used: set[tuple[str, str]] = set()
    for e in raw.edges:
        missing = [nid for nid in (e.source, e.target) if nid not in known_ids]
        if missing:
            owner = e.source if e.source in known_ids else None
            errors.append(FlowError(owner, "dangling_edge", f"선 {e.source}→{e.target}이 없는 노드 {missing}를 가리킵니다"))
            continue
        src, dst = nodes.get(e.source), nodes.get(e.target)
        if src is None or dst is None:
            continue  # 노드 자체 오류로 이미 보고됨
        if dst.kind is NodeKind.START:
            errors.append(FlowError(e.source, "edge_into_start", "시작 노드로 들어오는 선은 둘 수 없습니다"))
            continue
        if e.handle not in src.config.handles():
            allowed = ", ".join(src.config.handles()) or "(없음)"
            errors.append(FlowError(e.source, "unknown_handle", f"갈래 '{e.handle}'는 이 노드에 없습니다(가능: {allowed})"))
            continue
        if (e.source, e.handle) in used:
            errors.append(FlowError(e.source, "duplicate_handle", f"갈래 '{e.handle}'에서 선이 두 번 이상 나갑니다"))
            continue
        used.add((e.source, e.handle))
        edges.append(e)

    for node in nodes.values():
        for handle in node.config.required_handles():
            if (node.id, handle) not in used:
                errors.append(FlowError(node.id, "empty_branch", f"갈래 '{handle}'에 이어진 선이 없습니다"))

    graph = FlowGraph(nodes=nodes, edges=tuple(edges))
    if len(starts) == 1 and starts[0].id in nodes:
        errors.extend(_reachability_errors(graph, starts[0].id))
    errors.extend(_cycle_errors(graph))
    return (None if errors else graph), errors


def _reachability_errors(graph: FlowGraph, start_id: str) -> list[FlowError]:
    seen = {start_id}
    stack = [start_id]
    while stack:
        for nxt in graph.outgoing(stack.pop()).values():
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return [
        FlowError(nid, "unreachable_node", "시작 노드에서 이 노드로 가는 길이 없습니다")
        for nid in graph.nodes
        if nid not in seen
    ]


def _cycle_errors(graph: FlowGraph) -> list[FlowError]:
    """턴 경계(방문자 답을 기다리는 노드)를 지나지 않는 순환을 찾는다.

    경계 노드에서 나가는 선을 끊은 그래프에 순환이 남으면, 한 턴 안에서 끝없이 도는 길이다.
    """
    adj: dict[str, list[str]] = {
        nid: ([] if node.config.waits_for_reply() else list(graph.outgoing(nid).values()))
        for nid, node in graph.nodes.items()
    }
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(adj, white)
    reported: list[FlowError] = []

    def visit(root: str) -> None:
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            nid, idx = stack.pop()
            if idx == 0:
                color[nid] = grey
                path.append(nid)
            succ = adj[nid]
            if idx < len(succ):
                stack.append((nid, idx + 1))
                nxt = succ[idx]
                if color[nxt] == grey:
                    cycle = path[path.index(nxt):]
                    reported.append(
                        FlowError(
                            nxt,
                            "cycle_without_turn_boundary",
                            "순환 " + "→".join([*cycle, nxt]) + "이 방문자 답을 기다리는 노드"
                            "(await_reply가 켜진 AI 답변)를 지나지 않습니다",
                        )
                    )
                elif color[nxt] == white:
                    stack.append((nxt, 0))
            else:
                color[nid] = black
                path.pop()

    for nid in adj:
        if color[nid] == white:
            visit(nid)
    return reported


def validate_flow(data: object) -> list[FlowError]:
    """흐름 JSON을 검증해 오류 목록을 돌려준다. 빈 목록이면 유효하다."""
    return _build(data)[1]


def parse_flow(data: object) -> FlowGraph:
    """흐름 JSON을 검증하고 ``FlowGraph``로 돌려준다. 유효하지 않으면 ``FlowValidationError``."""
    graph, errors = _build(data)
    if graph is None:
        raise FlowValidationError(errors)
    return graph
