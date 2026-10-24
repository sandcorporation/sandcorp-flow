"""첫 핸들러 넷(시작·지식 검색·AI 답변·사람 넘김)과 기본 흐름(#23).

기본 흐름은 운영 챗(build_graph)과 **같은 동작**이어야 한다(#72가 갈아 끼울 때 회귀가 없게) — 같은 Fake LLM 입력에
같은 답·같은 HITL 분기·같은 저장을 비교한다. LLM은 결정적 Fake, DB·임베딩·체크포인터는 실제 객체.
"""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _snapshot(session_id):
    from apps.chat.models import ChatMessage, ChatSession
    from apps.escalation.models import Escalation

    session = ChatSession.objects.get(id=session_id)
    messages = [(m.role, m.content) for m in ChatMessage.objects.filter(session=session).order_by("created_at")]
    escalations = [(e.trigger_type, e.reason, e.status) for e in Escalation.objects.filter(session=session)]
    return session.is_hitl, messages, escalations


async def _old_and_new(tenant, message, fake):
    """같은 입력을 기존 build_graph 경로와 기본 흐름 실행기로 한 번씩 돌려 결과를 나란히 돌려준다."""
    from apps.agent.graph import run_chat_agent_async
    from apps.chat.models import ChatSession
    from apps.flows.defaults import DEFAULT_FLOW
    from apps.flows.executor import run_flow_turn_async

    old = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-old")
    fake.captured.clear()
    old_answer = await run_chat_agent_async(old, message)
    old_schemas = [name for name, _ in fake.captured]

    new = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-new")
    fake.captured.clear()
    result = await run_flow_turn_async(new, message, DEFAULT_FLOW)
    new_schemas = [name for name, _ in fake.captured]

    return (
        (old_answer, old_schemas, await adb(_snapshot)(old.id)),
        (result["assistant_response"], new_schemas, await adb(_snapshot)(new.id)),
        result,
    )


def test_default_flow_is_valid_and_shaped_start_search_answer_handoff():
    from apps.flows.defaults import DEFAULT_FLOW
    from apps.flows.schema import HANDOFF_HANDLE, NodeKind, parse_flow

    flow = parse_flow(DEFAULT_FLOW)
    kinds = [n.kind for n in flow.nodes.values()]
    assert kinds == [NodeKind.START, NodeKind.KNOWLEDGE_SEARCH, NodeKind.AI_ANSWER, NodeKind.HANDOFF]
    answer = next(n for n in flow.nodes.values() if n.kind is NodeKind.AI_ANSWER)
    handoff = next(n for n in flow.nodes.values() if n.kind is NodeKind.HANDOFF)
    assert flow.outgoing(answer.id) == {HANDOFF_HANDLE: handoff.id}


@pytest.mark.django_db(transaction=True)
async def test_default_flow_matches_build_graph_for_plain_answer(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    old, new, result = await _old_and_new(tenant, "영업시간이 어떻게 되나요?", fake_chat_llm)
    assert new == old
    assert old[2][0] is False  # 넘기지 않음
    assert [t["kind"] for t in result["trace"]] == ["start", "knowledge_search", "ai_answer"]


@pytest.mark.django_db(transaction=True)
async def test_default_flow_matches_build_graph_for_handoff(tenant_with_key, fake_chat_llm):
    """「상담원」 → AI가 needs_hitl → 사람 넘김(세션 HITL·Escalation 한 건)까지 같다."""
    tenant, _ = tenant_with_key
    old, new, result = await _old_and_new(tenant, "상담원 연결해 주세요", fake_chat_llm)
    assert new == old
    is_hitl, _, escalations = new[2]
    assert is_hitl is True and len(escalations) == 1
    assert [t["kind"] for t in result["trace"]] == ["start", "knowledge_search", "ai_answer", "handoff"]


@pytest.mark.django_db(transaction=True)
async def test_default_flow_matches_build_graph_when_hitl_disabled(tenant_with_key, fake_chat_llm):
    """테넌트가 사람 넘김을 끄면 넘김 없는 답변 스키마(PlainResponse)로 답하고 넘기지 않는다."""
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    await adb(TenantConfig.objects.filter(tenant=tenant).update)(hitl_enabled=False)
    old, new, _ = await _old_and_new(tenant, "상담원 연결해 주세요", fake_chat_llm)
    assert new == old
    assert new[1] == ["PlainResponse"]
    assert new[2][0] is False


@pytest.mark.django_db(transaction=True)
async def test_default_flow_matches_build_graph_for_source_fallback(tenant_with_key, fake_chat_llm):
    """그래프 검색으로 모자라면 원문 폴백 후 한 번 더 묻는다 — LLM 호출 횟수·답까지 같다."""
    from tests.test_local_source_fallback import _fake_1920, _seed_text_unit

    tenant, _ = tenant_with_key
    await adb(_seed_text_unit)(tenant)
    fake_chat_llm.override = _fake_1920
    old, new, _ = await _old_and_new(tenant, "지원하는 모니터의 해상도", fake_chat_llm)
    assert new == old
    assert "1920" in new[0]
    assert new[1] == ["HITLResponse", "HITLResponse"]


@pytest.mark.django_db(transaction=True)
async def test_answer_only_flow_skips_search_and_cannot_hand_off(tenant_with_key, fake_chat_llm):
    """지식 검색·사람 넘김이 없는 흐름: 검색 없이 답하고, 넘김 갈래가 없으니 넘김 없는 스키마로 묻는다.
    검색을 안 했으니 원문 폴백도 하지 않는다."""
    from apps.agent.nodes import PlainResponse
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    tenant, _ = tenant_with_key
    flow = {"nodes": [_node("s", "start", 1), _node("a", "ai_answer", 2)], "edges": [{"from": "s", "to": "a"}]}
    fake_chat_llm.override = lambda messages: PlainResponse(response="잘 모르겠어요.", context_sufficient=False)
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-only")

    result = await run_flow_turn_async(session, "상담원 연결해 주세요", flow)

    assert [t["kind"] for t in result["trace"]] == ["start", "ai_answer"]
    assert [name for name, _ in fake_chat_llm.captured] == ["PlainResponse"]
    is_hitl, messages, escalations = await adb(_snapshot)(session.id)
    assert (is_hitl, escalations) == (False, [])
    assert messages == [("assistant", "잘 모르겠어요.")]


@pytest.mark.django_db(transaction=True)
async def test_await_reply_ends_turn_and_next_turn_resumes_after_it(tenant_with_key, fake_chat_llm):
    """되묻기(await_reply) AI 답변에서 턴이 끝나고, 다음 턴은 그 노드의 나가는 선에서 이어진다."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    tenant, _ = tenant_with_key
    flow = {
        "nodes": [_node("s", "start", 1), _node("ask", "ai_answer", 2, {"await_reply": True}),
                  _node("final", "ai_answer", 3)],
        "edges": [{"from": "s", "to": "ask"}, {"from": "ask", "to": "final"}],
    }
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-ask")

    first = await run_flow_turn_async(session, "견적 문의요", flow)
    assert [t["node_id"] for t in first["trace"]] == ["s", "ask"]
    assert first["resume_node"] == "final"

    second = await run_flow_turn_async(session, "쇼핑몰이고 3개월 안에요", flow)
    assert [t["node_id"] for t in second["trace"]] == ["final"]
    assert second["resume_node"] == ""

    _, messages, _ = await adb(_snapshot)(session.id)
    assert [role for role, _ in messages] == ["assistant", "assistant"]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user", "assistant"]


@pytest.mark.django_db(transaction=True)
async def test_handoff_node_escalates_with_its_reason(tenant_with_key, fake_chat_llm):
    """AI 판단 없이 흐름이 곧장 사람 넘김에 닿으면 노드의 reason으로 넘긴다."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    tenant, _ = tenant_with_key
    flow = {"nodes": [_node("s", "start", 1), _node("h", "handoff", 2, {"reason": "모든 문의는 사람이"})],
            "edges": [{"from": "s", "to": "h"}]}
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-h")

    result = await run_flow_turn_async(session, "안녕하세요", flow)

    is_hitl, messages, escalations = await adb(_snapshot)(session.id)
    assert is_hitl is True
    assert escalations == [("ai", "모든 문의는 사람이", "pending")]
    assert messages == []  # 답이 없으니 상담 메시지도 없다 — 끝에서 빈 답을 따로 저장하지 않는다
    assert result["messages"] == [{"role": "user", "content": "안녕하세요"}]
    assert fake_chat_llm.captured == []


@pytest.mark.django_db(transaction=True)
async def test_handoff_node_respects_hitl_disabled(tenant_with_key, fake_chat_llm):
    """테넌트가 사람 넘김을 끄면 사람 넘김 노드는 넘기지 않고 턴을 끝낸다."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    await adb(TenantConfig.objects.filter(tenant=tenant).update)(hitl_enabled=False)
    flow = {"nodes": [_node("s", "start", 1), _node("h", "handoff", 2)], "edges": [{"from": "s", "to": "h"}]}
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-h-off")

    await run_flow_turn_async(session, "안녕하세요", flow)

    is_hitl, _, escalations = await adb(_snapshot)(session.id)
    assert (is_hitl, escalations) == (False, [])


@pytest.mark.django_db(transaction=True)
async def test_ai_answer_uses_the_chat_provider_not_its_own_client(tenant_with_key, fake_chat_llm):
    """AI 답변 노드는 클라이언트를 직접 만들지 않는다 — 기존 chat_provider(config)가 고른 Provider로 묻는다(#41)."""
    from apps.agent.providers import chat_provider
    from apps.chat.models import ChatSession
    from apps.flows.defaults import DEFAULT_FLOW
    from apps.flows.executor import run_flow_turn_async
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-prov")
    await run_flow_turn_async(session, "안녕하세요", DEFAULT_FLOW)

    expected = await adb(lambda: chat_provider(TenantConfig.objects.get(tenant=tenant)))()
    assert fake_chat_llm.last_provider == expected


def test_ai_answer_may_branch_to_handoff_handle():
    """AI 답변은 `next` 말고 `handoff` 갈래를 가질 수 있다(AI가 사람 넘김을 판단할 때만 그 선으로)."""
    from apps.flows.schema import validate_flow

    flow = {"nodes": [_node("s", "start", 1), _node("a", "ai_answer", 2), _node("h", "handoff", 3),
                      _node("b", "ai_answer", 4)],
            "edges": [{"from": "s", "to": "a"}, {"from": "a", "to": "h", "handle": "handoff"},
                      {"from": "a", "to": "b"}]}
    assert validate_flow(flow) == []
