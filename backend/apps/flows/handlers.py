"""노드 종류별 핸들러(#23). 기존 챗 노드 함수(apps/agent/nodes.py)를 불러 재사용한다 — 옮기지 않는다.

- 지식 검색: ``local_search_node``.
- AI 답변: ``call_llm_structured``(넘김 갈래에 선이 있고 이 턴에 넘김이 가능할 때) 또는 ``call_llm_plain``. 검색한 턴에
  근거가 모자라면 ``source_search_node``로 원문을 보태 한 번 더 묻는다(옛 build_graph의 원문 폴백과 같다). LLM 클라이언트는
  만들지 않는다 — 기존 함수가 ContextVar의 ``chat_provider``를 꺼내 쓴다.
- AI 분류·추출: ``apps.flows.classify`` — 같은 Provider 경로로 구조화 출력을 받아 상태의 ``label``·``fields``·``confidence``에 쓴다.
- 조건: ``when`` 식(``apps.flows.expr``)을 위에서부터 계산해 처음 참인 갈래로, 없으면 ``else``.
- 가드레일: ``apps.flows.guardrail`` — 직전 AI 답변(나가는 말)이나 방문자 입력(들어오는 말)을 검사해 막기·다시 쓰기·가리기.
  뒤에 출력 가드레일이 붙은 AI 답변은 답을 스트림에 흘리지 않고 모아 두었다가(``stream_held``) 가드레일이 통과시킨 글을 내보낸다.
- HTTP 도구: ``apps.flows.http_tool`` — 테넌트 허용 목록·safe_http로 부르고 2xx면 ``save`` 값을 상태 ``fields``에 더한 뒤
  ``next``, 실패(차단·시간·크기·HTTP 오류 등)면 ``error`` 갈래(선이 없으면 턴 끝). 이유 코드는 ``record.error``.
- 사람 넘김: ``create_escalation_node``(``hitl_enabled``가 거짓이면 넘기지 않고 턴을 끝낸다.
  ``dry_run``(시험 한 턴)이면 넘긴 것으로만 적고 Escalation을 만들지 않는다).
"""

from __future__ import annotations

import uuid
from typing import cast

from apps.agent.nodes import (
    publish_answer,
    call_llm_plain,
    call_llm_structured,
    create_escalation_node,
    local_search_node,
    source_search_node,
)
from apps.flows import guardrail as checks
from apps.flows import http_tool as http
from apps.flows.classify import classify as run_classify
from apps.flows.contract import FlowState, NodeContext, NodeOutcome, register
from apps.flows.expr import compile_when
from apps.flows.schema import (
    DEFAULT_HANDLE,
    ELSE_HANDLE,
    ERROR_HANDLE,
    HANDOFF_HANDLE,
    AiAnswerConfig,
    AiClassifyConfig,
    ConditionConfig,
    GuardrailConfig,
    HandoffConfig,
    HttpToolConfig,
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

    working: dict[str, object] = {**state, "node_instructions": config.instructions, "hold_stream": ctx.hold_stream}
    update: dict[str, object] = await call(working)
    source_fallback = False
    if state.get("rag_searched") and not update.get("context_sufficient", True) and not state.get("source_text_tried"):
        source_fallback = True
        working.update(update)
        added = await source_search_node(working)
        working.update(added)
        update = {**added, **await call(working)}

    update = {**update, "stream_held": ctx.hold_stream}
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


@register(NodeKind.HTTP_TOOL)
async def http_tool(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(HttpToolConfig, ctx.node.config)
    fields = dict(state.get("fields") or {})
    scope = http.Scope(user_message=state.get("user_message", ""), label=state.get("label") or "",
                       confidence=state.get("confidence"), fields=fields)
    result = await http.call(uuid.UUID(str(state["tenant_id"])), config, scope)  # 가림 목록 키는 UUID(기록 쪽과 같게)
    record: dict[str, object] = {
        "request": {"method": config.method, "url": result.url}, "status": result.status, "error": result.error,
        "output": result.saved, "missing": result.missing,
    }
    update = {"fields": {**fields, **result.saved}}
    if result.error is not None:
        why = f" — {result.detail}" if result.detail else ""
        record["reason"] = f"호출 실패({result.error}){why} → error 갈래"
        return NodeOutcome(update=update, handle=ERROR_HANDLE, record=record)
    saved = len(result.saved) - len(result.missing)
    record["reason"] = f"HTTP {result.status} 응답" + (f", 값 {saved}/{len(result.saved)}개 저장" if result.saved else "")
    return NodeOutcome(update=update, handle=DEFAULT_HANDLE, record=record)


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


@register(NodeKind.GUARDRAIL)
async def guardrail(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    config = cast(GuardrailConfig, ctx.node.config)
    incoming = config.target == "input"
    text = state.get("user_message", "") if incoming else state.get("assistant_response", "")
    held = bool(state.get("stream_held", False)) and not incoming
    sid = str(state["session_id"])

    async def finish(new_text: str, record: dict[str, object], *, end_turn: bool = False) -> NodeOutcome:
        """처리 결과를 상태에 쓰고, 모아 둔 답이면 이제 방문자 스트림에 내보낸다."""
        if incoming:
            update: dict[str, object] = {"user_message": new_text}
            if end_turn:  # 막힌 입력: 정해 둔 문구로 답하고 턴을 끝낸다. 막힌 원문은 대화 기록에도 남기지 않는다
                await publish_answer(sid, config.block_message)
                update = {"user_message": "(차단된 입력)", "assistant_response": config.block_message}
            return NodeOutcome(update=update, handle=None if end_turn else DEFAULT_HANDLE, record=record)
        if held and new_text:
            await publish_answer(sid, new_text)
        return NodeOutcome(update={"assistant_response": new_text, "stream_held": False}, record=record)

    if not text.strip():
        return await finish(text, {"target": config.target, "hits": [], "reason": "검사할 글이 없어 통과"})

    hits: list[dict[str, str]] = checks.deterministic_hits(text, list(config.rules), config.banned_words)
    judge_failed = False
    if "price_promise" in config.rules:
        try:
            verdict = await checks.judge_price_promise(text)
            if verdict.violates:
                hits.append({"rule": "price_promise", "reason": verdict.reason})
        except Exception as exc:  # noqa: BLE001 — 판정을 못 하면 통과시키지 않는다(닫힌 쪽으로)
            judge_failed = True
            hits.append({"rule": "price_promise", "reason": f"판정 실패({type(exc).__name__})"})

    record: dict[str, object] = {"target": config.target, "rules": list(config.rules), "hits": hits}
    if not hits:
        return await finish(text, {**record, "reason": "걸린 규칙 없음 — 그대로 통과"})
    record["action"] = config.action
    caught = ", ".join(sorted({str(h["rule"]) for h in hits}))
    if judge_failed:
        record["judge_failed"] = True
        caught += "(가격 약속 판정 실패 — 통과시키지 않음)"
    if not held and not incoming:
        record["late"] = True  # 이 답은 이미 방문자에게 나갔다 — 저장된 글만 바뀐다

    if config.action == "mask":
        return await finish(checks.mask(text, list(config.rules), config.banned_words),
                            {**record, "reason": f"{caught}에 걸려 걸린 부분을 가림"})
    if config.action == "rewrite":
        try:
            rewritten = await checks.rewrite(text, list(config.rules), config.banned_words, incoming=incoming)
        except Exception as exc:  # noqa: BLE001 — 고쳐 쓰지 못하면 원문을 내보내지 않고 막는다
            return await finish(config.block_message, {
                **record, "action": "block",
                "reason": f"{caught}에 걸림 — 다시 쓰기 실패({type(exc).__name__})로 막음"}, end_turn=incoming)
        if rewritten and not checks.deterministic_hits(rewritten, list(config.rules), config.banned_words):
            return await finish(rewritten, {**record, "reason": f"{caught}에 걸려 LLM으로 다시 씀"})
        return await finish(config.block_message, {
            **record, "action": "block", "reason": f"{caught}에 걸림 — 다시 쓴 결과에도 규칙이 남거나 비어 막음"},
            end_turn=incoming)
    return await finish(config.block_message, {**record, "reason": f"{caught}에 걸려 막음"}, end_turn=incoming)
