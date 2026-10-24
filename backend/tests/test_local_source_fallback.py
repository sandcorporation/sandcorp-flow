# pyright: reportOptionalSubscript=false
"""GraphRAG Local Search — 미스 기반 원문(TextUnit) 폴백 (issues 118-119). 노드 async화 전환.

LLM은 결정적 Fake(CLAUDE.md). 임베딩·vector_search는 실제로 검증한다.
"""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _seed_text_unit(tenant):
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings
    gs = GraphStore(str(tenant.id))
    fact = "이 모니터는 1920 x 1080 FHD 해상도를 지원합니다."
    gs.ensure_vector_index(dimensions=1024)
    emb = get_embeddings([fact], provider=gs._embedding_provider())[0]
    gs.upsert_text_unit("u-res", fact, emb, source_document_id="d1", chunk_index=0)


def _read_cv(session_id):
    from apps.agent.graph import _create_checkpointer
    saver, conn = _create_checkpointer()
    try:
        return saver.get({"configurable": {"thread_id": str(session_id)}})
    finally:
        conn.close()


def _fake_1920(messages):
    from apps.agent.nodes import HITLResponse
    joined = " ".join(getattr(m, "content", "") for m in messages)
    if "1920" in joined:
        return HITLResponse(response="지원 해상도는 1920x1080(FHD)입니다.",
                            needs_hitl=False, hitl_reason="", context_sufficient=True)
    return HITLResponse(response="", needs_hitl=False, hitl_reason="", context_sufficient=False)


@pytest.mark.django_db(transaction=True)
async def test_context_sufficient_flows_to_checkpoint(tenant_with_key, fake_chat_llm):
    """call_llm이 context_sufficient를 상태로 흘려 체크포인트에 노출한다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    fake_chat_llm.override = lambda messages: HITLResponse(
        response="죄송합니다, 해당 정보를 찾지 못했습니다.",
        needs_hitl=False, hitl_reason="", context_sufficient=False,
    )
    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-ctx")
    await run_chat_turn_async(session, "지원하는 모니터의 해상도")

    cp = await adb(_read_cv)(session.id)
    assert cp is not None
    assert cp["channel_values"]["context_sufficient"] is False


@pytest.mark.django_db(transaction=True)
async def test_local_search_falls_back_to_source_text(tenant_with_key, fake_chat_llm):
    """그래프엔 없고 TextUnit(원문)에만 있는 사실을, miss 시 원문 폴백으로 답한다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_seed_text_unit)(tenant)
    fake_chat_llm.override = _fake_1920

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-res")
    answer = await run_chat_turn_async(session, "지원하는 모니터의 해상도")
    assert "1920" in answer


@pytest.mark.django_db(transaction=True)
async def test_fallback_streams_answer_once(tenant_with_key, fake_chat_llm, monkeypatch):
    """폴백으로 call_llm이 2회 실행돼도 응답은 1번만 스트리밍된다(중복 방지)."""
    from apps.agent import nodes
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_seed_text_unit)(tenant)

    def fake(messages):
        joined = " ".join(getattr(m, "content", "") for m in messages)
        if "1920" in joined:
            return HITLResponse(response="지원 해상도는 1920x1080(FHD)입니다.",
                                needs_hitl=False, hitl_reason="", context_sufficient=True)
        return HITLResponse(response="죄송합니다, 정보를 찾지 못했습니다.",
                            needs_hitl=False, hitl_reason="", context_sufficient=False)
    fake_chat_llm.override = fake

    dones = {"n": 0}
    async def _adone(*a, **k):
        dones["n"] += 1
    async def _atok(*a, **k):
        pass
    monkeypatch.setattr(nodes, "apublish_done", _adone)
    monkeypatch.setattr(nodes, "apublish_token", _atok)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-dup")
    answer = await run_chat_turn_async(session, "지원하는 모니터의 해상도")

    cv = (await adb(_read_cv)(session.id))["channel_values"]
    assistants = [m for m in (cv.get("messages") or []) if m.get("role") == "assistant"]
    assert "1920" in answer
    assert dones["n"] == 1, f"apublish_done가 {dones['n']}회 호출됨(중복 스트리밍)"
    assert len(assistants) == 1, f"checkpoint messages에 assistant {len(assistants)}개(중복)"


@pytest.mark.django_db(transaction=True)
async def test_transient_rag_chunks_not_retained_in_resting_checkpoint(tenant_with_key, fake_chat_llm):
    """휴지 체크포인트는 대화(messages)만 보존하고 턴 한정 검색 산출물(rag_chunks)은 비운다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_seed_text_unit)(tenant)
    fake_chat_llm.override = _fake_1920

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-slim")
    await run_chat_turn_async(session, "지원하는 모니터의 해상도")

    cv = (await adb(_read_cv)(session.id))["channel_values"]
    assert cv.get("rag_chunks") == []
    assert cv.get("visitor_memories") == []
    assert len([m for m in (cv.get("messages") or []) if m.get("role") == "assistant"]) >= 1


@pytest.mark.django_db(transaction=True)
async def test_no_source_fallback_when_graph_answers(tenant_with_key, fake_chat_llm):
    """그래프로 답한 happy path(context_sufficient=True)에선 원문 폴백을 타지 않는다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    fake_chat_llm.override = lambda messages: HITLResponse(
        response="안녕하세요!", needs_hitl=False, hitl_reason="", context_sufficient=True)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-happy")
    answer = await run_chat_turn_async(session, "안녕")
    assert answer == "안녕하세요!"

    cv = (await adb(_read_cv)(session.id))["channel_values"]
    assert cv.get("source_text_tried") is False
    assert cv.get("rag_chunks") == []


@pytest.mark.django_db(transaction=True)
async def test_session_retrievals_recovers_chunks_from_history(tenant_with_key, fake_chat_llm):
    """휴지 체크포인트는 rag_chunks를 비우지만, session_retrievals는 히스토리에서 턴별 검색 결과를
    복원한다 — 테넌트가 어드민에서 검색 과정을 보게 한다(issue 207, 체크포인트 슬림화는 불변)."""
    from apps.agent.graph import session_retrievals
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_seed_text_unit)(tenant)
    fake_chat_llm.override = _fake_1920

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-retr")
    await run_chat_turn_async(session, "지원하는 모니터의 해상도")

    # 휴지 체크포인트엔 비어 있다(슬림화 불변)
    cv = (await adb(_read_cv)(session.id))["channel_values"]
    assert cv.get("rag_chunks") == []
    # 그러나 히스토리에서 복원된다
    turns = await adb(session_retrievals)(str(session.id))
    assert len(turns) >= 1
    last = turns[-1]
    assert last["user_message"] == "지원하는 모니터의 해상도"
    assert last["chunk_count"] >= 1
    assert any("1920" in c for c in last["chunks"])
    # 실행 노드 흐름도 히스토리(versions_seen)에서 복원된다 — 원문 폴백 경로
    # 실행 노드는 흐름 노드 id(#72). 원문 폴백은 AI 답변 핸들러 안에서 돌아 따로 보이지 않는다.
    assert last["nodes"] == ["start", "search", "answer", "__flow_finish__"]
