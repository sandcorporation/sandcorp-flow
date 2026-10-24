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

from apps.flows.expr import ExprError, compile_when
from apps.flows.templates import SAVE_NAME_RE, TemplateError, parse_path
from apps.flows.templates import refs as template_refs

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None

DEFAULT_HANDLE = "next"
"""갈래가 하나뿐인 노드의 나가는 선 handle. 선의 ``handle``을 비우면 이 값으로 본다."""

ELSE_HANDLE = "else"
"""조건 노드의 기본 갈래(어느 조건에도 맞지 않을 때)."""

ERROR_HANDLE = "error"
"""HTTP 도구가 실패했을 때(차단·시간 초과·크기·HTTP 오류) 나가는 갈래(선택). 선이 없으면 턴이 끝난다."""

HANDOFF_HANDLE = "handoff"
"""AI 답변이 사람 넘김이 필요하다고 판단할 때 나가는 갈래(선택). 선이 있을 때만 AI가 넘김을 판단한다."""

MAX_STEPS_PER_TURN = 50
"""실행기가 한 턴에 실행할 노드 수 상한(무한 루프 방지 안전장치, ADR-0027 순환 규칙)."""

CODE_RE = re.compile(r"^N-\d{2,}$")
SECRET_REF_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
HEADER_NAME_RE = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}")
RESERVED_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "upgrade", "te"})
"""HTTP 도구가 직접 정하지 못하는 헤더(요청 틀을 깨거나 다른 호스트로 보내는 통로)."""
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

    def handles(self) -> tuple[str, ...]:
        return (DEFAULT_HANDLE, HANDOFF_HANDLE)


class ConditionBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    when: str = Field(min_length=1)
    """갈래를 고르는 식(``apps/flows/expr.py``). 위에서부터 처음 참인 갈래로 간다."""

    @field_validator("when")
    @classmethod
    def _valid_when(cls, when: str) -> str:
        try:
            compile_when(when)
        except ExprError as exc:
            raise ValueError(f"when 식 오류: {exc}") from exc
        return when


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


class ExtractField(BaseModel):
    """추출할 필드 하나. 모델이 못 찾으면 null(누락)로 둔다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    description: str = ""
    type: Literal["string", "number", "boolean"] = "string"


class AiClassifyConfig(NodeConfig):
    """AI 분류·추출(ADR-0027, #25). ``labels``로 분류하고 ``fields``로 추출한다 — 둘 중 하나 이상.

    결과는 상태의 ``label``·``fields``·``confidence``(0~1)에 쓰고, 조건 노드가 읽는다.
    """

    instructions: str = ""
    labels: list[str] = Field(default_factory=list)
    fields: list[ExtractField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _labels_or_fields(self) -> "AiClassifyConfig":
        if not self.labels and not self.fields:
            raise ValueError("labels(분류 라벨)나 fields(추출 필드) 중 하나는 있어야 합니다")
        if len(set(self.labels)) != len(self.labels) or any(not label.strip() for label in self.labels):
            raise ValueError("labels는 비어 있지 않고 중복이 없어야 합니다")
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("fields 이름이 중복됩니다")
        return self


GuardrailRule = Literal["banned_words", "pii", "price_promise"]


DEFAULT_BLOCK_MESSAGE = "죄송합니다. 해당 내용은 안내해 드릴 수 없습니다."


class GuardrailConfig(NodeConfig):
    """가드레일(ADR-0027, #26). ``target``의 글을 ``rules``로 검사하고 걸리면 ``action``으로 처리한다.

    - ``banned_words``(금지어 목록)·``pii``(전화번호·주민등록번호·카드번호·이메일)는 결정적 검사, ``price_promise``는 LLM 판정.
    - ``block``: ``block_message``로 바꿈. ``rewrite``: LLM으로 고쳐 씀(고친 글에도 결정적 규칙이 남으면 막기로). ``mask``: 걸린
      부분만 가림(금지어·개인정보만 — 가격 약속은 부분을 가릴 수 없다).
    - ``output``: 직전 AI 답변(나가는 말), ``input``: 방문자 입력(들어오는 말). 입력을 막으면 ``block_message``로 답하고 턴이 끝난다.
    """

    rules: list[GuardrailRule] = Field(min_length=1)
    action: Literal["block", "rewrite", "mask"] = "rewrite"
    target: Literal["output", "input"] = "output"
    banned_words: list[str] = Field(default_factory=list)
    block_message: str = DEFAULT_BLOCK_MESSAGE

    @field_validator("banned_words")
    @classmethod
    def _words(cls, words: list[str]) -> list[str]:
        cleaned = [w.strip() for w in words]
        if any(not w for w in cleaned):
            raise ValueError("banned_words에 빈 단어를 넣을 수 없습니다")
        return cleaned

    @field_validator("block_message")
    @classmethod
    def _message(cls, message: str) -> str:
        if not message.strip():
            raise ValueError("block_message는 비워 둘 수 없습니다")
        return message

    @model_validator(mode="after")
    def _consistent(self) -> "GuardrailConfig":
        if len(set(self.rules)) != len(self.rules):
            raise ValueError("rules에 같은 규칙이 두 번 있습니다")
        if "banned_words" in self.rules and not self.banned_words:
            raise ValueError("banned_words 규칙을 쓰려면 banned_words 목록이 필요합니다")
        if self.action == "mask" and "price_promise" in self.rules:
            raise ValueError("가격 약속은 부분을 가릴 수 없습니다 — action을 block이나 rewrite로 하세요")
        return self


class HttpToolConfig(NodeConfig):
    """HTTP 도구(ADR-0029, #34). 허용 목록·IP 차단·리다이렉트 검사는 실행 때(``apps.flows.http_tool``).

    - ``url``: https만. 호스트(·포트·사용자 정보)는 틀의 **고정 부분**이어야 한다 — ``{{state.…}}``는 경로·쿼리에만,
      값은 실행 때 한 조각으로 퍼센트 인코딩된다(``/``·``?``·``#``·``.``까지). 비밀 참조는 넣을 수 없다.
    - ``headers``: 고정 글과 ``{{secret.이름}}``만(방문자 값은 안 됨). 인증 계열 이름이면 값은 참조(+스킴 한 단어)만.
    - ``body_template``: ``{{secret.…}}``·``{{state.…}}`` 둘 다. 값은 JSON 문자열 안쪽으로 이스케이프돼 들어간다.
    - ``save``: 상태 ``fields.이름`` ← 응답 JSON 경로(``templates.parse_path``). 조건 노드·다음 HTTP 도구가 읽는다.
    - 나가는 갈래: ``next``(2xx), ``error``(차단·시간 초과·크기·HTTP 오류 등, 선택 — 선이 없으면 턴이 끝난다).
    """

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(min_length=1, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    body_template: str | None = Field(default=None, max_length=16000)
    save: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        if not url.startswith("https://"):
            raise ValueError("url은 https:// 로 시작해야 합니다")
        authority = re.split(r"[/?#]", url[len("https://"):], maxsplit=1)[0]
        if "{{" in authority or "}}" in authority:
            raise ValueError("url의 호스트는 고정해야 합니다 — {{…}} 참조는 경로·쿼리에만 쓸 수 있습니다")
        if "@" in authority:
            raise ValueError("url에 사용자 정보(user@host)를 넣을 수 없습니다")
        if not authority:
            raise ValueError("url에 호스트가 없습니다")
        try:
            found = template_refs(url)
        except TemplateError as exc:
            raise ValueError(f"url: {exc}") from exc
        if any(r.kind == "secret" for r in found):
            raise ValueError("url에는 비밀 참조를 넣을 수 없습니다(헤더에 두세요)")
        return url

    @field_validator("headers")
    @classmethod
    def _secrets_by_reference(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if not HEADER_NAME_RE.fullmatch(name):
                raise ValueError(f"헤더 이름 '{name[:40]}'이(가) 올바르지 않습니다")
            if name.lower() in RESERVED_HEADERS:
                raise ValueError(f"헤더 '{name}'은(는) 직접 정할 수 없습니다")
            if "\r" in value or "\n" in value:
                raise ValueError(f"헤더 '{name}' 값에 줄바꿈을 넣을 수 없습니다")
            try:
                found = template_refs(value)
            except TemplateError as exc:
                raise ValueError(f"헤더 '{name}': {exc}") from exc
            if any(r.kind != "secret" for r in found):
                raise ValueError(f"헤더 '{name}'에는 방문자 값({{{{state.…}}}})을 넣을 수 없습니다(본문·URL 경로에 두세요)")
            if not SENSITIVE_HEADER_RE.search(name):
                continue
            # 인증 계열 헤더 값은 {{secret.이름}} 참조(앞의 스킴 한 단어 허용, 예: "Bearer {{secret.key}}")만.
            rest = SECRET_REF_RE.sub("", value).strip()
            if not SECRET_REF_RE.search(value) or (rest and not re.fullmatch(r"[A-Za-z]+", rest)):
                raise ValueError(f"헤더 '{name}'에는 비밀 원문 대신 {{{{secret.이름}}}} 참조를 넣으세요")
        return headers

    @field_validator("body_template")
    @classmethod
    def _body_refs(cls, body: str | None) -> str | None:
        if body is not None:
            try:
                template_refs(body)
            except TemplateError as exc:
                raise ValueError(f"body_template: {exc}") from exc
        return body

    @field_validator("save")
    @classmethod
    def _save_paths(cls, save: dict[str, str]) -> dict[str, str]:
        for name, path in save.items():
            if not SAVE_NAME_RE.fullmatch(name):
                raise ValueError(f"save 이름 '{name[:40]}'은(는) 영문·숫자·_ 64자까지입니다")
            try:
                parse_path(path)
            except TemplateError as exc:
                raise ValueError(f"save '{name}': {exc}") from exc
        return save

    def handles(self) -> tuple[str, ...]:
        return (DEFAULT_HANDLE, ERROR_HANDLE)


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
    errors.extend(_guardrail_errors(graph))
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


def _guardrail_errors(graph: FlowGraph) -> list[FlowError]:
    """출력 가드레일은 답을 보낸 뒤 턴이 끝나는 AI 답변(await_reply) 바로 뒤에 둘 수 없다 — 검사 전에 답이 이미 나간다."""
    errors: list[FlowError] = []
    for node in graph.nodes.values():
        if node.kind is not NodeKind.AI_ANSWER or not node.config.waits_for_reply():
            continue
        nxt = graph.nodes.get(graph.outgoing(node.id).get(DEFAULT_HANDLE, ""))
        if nxt and nxt.kind is NodeKind.GUARDRAIL and nxt.config.target == "output":  # type: ignore[attr-defined]
            errors.append(FlowError(nxt.id, "guardrail_after_await_reply",
                                    f"'{node.id}'는 답을 보낸 뒤 턴을 끝내므로 출력 가드레일을 바로 붙일 수 없습니다(target을 input으로 하거나 await_reply를 끄세요)"))
    return errors


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
