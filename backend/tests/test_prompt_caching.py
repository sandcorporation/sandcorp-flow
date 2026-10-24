"""프롬프트 캐싱 친화 메시지 재구조화 (issues 133-134).

휘발성(RAG·Visitor Memory)을 안정 system prefix에서 빼 뒤쪽 사용자 턴으로 옮겨,
provider 프롬프트 캐싱이 작동하도록 한다. LLM 판단이 아니라 우리 조립 코드를 검증한다(결정적).
"""


# ── Issue 133: 캐시 친화 메시지 재배치 ────────────────────────────────────────

def test_system_prefix_stable_across_volatile_context():
    """캐시 분기점: system prefix는 RAG/메모리가 달라져도 byte-동일해야 캐싱이 작동한다."""
    from apps.agent.nodes import _assemble_lc_messages

    base = {"system_prompt": "You are X.", "messages": [], "user_message": "안녕"}
    a = _assemble_lc_messages({**base, "rag_chunks": ["fact A"], "visitor_memories": []})
    b = _assemble_lc_messages({**base, "rag_chunks": ["fact B 1920x1080"], "visitor_memories": ["좋아함"]})

    # 안정 system prefix는 휘발성과 무관하게 동일
    assert a[0].content == b[0].content
    # 휘발성은 system에 없다
    assert "fact A" not in a[0].content
    assert "fact B" not in b[0].content
    assert "좋아함" not in b[0].content


def test_volatile_context_moves_to_trailing_user_turn():
    """RAG·Visitor Memory는 뒤쪽 사용자 턴에 UNTRUSTED_DATA 격리로 실린다."""
    from apps.agent.nodes import _assemble_lc_messages

    msgs = _assemble_lc_messages({
        "system_prompt": "You are X.",
        "messages": [],
        "rag_chunks": ["해상도 1920x1080"],
        "visitor_memories": ["이름 홍길동"],
        "user_message": "해상도 알려줘",
    })

    trailing = msgs[-1].content
    assert "1920x1080" in trailing       # RAG가 뒤쪽 턴에
    assert "이름 홍길동" in trailing      # 메모리도 뒤쪽 턴에
    assert "UNTRUSTED_DATA" in trailing  # 격리 유지
    assert "해상도 알려줘" in trailing    # 현재 질문도 같은 턴
    # 보안 지침은 안정 system에 남는다
    assert "노출" in msgs[0].content


# ── Issue 134: Anthropic cache_control 마커 (boundary, provider-aware) ─────────

def test_anthropic_provider_marks_system_with_cache_control():
    """anthropic provider면 안정 system 블록 끝에 cache_control(ephemeral) 분기점이 붙는다."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from apps.agent.llm import _mark_cache_breakpoint
    from apps.agent.providers import LLMProvider

    msgs = [SystemMessage(content="STABLE PREFIX"), HumanMessage(content="질문")]
    out = _mark_cache_breakpoint(LLMProvider(type="anthropic", model="claude"), msgs)

    sys = out[0]
    assert isinstance(sys.content, list)                       # 블록 리스트로 변환
    assert sys.content[-1]["text"] == "STABLE PREFIX"
    assert sys.content[-1]["cache_control"] == {"type": "ephemeral"}
    assert out[1].content == "질문"                             # 나머지는 불변


def test_non_anthropic_providers_get_no_marker():
    """openai/custom/플랫폼기본은 자동 prefix 캐싱 — cache_control 마커를 붙이지 않는다."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from apps.agent.llm import _mark_cache_breakpoint
    from apps.agent.providers import LLMProvider

    for t in ("openai", "custom", ""):
        msgs = [SystemMessage(content="STABLE PREFIX"), HumanMessage(content="질문")]
        out = _mark_cache_breakpoint(LLMProvider(type=t, model="m"), msgs)
        assert out[0].content == "STABLE PREFIX"  # 문자열 그대로, 마커 없음


# ── Issue 201: 빌드된 그래프의 여러 플로우에서 안정 prefix 검증 (FakeLLM 캡처) ──
# 단위 테스트는 _assemble_lc_messages를 손으로 부른다. 여기선 run_chat_turn_async(흐름 실행기)가
# 실제로 LLM에 보낸 messages를 fake가 캡처해, 그래프 종단에서 prefix 불변식을 잠근다.
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _chat_prefixes(captured):
    """chat 구조화 호출(HITLResponse/PlainResponse)의 system 메시지 콘텐츠만 추출."""
    return [msgs[0].content for name, msgs in captured
            if name in ("HITLResponse", "PlainResponse")]


def _chat_calls(captured):
    return [msgs for name, msgs in captured if name in ("HITLResponse", "PlainResponse")]


@pytest.mark.django_db(transaction=True)
async def test_capture_harness_records_graph_messages(tenant_with_key, fake_chat_llm):
    """캡처 하네스: 그래프가 LLM에 보낸 messages가 (schema, messages)로 기록되고 system이 맨 앞."""
    from langchain_core.messages import SystemMessage
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-cap")
    await run_chat_turn_async(session, "안녕")

    calls = _chat_calls(fake_chat_llm.captured)
    assert len(calls) >= 1
    assert isinstance(calls[0][0], SystemMessage)   # 첫 메시지는 안정 system prefix


@pytest.mark.django_db(transaction=True)
async def test_graph_system_prefix_stable_across_hitl_and_multiturn(tenant_with_key, fake_chat_llm):
    """HITL-on/off 두 경로 + 멀티턴에서 system prefix가 byte-동일(캐싱 안정 prefix)."""
    from apps.tenants.models import TenantConfig
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    # HITL-on(기본): 같은 세션 2턴(멀티턴, history 누적)
    s1 = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-mt")
    await run_chat_turn_async(s1, "안녕")
    await run_chat_turn_async(s1, "또 질문이요")
    # HITL-off로 전환 후 새 세션 1턴
    def _off():
        c = TenantConfig.objects.get(tenant=tenant)
        c.hitl_enabled = False
        c.save()
    await adb(_off)()
    s2 = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-off")
    await run_chat_turn_async(s2, "안녕")

    prefixes = _chat_prefixes(fake_chat_llm.captured)
    assert len(prefixes) >= 3
    assert len(set(prefixes)) == 1, f"system prefix가 플로우 간 달라짐: {set(prefixes)}"


@pytest.mark.django_db(transaction=True)
async def test_graph_volatile_in_trailing_not_system(tenant_with_key, fake_chat_llm):
    """Visitor Memory(휘발성)는 그래프 종단에서 trailing 턴에만, system prefix엔 없다."""
    from apps.memory.manager import upsert_memory
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(upsert_memory)(str(tenant.id), "v-mem", "이름", "홍길동")

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-mem")
    await run_chat_turn_async(session, "내 이름 기억해?")

    calls = _chat_calls(fake_chat_llm.captured)
    system = calls[0][0].content
    trailing = calls[0][-1].content
    assert "홍길동" not in system           # 휘발성은 안정 prefix에 없다
    assert "홍길동" in trailing             # trailing 턴에 실린다
    assert "UNTRUSTED_DATA" in trailing     # 격리 유지


# ── Issue 202: 나머지 그래프 플로우 (폴백·영업시간 외·주제범위 ON) ────────────

@pytest.mark.django_db(transaction=True)
async def test_graph_system_prefix_stable_on_source_fallback(tenant_with_key, fake_chat_llm):
    """원문 폴백(2번째 call_llm, RAG 증가)에서도 두 call의 system prefix byte-동일, RAG는 trailing에만."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key

    def _seed():
        gs = GraphStore(str(tenant.id))
        fact = "이 모니터는 1920 x 1080 FHD 해상도를 지원합니다."
        gs.ensure_vector_index(dimensions=1024)
        emb = get_embeddings([fact], provider=gs._embedding_provider())[0]
        gs.upsert_text_unit("u-res", fact, emb, source_document_id="d1", chunk_index=0)
    await adb(_seed)()
    # context_sufficient=False → source_search → 2번째 call_llm
    fake_chat_llm.override = lambda m: HITLResponse(
        response="죄송합니다.", needs_hitl=False, hitl_reason="",
        context_sufficient=False, in_scope=True)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-fb")
    await run_chat_turn_async(session, "지원 해상도 알려줘")

    calls = _chat_calls(fake_chat_llm.captured)
    assert len(calls) == 2, f"폴백이면 call_llm 2회여야: {len(calls)}"
    assert calls[0][0].content == calls[1][0].content      # system prefix 두 call 동일
    assert "1920" in calls[1][-1].content                  # 보강된 RAG는 trailing
    assert "1920" not in calls[1][0].content               # system엔 없다


@pytest.mark.django_db(transaction=True)
async def test_graph_offhours_notice_in_trailing_not_system(tenant_with_key, fake_chat_llm, monkeypatch):
    """영업시간 외 운영 안내는 trailing 턴에만, system prefix 불변."""
    from apps.tenants import business_hours
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    monkeypatch.setattr(business_hours, "is_open", lambda config, now: False)
    tenant, _ = tenant_with_key  # hitl_enabled 기본 True → 시간 외 운영 안내 주입
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-oh")
    await run_chat_turn_async(session, "안녕")

    calls = _chat_calls(fake_chat_llm.captured)
    assert "운영 안내" in calls[0][-1].content       # 안내는 trailing
    assert "운영 안내" not in calls[0][0].content    # system prefix엔 없다


@pytest.mark.django_db(transaction=True)
async def test_graph_system_prefix_stable_with_topic_scope_on(tenant_with_key, fake_chat_llm):
    """주제범위 ON: scope 지침(테넌트 불변)이 안정 prefix에 포함되고 모든 턴에서 byte-동일(캐싱 안 깨짐)."""
    from apps.tenants.models import TenantConfig
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key

    def _enable():
        c = TenantConfig.objects.get(tenant=tenant)
        c.topic_scope_enabled = True
        c.scope_description = "주문·배송 문의"
        c.save()
    await adb(_enable)()

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-sc")
    await run_chat_turn_async(session, "배송 문의요")
    await run_chat_turn_async(session, "또 질문이요")

    prefixes = _chat_prefixes(fake_chat_llm.captured)
    assert len(prefixes) >= 2
    assert len(set(prefixes)) == 1, f"scope ON 턴 간 prefix 달라짐: {set(prefixes)}"
    assert "주문·배송 문의" in prefixes[0]    # scope 지침이 안정 prefix에 들어감
