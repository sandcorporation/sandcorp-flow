"""넘김 턴에 방문자에게 보이는 메시지(#25 운영 관찰) + AI 답변 `instructions`가 프롬프트에 실리는 것.

운영에서 사람 넘김이 결정된 턴에 AI가 「연결해 드릴 수 없습니다」라고 먼저 답한 뒤 넘김 안내가 붙어 모순으로 보였다.
build_graph 시절(#72 이전)에도 같은 call_llm_structured가 response를 needs_hitl보다 먼저 흘렸으므로 그때부터의 동작이다.
"""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async

REFUSAL = "죄송하지만 사람 상담원과 연결해드릴 수는 없습니다."


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


@pytest.fixture
def visitor_wire(monkeypatch):
    """방문자 SSE에 나가는 토큰·done을 기록한다(Redis 발행 경계)."""
    from apps.agent import nodes

    wire = {"tokens": [], "done": 0}

    async def _tok(sid, content):
        wire["tokens"].append(content)

    async def _done(sid):
        wire["done"] += 1

    monkeypatch.setattr(nodes, "apublish_token", _tok)
    monkeypatch.setattr(nodes, "apublish_done", _done)
    return wire


async def _run(tenant, flow, message):
    from apps.chat.models import ChatMessage, ChatSession
    from apps.escalation.models import Escalation
    from apps.flows.executor import run_flow_turn_async

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-turn")
    result = await run_flow_turn_async(session, message, flow)
    saved = await adb(lambda: [m.content for m in ChatMessage.objects.filter(session=session, role="assistant")])()
    esc = await adb(lambda: Escalation.objects.filter(session=session).count())()
    return result, saved, esc


@pytest.mark.django_db(transaction=True)
async def test_handoff_turn_shows_the_visitor_no_model_text(tenant_with_key, fake_chat_llm, visitor_wire):
    """모델이 거절 문구를 쓰면서 needs_hitl을 켜도, 방문자에게는 모델 답이 흐르지도 저장되지도 않는다(넘김 안내만)."""
    from apps.agent.nodes import HITLResponse
    from apps.flows.defaults import DEFAULT_FLOW

    tenant, _ = tenant_with_key
    fake_chat_llm.override = lambda m: HITLResponse(response=REFUSAL, needs_hitl=True, hitl_reason="사람 요청")

    result, saved, esc = await _run(tenant, DEFAULT_FLOW, "사람 상담원 연결해 주세요")

    assert esc == 1 and result["needs_hitl"] is True
    assert visitor_wire == {"tokens": [], "done": 0}  # 스트림에 아무것도 안 나간다
    assert saved == []  # 방문자에게 보일 AI 메시지가 없다
    assert [m["role"] for m in result["messages"]] == ["user"]


@pytest.mark.django_db(transaction=True)
async def test_normal_turn_with_handoff_available_still_streams_the_answer(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.agent.nodes import HITLResponse
    from apps.flows.defaults import DEFAULT_FLOW

    tenant, _ = tenant_with_key
    fake_chat_llm.override = lambda m: HITLResponse(response="영업시간은 9시부터 6시입니다", needs_hitl=False)

    result, saved, esc = await _run(tenant, DEFAULT_FLOW, "영업시간이 어떻게 되나요?")

    assert esc == 0
    assert "".join(visitor_wire["tokens"]) == "영업시간은 9시부터 6시입니다" and visitor_wire["done"] == 1
    assert saved == ["영업시간은 9시부터 6시입니다"]


@pytest.mark.django_db(transaction=True)
async def test_ai_answer_instructions_are_in_the_prompt(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    flow = {"nodes": [_node("s", "start", 1),
                      _node("a", "ai_answer", 2, {"instructions": "빠진 요구사항 하나만 짧게 되묻는다"})],
            "edges": [{"from": "s", "to": "a"}]}

    await _run(tenant, flow, "견적 문의요")

    system = fake_chat_llm.captured[0][1][0]
    assert "빠진 요구사항 하나만 짧게 되묻는다" in str(system.content)


@pytest.mark.django_db(transaction=True)
async def test_instructions_do_not_leak_into_the_next_ai_answer_node(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    flow = {"nodes": [_node("s", "start", 1),
                      _node("a", "ai_answer", 2, {"instructions": "첫 노드 지침", "await_reply": True}),
                      _node("b", "ai_answer", 3)],
            "edges": [{"from": "s", "to": "a"}, {"from": "a", "to": "b"}]}
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-i")
    await run_flow_turn_async(session, "하나", flow)
    await run_flow_turn_async(session, "둘", flow)

    assert "첫 노드 지침" in str(fake_chat_llm.captured[0][1][0].content)
    assert "첫 노드 지침" not in str(fake_chat_llm.captured[1][1][0].content)
