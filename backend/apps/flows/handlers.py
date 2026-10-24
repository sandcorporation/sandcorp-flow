"""노드 종류별 핸들러(#23). 기존 챗 노드 함수(apps/agent/nodes.py)를 불러 재사용한다 — 옮기지 않는다.

- 지식 검색: ``local_search_node``.
- AI 답변: ``call_llm_structured``(넘김 갈래에 선이 있고 이 턴에 넘김이 가능할 때) 또는 ``call_llm_plain``. 검색한 턴에
  근거가 모자라면 ``source_search_node``로 원문을 보태 한 번 더 묻는다(build_graph의 원문 폴백과 같다). LLM 클라이언트는
  만들지 않는다 — 기존 함수가 ContextVar의 ``chat_provider``를 꺼내 쓴다.
- 사람 넘김: ``create_escalation_node``(``hitl_enabled``가 거짓이면 넘기지 않고 턴을 끝낸다).
"""

from __future__ import annotations

from typing import cast

from apps.agent.nodes import (
    call_llm_plain,
    call_llm_structured,
    create_escalation_node,
    local_search_node,
    source_search_node,
)
from apps.flows.contract import FlowState, NodeContext, NodeOutcome, register
from apps.flows.schema import DEFAULT_HANDLE, HANDOFF_HANDLE, AiAnswerConfig, HandoffConfig, KnowledgeSearchConfig, NodeKind


@register(NodeKind.START)
async def start(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    return NodeOutcome(update={})


@register(NodeKind.KNOWLEDGE_SEARCH)
async def knowledge_search(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(KnowledgeSearchConfig, ctx.node.config)
    update = await local_search_node({**state, "search_top_k": config.top_k})
    return NodeOutcome(update={**update, "rag_searched": True})


@register(NodeKind.AI_ANSWER)
async def ai_answer(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(AiAnswerConfig, ctx.node.config)
    can_hand_off = state.get("hitl_enabled", False) and HANDOFF_HANDLE in ctx.outgoing
    call = call_llm_structured if can_hand_off else call_llm_plain

    working: dict[str, object] = dict(state)
    update: dict[str, object] = await call(working)
    if state.get("rag_searched") and not update.get("context_sufficient", True) and not state.get("source_text_tried"):
        working.update(update)
        added = await source_search_node(working)
        working.update(added)
        update = {**added, **await call(working)}

    if can_hand_off and update.get("needs_hitl"):
        return NodeOutcome(update=update, handle=HANDOFF_HANDLE)
    return NodeOutcome(update=update, handle=DEFAULT_HANDLE, wait_for_reply=config.await_reply)


@register(NodeKind.HANDOFF)
async def handoff(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    if not state.get("hitl_enabled", False):
        return NodeOutcome(update={}, handle=None)
    config = cast(HandoffConfig, ctx.node.config)
    working = dict(state)
    if not working.get("hitl_reason") and config.reason:
        working["hitl_reason"] = config.reason
    update = await create_escalation_node(working)
    return NodeOutcome(update={**update, "needs_hitl": True, "hitl_reason": working.get("hitl_reason", "")},
                       handle=None, saved=True)
