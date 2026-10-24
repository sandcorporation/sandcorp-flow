"""실행기: Flow Version의 흐름 JSON을 LangGraph ``StateGraph``로 지어 한 턴을 돌린다(#23, ADR-0027).

- 흐름 노드 하나 = LangGraph 노드 하나. 노드 종류별 핸들러(``contract.HANDLERS``)가 상태를 바꾸고 갈래를 고르면,
  실행기가 ``FlowGraph.outgoing``으로 다음 노드를 정한다. 갈래에 선이 없으면 턴이 끝난다.
- 턴 끝(``FINISH_NODE``)에서 기존 ``save_messages_node``로 대화를 저장한다. 사람 넘김처럼 핸들러가 이미 저장했으면
  (``NodeOutcome.saved``) 바로 끝낸다 — 옛 build_graph(#72에서 제거)의 save_messages/create_escalation 종단과 같다.
- ``await_reply`` AI 답변 뒤에는 ``resume_node``에 다음 노드를 적어 두고 턴을 끝낸다. 다음 턴은 거기서 시작한다
  (체크포인터가 상태를 이어 준다).
- 한 턴 노드 상한 ``MAX_STEPS_PER_TURN``을 넘으면 그 자리에서 턴을 끝낸다(``step_limit_exceeded``).
- 지은 ``StateGraph``는 흐름 해시(``flow_hash``)로 캐시한다. 체크포인터는 실행마다 달라 compile은 실행 때 한다.
"""

# langgraph add_node / add_conditional_edges의 느슨한 시그니처 타입 — 런타임은 정상.
# pyright: reportArgumentType=false

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from apps.flows import handlers as _handlers  # noqa: F401 — 레지스트리를 채운다
from apps.flows.contract import (
    HANDLERS,
    FlowState,
    NodeContext,
    NodeHandler,
    NodeOutcome,
    TraceEntry,
)
from apps.usage.context import collect_calls
from apps.usage.pricing import cost_usd
from apps.flows.schema import JSON, MAX_STEPS_PER_TURN, FlowGraph, Node, NodeKind, flow_hash, parse_flow

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from apps.chat.models import ChatSession

__all__ = [
    "FINISH_NODE",
    "HANDLERS",
    "FlowState",
    "NodeContext",
    "NodeKind",
    "NodeOutcome",
    "UnsupportedNodeKind",
    "compile_flow",
    "flow_builder",
    "run_flow_turn_async",
]

FINISH_NODE = "__flow_finish__"
"""턴 끝 저장 노드. 흐름 노드 id와 겹치면 지을 때 거부한다."""

RECURSION_LIMIT = MAX_STEPS_PER_TURN + 10
"""LangGraph 슈퍼스텝 상한. 노드 상한은 실행기가 먼저 걸고, 이것은 그 뒤의 안전망이다."""

_BUILDER_CACHE_MAX = 256
_builders: dict[str, StateGraph] = {}


class UnsupportedNodeKind(ValueError):
    """핸들러가 아직 없는 노드 종류가 흐름에 있다."""


def _dec_str(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _str_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _dec_str(value)


def _make_step(node: Node, handler: NodeHandler, outgoing: dict[str, str]):
    ctx = NodeContext(node=node, outgoing=outgoing)

    async def step(state: FlowState) -> dict[str, object]:
        steps = state.get("steps", 0)
        if steps >= MAX_STEPS_PER_TURN:
            return {"route": FINISH_NODE, "resume_node": "", "step_limit_exceeded": True}

        started = time.monotonic()
        with collect_calls() as llm_calls:
            outcome: NodeOutcome = await handler(state, ctx)
        duration_ms = int((time.monotonic() - started) * 1000)
        target = outgoing.get(outcome.handle) if outcome.handle else None
        resume = ""
        if outcome.wait_for_reply:
            resume, target = target or "", None
        route = target or (END if outcome.saved else FINISH_NODE)
        calls = [
            {"model": c.model, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
             "cost_usd": _str_or_none(cost_usd(c.model, c.input_tokens, c.output_tokens))}
            for c in llm_calls
        ]
        known = [Decimal(str(c["cost_usd"])) for c in calls if c["cost_usd"] is not None]
        entry: TraceEntry = {
            "node_id": node.id, "kind": node.kind.value, "handle": outcome.handle, "record": outcome.record,
            "duration_ms": duration_ms,
            "input_tokens": sum(c.input_tokens for c in llm_calls),
            "output_tokens": sum(c.output_tokens for c in llm_calls),
            # 호출이 없으면 비용 0, 모르는 모델이 한 번이라도 있으면 모름(None)
            "cost_usd": None if len(known) < len(calls) else _dec_str(sum(known, Decimal(0))),
            "calls": calls,
        }
        return {
            **outcome.update,
            "route": route,
            "resume_node": resume,
            "steps": steps + 1,
            "trace": [*state.get("trace", []), entry],
        }

    return step


async def _finish(state: FlowState) -> dict[str, object]:
    from apps.agent.nodes import save_messages_node

    return await save_messages_node(dict(state))


def _route(state: FlowState) -> str:
    return state["route"]


def _build(flow: FlowGraph) -> StateGraph:
    missing = sorted({n.kind.value for n in flow.nodes.values() if n.kind not in HANDLERS})
    if missing:
        raise UnsupportedNodeKind(f"실행기가 아직 돌릴 수 없는 노드 종류: {', '.join(missing)}")
    if FINISH_NODE in flow.nodes:
        raise UnsupportedNodeKind(f"노드 id '{FINISH_NODE}'는 실행기 예약어입니다")

    graph = StateGraph(FlowState)
    targets: dict[str, str] = {nid: nid for nid in flow.nodes}
    targets[FINISH_NODE] = FINISH_NODE
    targets[END] = END

    for node in flow.nodes.values():
        graph.add_node(node.id, _make_step(node, HANDLERS[node.kind], flow.outgoing(node.id)))
        graph.add_conditional_edges(node.id, _route, targets)
    graph.add_node(FINISH_NODE, _finish)
    graph.add_edge(FINISH_NODE, END)

    start_id = flow.start.id

    def _entry(state: FlowState) -> str:
        resume = state.get("resume_node") or ""
        return resume if resume in flow.nodes else start_id

    graph.add_conditional_edges(START, _entry, {nid: nid for nid in flow.nodes})
    return graph


def flow_builder(graph_json: JSON) -> StateGraph:
    """흐름 JSON → 지은 ``StateGraph``(해시로 캐시). 무효면 ``FlowValidationError``."""
    key = flow_hash(graph_json)
    builder = _builders.get(key)
    if builder is None:
        builder = _build(parse_flow(graph_json))
        if len(_builders) >= _BUILDER_CACHE_MAX:
            _builders.clear()
        _builders[key] = builder
    return builder


def compile_flow(graph_json: JSON, checkpointer: BaseCheckpointSaver | None = None):
    return flow_builder(graph_json).compile(checkpointer=checkpointer)


def turn_input(base: dict[str, object], hitl_enabled: bool, *, dry_run: bool = False) -> dict[str, object]:
    """기존 챗 입력(``_load_chat_inputs``의 initial_state)에 실행기 턴 키를 더한다.

    ``resume_node``는 넣지 않는다 — 체크포인트에 남은 재개 지점을 덮지 않게.
    """
    return {
        **base,
        "hitl_enabled": hitl_enabled,
        "dry_run": dry_run,
        "route": "",
        "steps": 0,
        "step_limit_exceeded": False,
        "rag_searched": False,
        "trace": [],
    }


async def run_flow_turn_async(
    session: ChatSession, user_message: str, graph_json: JSON, *, dry_run: bool = False
) -> FlowState:
    """세션 한 턴을 흐름으로 돌린다. 입력·Provider·사용량 맥락은 기존 챗 경로와 같은 함수로 만든다.

    LLM 클라이언트는 노드가 만들지 않는다 — ``chat_provider(config)``로 고른 Provider를 ContextVar에 넣고,
    AI 답변 핸들러가 부르는 기존 ``call_llm_*``이 그것을 꺼내 쓴다. 체험 턴 차감은 이 함수를 부르는 쪽
    (chat_task의 ``trial.admit_turn``)이 맡는다. 운영 진입은 ``apps.flows.chat_turn``(#72).

    ``dry_run=True``(시험 한 턴, #76)면 갈래는 운영과 똑같이 고르되 사람 넘김 노드가 실제로 넘기지 않는다 —
    Escalation·웹훅·상담원/방문자 SSE가 생기지 않고, trace에 넘김 노드가 남고 ``needs_hitl``이 참이 된다.
    """
    from asgiref.sync import sync_to_async
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from apps.agent.graph import _build_conninfo, _load_chat_inputs
    from apps.flows.trace import save_decision_trace
    from apps.agent.providers import set_chat_provider
    from apps.usage.context import set_usage_context

    initial_state, effective_hitl, provider = await sync_to_async(_load_chat_inputs)(session, user_message)
    set_chat_provider(provider)
    set_usage_context(session.tenant_id, "chat", session_id=session.id)

    started = time.monotonic()
    async with AsyncPostgresSaver.from_conn_string(_build_conninfo()) as saver:
        await saver.setup()
        graph = compile_flow(graph_json, checkpointer=saver)
        result: FlowState = await graph.ainvoke(
            turn_input(dict(initial_state), effective_hitl, dry_run=dry_run),
            config={"configurable": {"thread_id": str(session.id)}, "recursion_limit": RECURSION_LIMIT},
        )
    # 판단 기록: 운영 턴·시험 턴이 같은 자리에서 한 번의 insert로 남긴다(저장 실패는 턴을 깨지 않는다)
    await sync_to_async(save_decision_trace)(session, result.get("trace", []), int((time.monotonic() - started) * 1000))
    return result
