"""AI 분류·추출 노드와 조건 노드(#25). LLM은 결정적 Fake(usage 보고 포함), 나머지는 실제 객체."""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _flow(classify_config, branches):
    return {
        "nodes": [
            _node("s", "start", 1),
            _node("c", "ai_classify", 2, classify_config),
            _node("r", "condition", 3, {"branches": branches}),
            _node("refund", "ai_answer", 4, {"instructions": "환불 절차를 안내한다"}),
            _node("human", "handoff", 5, {"reason": "확신이 낮음"}),
            _node("general", "ai_answer", 6, {"instructions": "일반 답변"}),
        ],
        "edges": [
            {"from": "s", "to": "c"}, {"from": "c", "to": "r"},
            {"from": "r", "to": "refund", "handle": "refund"},
            {"from": "r", "to": "human", "handle": "unsure"},
            {"from": "r", "to": "general", "handle": "else"},
        ],
    }


BRANCHES = [{"name": "unsure", "when": "confidence < 0.6"}, {"name": "refund", "when": "label == 'refund'"}]
LABELS = {"instructions": "문의 의도를 분류한다", "labels": ["refund", "other"]}


def _classifier(fake, **result):
    def make(messages, schema):
        return schema(**result)

    fake.by_schema["ClassifyResult"] = make


async def _turn(tenant, flow, message="환불하고 싶어요"):
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-cls")
    return await run_flow_turn_async(session, message, flow), session


@pytest.mark.django_db(transaction=True)
async def test_classify_then_condition_routes_by_label(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="refund", confidence=0.92)

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES))

    assert [t["node_id"] for t in result["trace"]] == ["s", "c", "r", "refund"]
    assert result["label"] == "refund" and result["confidence"] == 0.92
    cond = result["trace"][2]
    assert cond["handle"] == "refund"
    assert "label == 'refund'" in cond["record"]["reason"]  # 고른 갈래와 이유가 판단 기록에 남는다


@pytest.mark.django_db(transaction=True)
async def test_confidence_below_threshold_takes_the_unsure_branch_even_when_label_matches(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="refund", confidence=0.4)

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES))

    assert result["trace"][2]["handle"] == "unsure"  # 첫 번째로 참인 갈래
    assert [t["node_id"] for t in result["trace"]][-1] == "human"


@pytest.mark.django_db(transaction=True)
async def test_no_branch_matches_takes_else(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="other", confidence=0.95)

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES), "안녕하세요")

    cond = result["trace"][2]
    assert cond["handle"] == "else"
    assert cond["record"]["branch"] == "else"
    assert [t["node_id"] for t in result["trace"]][-1] == "general"


@pytest.mark.django_db(transaction=True)
async def test_classify_records_tokens_and_cost_from_the_fake_usage_and_uses_the_chat_provider(tenant_with_key, fake_chat_llm):
    from apps.agent.providers import chat_provider
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="other", confidence=0.9)

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES), "안녕하세요")

    entry = result["trace"][1]
    assert (entry["input_tokens"], entry["output_tokens"]) == (120, 30)
    assert entry["cost_usd"] is not None
    assert entry["record"]["output"]["label"] == "other" and entry["record"]["output"]["confidence"] == 0.9
    expected = await adb(lambda: chat_provider(TenantConfig.objects.get(tenant=tenant)))()
    assert fake_chat_llm.last_provider == expected  # 노드가 클라이언트를 직접 만들지 않는다
    assert result["trace"][2]["input_tokens"] == 0  # 조건 노드는 LLM을 부르지 않는다


FIELDS = {"instructions": "요구사항을 뽑는다", "fields": [
    {"name": "budget", "description": "예산(원)", "type": "number"},
    {"name": "deadline", "description": "기한", "type": "string"},
    {"name": "requirements_complete", "description": "목적·규모·기한이 모두 나왔나", "type": "boolean"},
]}


@pytest.mark.django_db(transaction=True)
async def test_extraction_fields_land_in_state_and_a_condition_can_read_them(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, fields={"budget": 3000000, "deadline": "3개월", "requirements_complete": True},
                confidence=0.9)
    branches = [{"name": "refund", "when": "fields.requirements_complete == true and fields.budget >= 1000000"},
                {"name": "unsure", "when": "confidence < 0.1"}]

    result, _ = await _turn(tenant, _flow(FIELDS, branches), "예산 300만원, 3개월 안에요")

    assert result["fields"] == {"budget": 3000000, "deadline": "3개월", "requirements_complete": True}
    assert result["trace"][2]["handle"] == "refund"
    assert result["trace"][1]["record"]["missing"] == []


@pytest.mark.django_db(transaction=True)
async def test_missing_extraction_fields_are_null_and_listed_in_the_record(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, fields={"budget": None, "deadline": "", "requirements_complete": False}, confidence=0.8)
    branches = [{"name": "refund", "when": "fields.requirements_complete == true"},
                {"name": "unsure", "when": "confidence < 0.1"}]

    result, _ = await _turn(tenant, _flow(FIELDS, branches), "견적 문의요")

    assert result["fields"]["budget"] is None and result["fields"]["deadline"] is None  # 빈 문자열도 누락
    assert result["fields"]["requirements_complete"] is False  # false는 누락이 아니다
    assert result["trace"][1]["record"]["missing"] == ["budget", "deadline"]
    assert result["trace"][2]["handle"] == "else"  # 누락이어도 흐름은 기본 갈래로 계속된다


@pytest.mark.django_db(transaction=True)
async def test_confidence_is_clamped_to_zero_one(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="refund", confidence=7)

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES))

    assert result["confidence"] == 1.0


@pytest.mark.django_db(transaction=True)
async def test_classify_prompt_carries_labels_fields_instructions_and_user_message_as_data(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    _classifier(fake_chat_llm, label="refund", confidence=0.9)

    await _turn(tenant, _flow(LABELS, BRANCHES), "환불하고 싶어요")

    name, messages = fake_chat_llm.captured[0]
    assert name == "ClassifyResult"
    text = "\n".join(str(m.content) for m in messages)
    assert "문의 의도를 분류한다" in text and "refund" in text and "환불하고 싶어요" in text


@pytest.mark.django_db(transaction=True)
async def test_classify_failure_does_not_break_the_turn_and_falls_to_else(tenant_with_key, fake_chat_llm):
    """모델이 깨진 출력을 내면 확신도 0·라벨 없음으로 이어 간다(조건의 문턱 갈래가 받는다)."""
    tenant, _ = tenant_with_key

    def boom(messages, schema):
        raise ValueError("구조화 출력 파싱 실패")

    fake_chat_llm.by_schema["ClassifyResult"] = boom

    result, _ = await _turn(tenant, _flow(LABELS, BRANCHES))

    assert result["confidence"] == 0.0 and result["label"] == ""
    assert result["trace"][2]["handle"] == "unsure"
    assert "실패" in result["trace"][1]["record"]["reason"]


@pytest.mark.django_db(transaction=True)
async def test_example_flow_runs_one_turn_up_to_the_http_node(tenant_with_key, fake_chat_llm):
    """예제 ①: 전체가 지어지고, HTTP 노드 앞까지의 분기가 한 턴에 돈다."""
    import json
    from pathlib import Path

    from apps.flows.executor import compile_flow

    tenant, _ = tenant_with_key
    graph = json.loads((Path(__file__).parent.parent / "apps/flows/examples/sandcorp_home.json").read_text())
    compile_flow(graph)  # 모든 노드 종류에 핸들러가 있다(HTTP 도구 #34까지)

    # HTTP 노드만 빼고 같은 분기를 돈다(HTTP 호출은 test_flow_http_tool.py) — 가격 약속 판정은 걸리지 않게 둔다
    trimmed = {"nodes": [n for n in graph["nodes"] if n["kind"] != "http_tool"],
               "edges": [e for e in graph["edges"] if "submit" not in {e["from"], e["to"]}]}
    fake_chat_llm.by_schema["GuardrailVerdict"] = lambda m, schema: schema(violates=False)
    _classifier(fake_chat_llm, label="project_inquiry", confidence=0.9,
                fields={"requirements_complete": True})
    result, _ = await _turn(tenant, trimmed, "쇼핑몰 만들고 싶어요")
    assert [t["node_id"] for t in result["trace"]] == ["start", "classify", "route", "enough", "cases", "draft", "guard"]

    _classifier(fake_chat_llm, label="project_inquiry", confidence=0.9, fields={"requirements_complete": False})
    result, _ = await _turn(tenant, trimmed, "쇼핑몰 만들고 싶어요")
    assert [t["node_id"] for t in result["trace"]] == ["start", "classify", "route", "enough", "ask"]

    _classifier(fake_chat_llm, label="human_request", confidence=0.9, fields={"requirements_complete": False})
    result, _ = await _turn(tenant, trimmed, "사람이랑 얘기하고 싶어요")
    assert [t["node_id"] for t in result["trace"]] == ["start", "classify", "route", "handoff"]
