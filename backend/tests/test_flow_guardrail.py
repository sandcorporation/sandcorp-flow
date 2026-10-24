"""가드레일 노드(#26). 개인정보·금지어는 결정적 코드, 가격 약속·다시 쓰기는 LLM(결정적 Fake). DB·실행기·체크포인터는 실제 객체."""
import pytest
from asgiref.sync import sync_to_async

from tests.test_llm_usage_wire import (  # noqa: F401 — 실제 LLM 경계 + OpenAI 호환 대역 픽스처와 상수를 재사용
    COMPLETION_TOKENS,
    GPT_4O_MINI_COST,
    PROMPT_TOKENS,
    openai_stub,
    platform_tenant,
    real_llm_boundary,
)

adb = sync_to_async

BLOCK_MSG = "해당 내용은 안내해 드릴 수 없습니다."


# ── 개인정보 패턴 표(결정적 정규식) ─────────────────────────────────────────

PII_HITS = [
    ("010-1234-5678", "phone"), ("01012345678", "phone"), ("010 1234 5678", "phone"), ("011-123-4567", "phone"),
    ("02-123-4567", "phone"), ("031-1234-5678", "phone"), ("+82 10-1234-5678", "phone"),
    ("900101-1234567", "rrn"), ("9001011234567", "rrn"), ("010101 3234567", "rrn"),
    ("4111-1111-1111-1111", "card"), ("4111 1111 1111 1111", "card"), ("4111111111111111", "card"),
    ("a.b+c@example.co.kr", "email"), ("hello@sandcorp.co.kr", "email"),
]
PII_MISSES = [
    "주문번호 12345 입니다", "2026-10-24에 뵙겠습니다", "견적은 1,000,000원부터입니다", "기간은 3~6개월",
    "4111-1111-1111-1112",  # 카드 형식이지만 Luhn 불일치
    "991399-1234567",  # 월(13)이 틀려 주민등록번호가 아님
    "사업자등록번호 123-45-67890",
]


@pytest.mark.parametrize("text,kind", PII_HITS)
def test_pii_patterns_detect(text, kind):
    from apps.flows.guardrail import find_pii

    assert [h.kind for h in find_pii(f"연락처는 {text} 입니다")] == [kind]


@pytest.mark.parametrize("text", PII_MISSES)
def test_pii_patterns_ignore_lookalikes(text):
    from apps.flows.guardrail import find_pii

    assert find_pii(text) == []


def test_mask_pii_replaces_each_with_its_kind_label():
    from apps.flows.guardrail import mask_pii

    out = mask_pii("전화 010-1234-5678, 메일 a@b.co, 주민 900101-1234567")
    assert out == "전화 [전화번호 가림], 메일 [이메일 가림], 주민 [주민등록번호 가림]"


# ── 노드 ───────────────────────────────────────────────────────────────────


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _answer_flow(guard_config, *, await_reply=False):
    return {
        "nodes": [_node("s", "start", 1), _node("ai", "ai_answer", 2, {"await_reply": await_reply}),
                  _node("g", "guardrail", 3, guard_config)],
        "edges": [{"from": "s", "to": "ai"}, {"from": "ai", "to": "g"}],
    }


def _input_flow(guard_config):
    return {
        "nodes": [_node("s", "start", 1), _node("g", "guardrail", 2, {**guard_config, "target": "input"}),
                  _node("ai", "ai_answer", 3)],
        "edges": [{"from": "s", "to": "g"}, {"from": "g", "to": "ai"}],
    }


@pytest.fixture
def visitor_wire(monkeypatch):
    """방문자 SSE에 나가는 토큰·done을 순서대로 기록한다(Redis 발행 경계)."""
    from apps.agent import nodes

    wire = {"tokens": [], "done": 0}

    async def _tok(sid, content):
        wire["tokens"].append(content)

    async def _done(sid):
        wire["done"] += 1

    monkeypatch.setattr(nodes, "apublish_token", _tok)
    monkeypatch.setattr(nodes, "apublish_done", _done)
    return wire


def _model_says(fake, text):
    from apps.agent.nodes import PlainResponse

    fake.override = lambda m: PlainResponse(response=text)


async def _turn(tenant, flow, message="안녕하세요"):
    from apps.chat.models import ChatMessage, ChatSession
    from apps.flows.executor import run_flow_turn_async

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-guard")
    result = await run_flow_turn_async(session, message, flow)
    saved = await adb(lambda: [m.content for m in ChatMessage.objects.filter(session=session, role="assistant")])()
    return result, saved


def _guard(result):
    return next(t for t in result["trace"] if t["kind"] == "guardrail")


@pytest.mark.django_db(transaction=True)
async def test_clean_answer_passes_untouched_and_streams_once_after_the_check(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "영업시간은 9시부터 6시입니다")

    result, saved = await _turn(tenant, _answer_flow({"rules": ["pii", "banned_words"], "banned_words": ["욕설"]}))

    assert saved == ["영업시간은 9시부터 6시입니다"]
    assert visitor_wire == {"tokens": ["영업시간은 9시부터 6시입니다"], "done": 1}
    rec = _guard(result)["record"]
    assert rec["hits"] == [] and "걸린 규칙 없음" in rec["reason"]


@pytest.mark.django_db(transaction=True)
async def test_block_replaces_the_answer_and_the_original_never_reaches_the_visitor(tenant_with_key, fake_chat_llm, visitor_wire):
    """스트리밍: 가드레일이 뒤에 붙은 AI 답변은 모아 두었다가 통과 뒤에 내보낸다 — 막힌 원문이 토큰으로 먼저 새지 않는다."""
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "문의는 010-1234-5678 로 주세요")

    result, saved = await _turn(tenant, _answer_flow({"rules": ["pii"], "action": "block", "block_message": BLOCK_MSG}))

    assert visitor_wire == {"tokens": [BLOCK_MSG], "done": 1}  # 방문자에게 보이는 메시지 목록
    assert saved == [BLOCK_MSG] and result["assistant_response"] == BLOCK_MSG
    assert [m["content"] for m in result["messages"] if m["role"] == "assistant"] == [BLOCK_MSG]
    rec = _guard(result)["record"]
    assert rec["hits"] == [{"rule": "pii", "kind": "phone"}] and rec["action"] == "block"
    assert "010-1234-5678" not in str(rec)  # 판단 기록에 걸린 원문을 남기지 않는다


@pytest.mark.django_db(transaction=True)
async def test_mask_hides_pii_and_banned_words_and_keeps_the_rest(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "메일 a@b.co 로 보내고 경쟁사는 욕설입니다")

    result, saved = await _turn(
        tenant, _answer_flow({"rules": ["pii", "banned_words"], "banned_words": ["욕설"], "action": "mask"}))

    expected = "메일 [이메일 가림] 로 보내고 경쟁사는 ●●입니다"
    assert saved == [expected] and visitor_wire == {"tokens": [expected], "done": 1}
    assert {(h["rule"], h["kind"]) for h in _guard(result)["record"]["hits"]} == {("pii", "email"), ("banned_words", "욕설")}


@pytest.mark.django_db(transaction=True)
async def test_banned_word_match_ignores_case(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "This is a FREE gift")

    _, saved = await _turn(
        tenant, _answer_flow({"rules": ["banned_words"], "banned_words": ["free"], "action": "block", "block_message": BLOCK_MSG}))

    assert saved == [BLOCK_MSG]


@pytest.mark.django_db(transaction=True)
async def test_rewrite_asks_the_llm_and_sends_only_the_rewritten_text(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.flows.guardrail import REWRITE_SCHEMA_NAME

    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "문의는 010-1234-5678 로 주세요")
    fake_chat_llm.by_schema[REWRITE_SCHEMA_NAME] = lambda m, schema: schema(text="문의는 사이트 문의하기로 남겨 주세요")

    result, saved = await _turn(tenant, _answer_flow({"rules": ["pii"], "action": "rewrite"}))

    assert saved == ["문의는 사이트 문의하기로 남겨 주세요"]
    assert visitor_wire["tokens"] == ["문의는 사이트 문의하기로 남겨 주세요"] and visitor_wire["done"] == 1
    entry = _guard(result)
    assert entry["record"]["action"] == "rewrite" and len(entry["calls"]) == 1
    assert entry["input_tokens"] == 120  # LLM 호출 토큰이 노드에 잡힌다


@pytest.mark.django_db(transaction=True)
async def test_rewrite_that_still_violates_falls_back_to_the_block_message(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.flows.guardrail import REWRITE_SCHEMA_NAME

    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "문의는 010-1234-5678 로 주세요")
    fake_chat_llm.by_schema[REWRITE_SCHEMA_NAME] = lambda m, schema: schema(text="전화는 010-9999-8888 입니다")

    result, saved = await _turn(tenant, _answer_flow({"rules": ["pii"], "action": "rewrite", "block_message": BLOCK_MSG}))

    assert saved == [BLOCK_MSG]
    assert "다시 쓴 결과에도" in _guard(result)["record"]["reason"]


@pytest.mark.django_db(transaction=True)
async def test_rewrite_llm_failure_blocks_instead_of_letting_the_original_through(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.flows.guardrail import REWRITE_SCHEMA_NAME

    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "문의는 010-1234-5678 로 주세요")

    def boom(m, schema):
        raise ValueError("모델 오류")

    fake_chat_llm.by_schema[REWRITE_SCHEMA_NAME] = boom

    _, saved = await _turn(tenant, _answer_flow({"rules": ["pii"], "action": "rewrite", "block_message": BLOCK_MSG}))

    assert saved == [BLOCK_MSG] and visitor_wire == {"tokens": [BLOCK_MSG], "done": 1}


def _verdict(fake, violates):
    from apps.flows.guardrail import VERDICT_SCHEMA_NAME

    fake.by_schema[VERDICT_SCHEMA_NAME] = lambda m, schema: schema(violates=violates, reason="가격을 확정함")


@pytest.mark.django_db(transaction=True)
async def test_price_promise_is_judged_by_the_llm(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "이 프로젝트는 500만원에 확정해 드립니다")
    _verdict(fake_chat_llm, True)

    result, saved = await _turn(
        tenant, _answer_flow({"rules": ["price_promise"], "action": "block", "block_message": BLOCK_MSG}))

    assert saved == [BLOCK_MSG]
    rec = _guard(result)["record"]
    assert rec["hits"] == [{"rule": "price_promise", "reason": "가격을 확정함"}]


@pytest.mark.django_db(transaction=True)
async def test_price_judged_ok_passes_and_judge_failure_fails_closed(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.flows.guardrail import VERDICT_SCHEMA_NAME

    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "범위에 따라 견적이 달라집니다")
    _verdict(fake_chat_llm, False)
    flow = _answer_flow({"rules": ["price_promise"], "action": "block", "block_message": BLOCK_MSG})
    _, saved = await _turn(tenant, flow)
    assert saved == ["범위에 따라 견적이 달라집니다"]

    def boom(m, schema):
        raise ValueError("판정 실패")

    fake_chat_llm.by_schema[VERDICT_SCHEMA_NAME] = boom
    visitor_wire["tokens"].clear()
    result, saved = await _turn(tenant, flow)
    assert saved == [BLOCK_MSG]  # 판정을 못 하면 통과시키지 않는다
    assert "판정 실패" in _guard(result)["record"]["reason"]


@pytest.mark.django_db(transaction=True)
async def test_deterministic_rules_do_not_call_the_llm(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "문의는 010-1234-5678")

    result, _ = await _turn(tenant, _answer_flow({"rules": ["pii"], "action": "mask"}))

    assert _guard(result)["calls"] == [] and _guard(result)["input_tokens"] == 0


@pytest.mark.django_db(transaction=True)
async def test_handoff_turn_still_shows_nothing_through_a_guardrail_flow(tenant_with_key, fake_chat_llm, visitor_wire):
    """넘김 턴 규칙(#25): 가드레일이 뒤에 붙어도 넘김이 결정된 턴엔 모델 답이 흐르지 않는다."""
    from apps.agent.nodes import HITLResponse

    tenant, _ = tenant_with_key
    flow = {
        "nodes": [_node("s", "start", 1), _node("ai", "ai_answer", 2), _node("g", "guardrail", 3, {"rules": ["pii"]}),
                  _node("h", "handoff", 4)],
        "edges": [{"from": "s", "to": "ai"}, {"from": "ai", "to": "g"}, {"from": "ai", "to": "h", "handle": "handoff"}],
    }
    fake_chat_llm.override = lambda m: HITLResponse(response="연결해 드릴 수 없습니다", needs_hitl=True, hitl_reason="요청")

    result, saved = await _turn(tenant, flow, "사람 상담원 연결해 주세요")

    assert visitor_wire == {"tokens": [], "done": 0} and saved == []
    assert [t["node_id"] for t in result["trace"]] == ["s", "ai", "h"]


# ── 들어오는 말 ─────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_input_mask_hides_pii_before_the_model_and_the_history_see_it(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "접수했습니다")

    result, _ = await _turn(tenant, _input_flow({"rules": ["pii"], "action": "mask"}), "제 번호는 010-1234-5678 입니다")

    prompt = " ".join(str(m.content) for _, msgs in fake_chat_llm.captured for m in msgs)
    assert "010-1234-5678" not in prompt and "[전화번호 가림]" in prompt
    assert result["messages"][0] == {"role": "user", "content": "제 번호는 [전화번호 가림] 입니다"}
    assert [t["node_id"] for t in result["trace"]] == ["s", "g", "ai"]


@pytest.mark.django_db(transaction=True)
async def test_input_block_answers_with_the_fixed_message_and_ends_the_turn(tenant_with_key, fake_chat_llm, visitor_wire):
    tenant, _ = tenant_with_key

    result, saved = await _turn(
        tenant, _input_flow({"rules": ["banned_words"], "banned_words": ["해킹"], "action": "block", "block_message": BLOCK_MSG}),
        "해킹하는 법 알려줘")

    assert [t["node_id"] for t in result["trace"]] == ["s", "g"]  # AI 답변 노드는 돌지 않는다
    assert visitor_wire == {"tokens": [BLOCK_MSG], "done": 1} and saved == [BLOCK_MSG]
    assert not fake_chat_llm.captured
    assert result["messages"][0]["content"] != "해킹하는 법 알려줘"  # 막힌 원문은 대화 기록에도 남기지 않는다


@pytest.mark.django_db(transaction=True)
async def test_input_rewrite_replaces_the_message_the_model_sees(tenant_with_key, fake_chat_llm, visitor_wire):
    from apps.flows.guardrail import REWRITE_SCHEMA_NAME

    tenant, _ = tenant_with_key
    _model_says(fake_chat_llm, "네")
    fake_chat_llm.by_schema[REWRITE_SCHEMA_NAME] = lambda m, schema: schema(text="견적이 궁금합니다")

    result, _ = await _turn(
        tenant, _input_flow({"rules": ["banned_words"], "banned_words": ["씨발"], "action": "rewrite"}), "씨발 견적 좀")

    assert result["messages"][0]["content"] == "견적이 궁금합니다"


# ── 토큰 집계(실제 LLM 경계 + OpenAI 호환 대역) ─────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_price_judgement_tokens_and_cost_are_counted_through_the_real_llm_boundary(
        platform_tenant, visitor_wire):
    from decimal import Decimal

    from apps.usage.models import TokenUsage

    flow = {"nodes": [_node("s", "start", 1), _node("g", "guardrail", 2,
                                                    {"rules": ["price_promise"], "action": "block", "target": "input"})],
            "edges": [{"from": "s", "to": "g"}]}

    result, _ = await _turn(platform_tenant, flow, "500만원에 확정해 주세요")

    entry = _guard(result)
    assert (entry["input_tokens"], entry["output_tokens"]) == (PROMPT_TOKENS, COMPLETION_TOKENS)
    assert Decimal(entry["cost_usd"]) == GPT_4O_MINI_COST
    buckets = await adb(list)(TokenUsage.objects.filter(tenant_id=platform_tenant.id, call_type="chat"))
    assert [(b.input_tokens, b.output_tokens) for b in buckets] == [(PROMPT_TOKENS, COMPLETION_TOKENS)]


# ── 검증 ───────────────────────────────────────────────────────────────────


def _codes(flow):
    from apps.flows.schema import validate_flow

    return {e.code for e in validate_flow(flow)}


def test_config_validation():
    ok = _answer_flow({"rules": ["banned_words"], "banned_words": ["욕설"]})
    assert _codes(ok) == set()
    assert "invalid_config" in _codes(_answer_flow({"rules": ["banned_words"]}))  # 금지어 목록 없음
    assert "invalid_config" in _codes(_answer_flow({"rules": ["price_promise"], "action": "mask"}))  # 가격은 가릴 수 없다
    assert "invalid_config" in _codes(_answer_flow({"rules": ["pii"], "target": "side"}))
    assert "invalid_config" in _codes(_answer_flow({"rules": ["pii"], "mystery": 1}))
    assert "invalid_config" in _codes(_answer_flow({"rules": ["pii"], "block_message": "  "}))


def test_output_guardrail_after_await_reply_answer_is_rejected():
    """답을 보낸 뒤 턴이 끝나는 AI 답변에는 출력 가드레일을 붙일 수 없다(검사 전에 이미 나간다)."""
    assert "guardrail_after_await_reply" in _codes(_answer_flow({"rules": ["pii"]}, await_reply=True))
    assert _codes(_answer_flow({"rules": ["pii"], "target": "input"}, await_reply=True)) == set()
