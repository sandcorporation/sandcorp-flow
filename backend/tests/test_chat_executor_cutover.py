"""운영 챗 경로를 흐름 실행기로 교체(#72, ADR-0027).

방문자 한 턴(chat_task)이 세션에 고정된 Flow Version의 흐름을 실행기로 돈다. 세션 도중 배포는 진행 중 대화를 바꾸지 않고,
고정 버전이 없는 기존 세션은 첫 턴에 공개 흐름의 배포 중 버전에 고정된다. 운영 배포 순간의 옛 그래프(build_graph)
체크포인트도 예외 없이 이어 간다. LLM은 결정적 Fake(conftest autouse), DB·체크포인터·taskiq(InMemory)는 실제 객체.
"""
import copy

import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _new_session(tenant, visitor="v-cut"):
    from apps.chat.models import ChatSession

    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id=visitor)


async def _turn(session, message="안녕"):
    from apps.chat.chat_task import chat_task

    sent = await chat_task.kiq(str(session.id), message)
    await sent.wait_result(timeout=20)


def _turn_nodes(session):
    """턴마다 지난 LangGraph 노드 이름(=흐름 노드 id)."""
    from apps.agent.graph import session_retrievals

    return [t["nodes"] for t in session_retrievals(str(session.id))]


def _assistant_messages(session):
    from apps.chat.models import ChatMessage

    return list(ChatMessage.objects.filter(session=session, role="assistant").order_by("created_at")
                .values_list("content", flat=True))


def _deploy(tenant, graph):
    """테넌트의 공개 흐름(없으면 기본 흐름을 만든다)에 graph를 새 버전으로 굳혀 배포한다. 버전을 돌려준다."""
    from apps.flows import services
    from apps.flows.defaults import ensure_default_flow

    flow = ensure_default_flow(tenant.id)
    services.save_draft(tenant.id, flow.id, graph)
    version = services.save_version(tenant.id, flow.id).version
    services.deploy_version(tenant.id, flow.id, version.number)
    return version


def _renamed_default_flow(answer_id):
    """기본 흐름에서 AI 답변 노드 id만 바꾼 것 — 어느 버전으로 돌았는지 trace로 가린다."""
    from apps.flows.defaults import DEFAULT_FLOW

    graph = copy.deepcopy(DEFAULT_FLOW)
    for node in graph["nodes"]:
        if node["id"] == "answer":
            node["id"] = answer_id
    for edge in graph["edges"]:
        edge["from"] = answer_id if edge["from"] == "answer" else edge["from"]
        edge["to"] = answer_id if edge["to"] == "answer" else edge["to"]
    return graph


@pytest.mark.django_db(transaction=True)
async def test_visitor_turn_runs_through_flow_executor(tenant_with_key, fake_chat_llm):
    """공개 흐름이 없는 테넌트도 기본 흐름(DEFAULT_FLOW)을 실행기로 돈다 — build_graph 노드(call_llm 등)가 아니다."""
    tenant, _ = tenant_with_key
    session = await adb(_new_session)(tenant)

    await _turn(session)

    nodes = await adb(_turn_nodes)(session)
    assert nodes[-1][:3] == ["start", "search", "answer"]
    assert "call_llm" not in nodes[-1]
    assert await adb(_assistant_messages)(session) == ["안녕하세요! 무엇을 도와드릴까요?"]
    await adb(session.refresh_from_db)()
    assert session.flow_version_id is None  # 고정할 공개 흐름이 없으면 비워 둔 채 기본 흐름으로


@pytest.mark.django_db(transaction=True)
async def test_deploy_mid_conversation_keeps_old_version_for_existing_session(tenant_with_key, fake_chat_llm):
    """세션 도중 새 버전을 배포해도 기존 세션은 고정된 옛 버전으로, 새 세션은 새 버전으로 돈다."""
    tenant, _ = tenant_with_key
    v1 = await adb(_deploy)(tenant, _renamed_default_flow("answer_v1"))
    old = await adb(_new_session)(tenant, "v-old")
    await _turn(old, "첫 질문")

    v2 = await adb(_deploy)(tenant, _renamed_default_flow("answer_v2"))
    new = await adb(_new_session)(tenant, "v-new")
    await _turn(old, "배포 뒤 질문")
    await _turn(new, "새 세션 질문")

    assert (old.flow_version_id, new.flow_version_id) == (v1.id, v2.id)
    old_nodes = await adb(_turn_nodes)(old)
    assert all("answer_v1" in turn for turn in old_nodes) and len(old_nodes) == 2
    assert "answer_v2" in (await adb(_turn_nodes)(new))[-1]


@pytest.mark.django_db(transaction=True)
async def test_unpinned_legacy_session_pins_to_public_version_on_first_turn(tenant_with_key, fake_chat_llm):
    """flow_version이 빈 기존 세션은 첫 턴에 그 테넌트 공개 흐름의 배포 중 버전에 고정되고, 이후 배포에도 그 버전으로 이어 간다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    v1 = await adb(_deploy)(tenant, _renamed_default_flow("answer_v1"))
    session = await adb(_new_session)(tenant)
    await adb(ChatSession.objects.filter(pk=session.pk).update)(flow_version=None)  # 이 칸이 생기기 전 세션

    await _turn(session, "첫 질문")
    await adb(session.refresh_from_db)()
    assert session.flow_version_id == v1.id

    await adb(_deploy)(tenant, _renamed_default_flow("answer_v2"))
    await _turn(session, "둘째 질문")
    assert all("answer_v1" in turn for turn in await adb(_turn_nodes)(session))


@pytest.mark.django_db(transaction=True)
async def test_await_reply_cycle_resumes_from_next_node_on_next_turn(tenant_with_key, fake_chat_llm):
    """되묻기 순환: 턴1은 await_reply AI 답변에서 끝나고, 턴2는 그 노드의 나가는 선(answer)에서 이어 간다."""
    tenant, _ = tenant_with_key
    cycle = {
        "nodes": [
            _node("start", "start", 1),
            _node("ask", "ai_answer", 2, {"await_reply": True}),
            _node("answer", "ai_answer", 3),
        ],
        "edges": [
            {"from": "start", "to": "ask"},
            {"from": "ask", "to": "answer"},
            {"from": "answer", "to": "ask"},
        ],
    }
    await adb(_deploy)(tenant, cycle)
    session = await adb(_new_session)(tenant)

    await _turn(session, "주문하고 싶어요")
    await _turn(session, "빨간색이요")

    nodes = await adb(_turn_nodes)(session)
    assert nodes[0] == ["start", "ask", "__flow_finish__"]
    assert nodes[1] == ["answer", "ask", "__flow_finish__"]
    assert len(await adb(_assistant_messages)(session)) == 2


async def _legacy_checkpoint(session, *, crashed: bool):
    """운영 배포 전 build_graph가 남긴 것과 같은 모양의 체크포인트(노드 이름·ChatState 채널)를 같은 thread_id에 남긴다.

    crashed면 save_messages 직전에 멈춘 턴(워커가 죽은 경우)처럼 다음 노드가 남은 체크포인트가 된다.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph

    from apps.agent.graph import ChatState, _build_conninfo

    async def local_search(state):
        return {"rag_chunks": ["옛 근거"]}

    async def call_llm(state):
        return {"assistant_response": "옛 답변", "needs_hitl": False, "context_sufficient": True}

    async def save_messages(state):
        return {"messages": [{"role": "user", "content": state["user_message"]},
                             {"role": "assistant", "content": state["assistant_response"]}],
                "rag_chunks": []}

    legacy = StateGraph(ChatState)
    legacy.add_node("local_search", local_search)
    legacy.add_node("call_llm", call_llm)
    legacy.add_node("save_messages", save_messages)
    legacy.add_edge(START, "local_search")
    legacy.add_edge("local_search", "call_llm")
    legacy.add_edge("call_llm", "save_messages")
    legacy.add_edge("save_messages", END)

    async with AsyncPostgresSaver.from_conn_string(_build_conninfo()) as saver:
        await saver.setup()
        graph = legacy.compile(checkpointer=saver, interrupt_before=["save_messages"] if crashed else None)
        await graph.ainvoke(
            {"session_id": str(session.id), "tenant_id": str(session.tenant_id), "visitor_id": session.visitor_id,
             "user_message": "배포 전 질문", "messages": []},
            config={"configurable": {"thread_id": str(session.id)}},
        )


def _latest_messages(session):
    from apps.agent.graph import _create_checkpointer

    saver, conn = _create_checkpointer()
    try:
        tup = saver.get_tuple({"configurable": {"thread_id": str(session.id)}})
    finally:
        conn.close()
    return tup.checkpoint["channel_values"].get("messages", [])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("crashed", [False, True], ids=["finished_turn", "crashed_mid_turn"])
async def test_legacy_build_graph_checkpoint_continues_without_error(tenant_with_key, fake_chat_llm, crashed):
    """배포 순간 진행 중이던 대화: 옛 그래프 체크포인트 위에서 새 턴이 예외 없이 돌고, 끝난 턴의 대화 기록은 이어진다."""
    tenant, _ = tenant_with_key
    session = await adb(_new_session)(tenant)
    await _legacy_checkpoint(session, crashed=crashed)

    await _turn(session, "배포 뒤 질문")

    assert await adb(_assistant_messages)(session) == ["안녕하세요! 무엇을 도와드릴까요?"]
    contents = [m["content"] for m in await adb(_latest_messages)(session)]
    assert contents[-2:] == ["배포 뒤 질문", "안녕하세요! 무엇을 도와드릴까요?"]
    if not crashed:
        assert contents[:2] == ["배포 전 질문", "옛 답변"]  # 옛 대화 기록 위에 이어 쌓인다
