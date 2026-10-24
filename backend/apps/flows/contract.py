"""실행기와 노드 핸들러 사이의 계약(#23, ADR-0027).

핸들러는 노드 종류마다 하나다. 상태(``FlowState``)를 읽고, 바꿀 키(``NodeOutcome.update``)와 나갈 갈래
(``NodeOutcome.handle``)를 돌려준다. 다음 노드를 고르는 일·턴을 끝내는 일·노드 상한은 실행기가 한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from typing_extensions import TypedDict

from apps.agent.graph import ChatState
from apps.flows.schema import DEFAULT_HANDLE, Node, NodeKind


class TraceEntry(TypedDict):
    """한 턴에 지난 노드 하나 — 판단 기록(Decision Trace)의 항목(#24).

    ``record``는 핸들러가 채우는 노드별 입력·출력·분기 이유(``NodeOutcome.record``). 시간·토큰·비용은 실행기가 잰다.
    ``cost_usd``는 USD 소수 문자열이고, 단가 표에 없는 모델의 호출이 있으면 None.
    """

    node_id: str
    kind: str
    handle: str | None
    record: dict[str, object]
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: str | None
    calls: list[dict[str, object]]
    """노드 안에서 일어난 LLM 호출 ``{model, input_tokens, output_tokens, cost_usd}``."""


class FlowState(ChatState):
    """기존 챗 상태(``ChatState``)에 실행기 키를 더한 것.

    - ``hitl_enabled``: 이 턴에 사람 넘김이 가능한가(테넌트 설정 ∧ 상담시간). 거짓이면 AI 답변은 넘김 갈래를 쓰지
      않고, 사람 넘김 노드는 넘기지 않고 턴을 끝낸다.
    - ``dry_run``: 시험 한 턴(#76). 사람 넘김 노드가 Escalation·알림 없이 넘겼다고만 적는다.
    - ``resume_node``: 다음 턴을 시작할 노드 id(``await_reply`` AI 답변 뒤). 비면 시작 노드부터.
    - ``route``: 실행기 내부 — 방금 노드가 고른 다음 노드 id.
    - ``steps``·``step_limit_exceeded``: 한 턴 노드 상한(``MAX_STEPS_PER_TURN``).
    - ``rag_searched``: 이 턴에 지식 검색을 했는가(원문 폴백은 검색을 한 턴에만).
    - ``trace``: 이 턴에 지난 노드들.
    - ``node_instructions``: 지금 도는 AI 답변 노드의 ``instructions``(프롬프트 끝에 실린다). 노드마다 핸들러가 넣는다.
    - ``label``·``fields``·``confidence``: AI 분류·추출 노드의 결과(#25). 조건 노드가 읽는다.
    """

    hitl_enabled: bool
    dry_run: bool
    resume_node: str
    route: str
    steps: int
    step_limit_exceeded: bool
    rag_searched: bool
    trace: list[TraceEntry]
    node_instructions: str
    label: str
    confidence: float
    fields: dict[str, object]


StateUpdate = Mapping[str, object]


@dataclass(frozen=True)
class NodeContext:
    node: Node
    outgoing: Mapping[str, str]
    """handle → 다음 노드 id. 선이 있는 갈래만 들어 있다."""


@dataclass(frozen=True)
class NodeOutcome:
    update: StateUpdate
    handle: str | None = DEFAULT_HANDLE
    """나갈 갈래. None이거나 그 갈래에 선이 없으면 턴이 끝난다."""
    wait_for_reply: bool = False
    """참이면 답을 보낸 뒤 턴을 끝내고, 다음 턴은 ``handle``의 선이 가리키는 노드에서 시작한다."""
    saved: bool = False
    """핸들러가 이미 이 턴의 대화를 저장했다(사람 넘김). 거짓이면 턴 끝에 실행기가 저장한다."""
    record: dict[str, object] = field(default_factory=dict)


NodeHandler = Callable[[FlowState, NodeContext], Awaitable[NodeOutcome]]

HANDLERS: dict[NodeKind, NodeHandler] = {}
"""노드 종류 → 핸들러. 핸들러가 없는 종류가 든 흐름은 지을 때 거부한다."""


def register(kind: NodeKind) -> Callable[[NodeHandler], NodeHandler]:
    def deco(fn: NodeHandler) -> NodeHandler:
        HANDLERS[kind] = fn
        return fn

    return deco
