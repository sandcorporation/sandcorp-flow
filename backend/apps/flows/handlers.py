"""노드 종류별 핸들러(#23). 기존 챗 노드 함수(apps/agent/nodes.py)를 불러 재사용한다 — 옮기지 않는다.

- 지식 검색: ``local_search_node``.
- AI 답변: ``call_llm_structured``(넘김 갈래에 선이 있고 이 턴에 넘김이 가능할 때) 또는 ``call_llm_plain``. 검색한 턴에
  근거가 모자라면 ``source_search_node``로 원문을 보태 한 번 더 묻는다(옛 build_graph의 원문 폴백과 같다). LLM 클라이언트는
  만들지 않는다 — 기존 함수가 ContextVar의 ``chat_provider``를 꺼내 쓴다.
- AI 분류·추출: ``apps.flows.classify`` — 같은 Provider 경로로 구조화 출력을 받아 상태의 ``label``·``fields``·``confidence``에 쓴다.
- 조건: ``when`` 식(``apps.flows.expr``)을 위에서부터 계산해 처음 참인 갈래로, 없으면 ``else``.
- 사람 넘김: ``create_escalation_node``(``hitl_enabled``가 거짓이면 넘기지 않고 턴을 끝낸다.
  ``dry_run``(시험 한 턴)이면 넘긴 것으로만 적고 Escalation을 만들지 않는다).
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
from apps.flows.classify import classify as run_classify
from apps.flows.contract import FlowState, NodeContext, NodeOutcome, register
from apps.flows.expr import compile_when
from apps.flows.schema import (
    DEFAULT_HANDLE,
    ELSE_HANDLE,
    HANDOFF_HANDLE,
    AiAnswerConfig,
    AiClassifyConfig,
    ConditionConfig,
    HandoffConfig,
    KnowledgeSearchConfig,
    NodeKind,
)


@register(NodeKind.START)
async def start(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    return NodeOutcome(update={})


@register(NodeKind.KNOWLEDGE_SEARCH)
async def knowledge_search(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(KnowledgeSearchConfig, ctx.node.config)
    update = await local_search_node({**state, "search_top_k": config.top_k})
    chunks = update.get("rag_chunks") or []
    return NodeOutcome(
        update={**update, "rag_searched": True},
        record={"input": state.get("user_message", ""), "output": {"chunks": len(chunks)}, "top_k": config.top_k,
                "reason": f"지식 {len(chunks)}건을 찾음" if chunks else "찾은 지식이 없음"},
    )


@register(NodeKind.AI_ANSWER)
async def ai_answer(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(AiAnswerConfig, ctx.node.config)
    can_hand_off = state.get("hitl_enabled", False) and HANDOFF_HANDLE in ctx.outgoing
    call = call_llm_structured if can_hand_off else call_llm_plain

    working: dict[str, object] = {**state, "node_instructions": config.instructions}
    update: dict[str, object] = await call(working)
    source_fallback = False
    if state.get("rag_searched") and not update.get("context_sufficient", True) and not state.get("source_text_tried"):
        source_fallback = True
        working.update(update)
        added = await source_search_node(working)
        working.update(added)
        update = {**added, **await call(working)}

    record: dict[str, object] = {
        "input": state.get("user_message", ""), "output": update.get("assistant_response", ""),
        "source_fallback": source_fallback,
    }
    if can_hand_off and update.get("needs_hitl"):
        record["reason"] = f"사람 넘김이 필요하다고 판단: {update.get('hitl_reason') or '이유 없음'}"
        return NodeOutcome(update=update, handle=HANDOFF_HANDLE, record=record)
    record["reason"] = "답변 완료" + (" (원문을 보태 다시 물음)" if source_fallback else "")
    return NodeOutcome(update=update, handle=DEFAULT_HANDLE, wait_for_reply=config.await_reply, record=record)


@register(NodeKind.AI_CLASSIFY)
async def ai_classify(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(AiClassifyConfig, ctx.node.config)
    user_message = state.get("user_message", "")
    try:
        result = await run_classify(config, list(state.get("messages", [])), user_message)
    except Exception as exc:  # noqa: BLE001 — 모델·파싱 실패가 턴을 깨지 않게: 확신도 0으로 이어 가 조건의 문턱 갈래가 받는다
        return NodeOutcome(
            update={"label": "", "fields": {f.name: None for f in config.fields}, "confidence": 0.0},
            record={"input": user_message, "output": {"label": "", "confidence": 0.0},
                    "missing": [f.name for f in config.fields],
                    "reason": f"분류 호출 실패({type(exc).__name__}) — 확신도 0으로 이어 감"},
        )
    out: dict[str, object] = {"confidence": result.confidence}
    if config.labels:
        out["label"] = result.label
    if config.fields:
        out["fields"] = result.fields
    parts = [f"라벨 '{result.label}'"] if config.labels else []
    if config.fields:
        parts.append(f"필드 {len(config.fields) - len(result.missing)}/{len(config.fields)}개 추출")
    return NodeOutcome(
        update={"label": result.label if config.labels else "", "fields": result.fields,
                "confidence": result.confidence},
        record={"input": user_message, "output": out, "missing": result.missing,
                "reason": f"{', '.join(parts)}, 확신도 {result.confidence:.2f}"},
    )


@register(NodeKind.CONDITION)
async def condition(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(ConditionConfig, ctx.node.config)
    scope: dict[str, object] = {
        "label": state.get("label") or None,
        "confidence": state.get("confidence"),
        "fields": state.get("fields") or {},
        "user_message": state.get("user_message", ""),
    }
    checked: list[dict[str, object]] = []
    for branch in config.branches:
        matched = compile_when(branch.when).evaluate(scope)
        checked.append({"name": branch.name, "when": branch.when, "matched": matched})
        if matched:
            return NodeOutcome(update={}, handle=branch.name, record={
                "input": scope, "branch": branch.name, "checked": checked,
                "reason": f"'{branch.when}'이(가) 참이라 '{branch.name}' 갈래로"})
    return NodeOutcome(update={}, handle=ELSE_HANDLE, record={
        "input": scope, "branch": ELSE_HANDLE, "checked": checked,
        "reason": "참인 갈래가 없어 기본(else) 갈래로"})


@register(NodeKind.HANDOFF)
async def handoff(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    if not state.get("hitl_enabled", False):
        return NodeOutcome(update={}, handle=None, record={"reason": "사람 넘김이 꺼져 있어 넘기지 않음"})
    config = cast(HandoffConfig, ctx.node.config)
    working = dict(state)
    if not working.get("hitl_reason") and config.reason:
        working["hitl_reason"] = config.reason
    if state.get("dry_run", False):  # 시험 한 턴(#76): 넘겼다고만 적고 Escalation·알림은 만들지 않는다
        return NodeOutcome(update={"needs_hitl": True, "hitl_reason": working.get("hitl_reason", "")},
                           handle=None,
                           record={"dry_run": True, "reason": f"넘김(시험): {working.get('hitl_reason') or config.reason or '이유 없음'}"})
    update = await create_escalation_node(working)
    return NodeOutcome(update={**update, "needs_hitl": True, "hitl_reason": working.get("hitl_reason", "")},
                       handle=None, saved=True,
                       record={"reason": f"넘김: {working.get('hitl_reason') or '이유 없음'}"})
