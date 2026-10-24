"""판단 기록(Decision Trace)과 모델 단가 표(#24, ADR-0027).

운영 한 턴(chat_turn)과 시험 한 턴(run_test_turn)이 같은 형식으로 기록한다. LLM은 결정적 Fake(conftest autouse)가
Provider 응답의 usage를 돌려주는 것처럼 보고한다. DB·체크포인터는 실제 객체.
"""
from decimal import Decimal

import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


# ── 모델 단가 표 ──────────────────────────────────────────────────────────────


def _embedding_of(row) -> tuple[int, bool]:
    """지식 검색 노드의 질의 임베딩(실제 임베딩 서버 응답) 토큰 수와 단가를 모르는지 — 턴 합계에 더해진다(#85)."""
    search = next((e for e in row.entries if e["kind"] == "knowledge_search"), None)
    tokens = search["input_tokens"] if search else 0
    return tokens, bool(search and tokens and search["cost_usd"] is None)


def test_price_table_prices_platform_models():
    """플랫폼 LLM·임베딩 모델 단가가 표에 있다 — /try 상한과 체험 한도가 이 단가로 계산된다."""
    from django.conf import settings

    from apps.usage.pricing import cost_usd

    assert cost_usd(settings.PLATFORM_LLM_MODEL, 1_000_000, 1_000_000) == Decimal("0.75")  # 0.15 + 0.60
    assert cost_usd("text-embedding-3-small", 1_000_000, 0) == Decimal("0.02")


def test_price_table_resolves_dated_model_names_and_leaves_unknown_null():
    from apps.usage.pricing import cost_usd

    assert cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 0) == Decimal("0.15")
    assert cost_usd("some-unknown-model", 1000, 1000) is None


# ── 기록 ─────────────────────────────────────────────────────────────────────


def _session(tenant, **kw):
    from apps.chat.models import ChatSession

    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-trace", **kw)


@pytest.mark.django_db(transaction=True)
async def test_production_turn_records_nodes_tokens_cost(tenant_with_key, fake_chat_llm):
    """운영 한 턴: 기본 흐름이 지난 노드마다 입력·출력·갈래·토큰·비용·시간이 한 행에 남고 합계가 맞는다."""
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    fake_chat_llm.usage = ("gpt-4o-mini", 1000, 200)
    session = await adb(_session)(tenant)

    await run_chat_turn_async(session, "배송은 얼마나 걸려요?")

    rows = await adb(list)(DecisionTrace.objects.filter(session=session))
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id == tenant.id and row.is_test is False
    kinds = [e["kind"] for e in row.entries]
    assert kinds[0] == "start" and "ai_answer" in kinds
    answer = next(e for e in row.entries if e["kind"] == "ai_answer")
    assert answer["record"]["input"] == "배송은 얼마나 걸려요?"
    assert answer["record"]["output"]
    assert answer["input_tokens"] == 1000 and answer["output_tokens"] == 200
    assert Decimal(answer["cost_usd"]) == Decimal("0.00027")  # 1000*0.15/1M + 200*0.60/1M
    assert answer["duration_ms"] >= 0
    embed, embed_unknown = _embedding_of(row)
    assert row.input_tokens == 1000 + embed and row.output_tokens == 200
    assert row.cost_usd == (None if embed_unknown else Decimal("0.00027"))
    assert row.duration_ms >= 0


@pytest.mark.django_db(transaction=True)
async def test_unknown_model_leaves_cost_null_but_keeps_tokens(tenant_with_key, fake_chat_llm):
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    fake_chat_llm.usage = ("mystery-model-9", 500, 50)
    session = await adb(_session)(tenant)

    await run_chat_turn_async(session, "안녕")

    row = await adb(DecisionTrace.objects.get)(session=session)
    answer = next(e for e in row.entries if e["kind"] == "ai_answer")
    assert answer["cost_usd"] is None and answer["input_tokens"] == 500
    assert row.cost_usd is None and row.input_tokens == 500 + _embedding_of(row)[0]


@pytest.mark.django_db(transaction=True)
async def test_token_usage_rollup_gets_cost_column(tenant_with_key, fake_chat_llm):
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.usage.models import TokenUsage

    tenant, _ = tenant_with_key
    fake_chat_llm.usage = ("gpt-4o-mini", 1000, 200)
    session = await adb(_session)(tenant)

    await run_chat_turn_async(session, "안녕")

    bucket = await adb(TokenUsage.objects.get)(tenant_id=tenant.id, call_type="chat", model="gpt-4o-mini")
    assert bucket.input_tokens == 1000 and bucket.cost_usd == Decimal("0.00027")


@pytest.mark.django_db(transaction=True)
async def test_redaction_hook_runs_just_before_saving(tenant_with_key, monkeypatch):
    """비밀 가림 훅은 저장 직전 모든 문자열에 적용된다 — 기본은 Tenant Secret 가림(#33) 하나."""
    from apps.flows import trace
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace

    from apps.tenants.tenant_secrets import redact

    assert trace.REDACTORS == [redact]
    monkeypatch.setattr(trace, "REDACTORS", [lambda s: s.replace("sk-secret-123", "[비밀]")])
    tenant, _ = tenant_with_key
    session = await adb(_session)(tenant)

    await run_chat_turn_async(session, "내 키는 sk-secret-123 이에요")

    row = await adb(DecisionTrace.objects.get)(session=session)
    dumped = str(row.entries)
    assert "sk-secret-123" not in dumped and "[비밀]" in dumped


@pytest.mark.django_db(transaction=True)
async def test_long_input_and_output_are_truncated(tenant_with_key):
    from apps.flows import trace
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    session = await adb(_session)(tenant)

    await run_chat_turn_async(session, "가" * 10_000)

    row = await adb(DecisionTrace.objects.get)(session=session)
    answer = next(e for e in row.entries if e["kind"] == "ai_answer")
    assert len(answer["record"]["input"]) <= trace.MAX_FIELD_CHARS + 20
    assert answer["record"]["input"].endswith("…(잘림)")


@pytest.mark.django_db(transaction=True)
async def test_test_turn_records_same_shape_as_production(tenant_with_key, fake_chat_llm):
    """시험 한 턴(서비스 층·MCP)도 같은 형식으로 남고 is_test로 구분되며, 결과 trace에 토큰·비용이 실린다."""
    from apps.flows import services
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    fake_chat_llm.usage = ("gpt-4o-mini", 1000, 200)
    flow = await adb(services.create_flow)(tenant.id, "시험", None)
    from apps.flows.defaults import DEFAULT_FLOW

    await adb(services.save_draft)(tenant.id, flow.id, DEFAULT_FLOW)

    result = await services.run_test_turn(tenant.id, flow.id, "안녕하세요")

    assert result.ran
    row = await adb(DecisionTrace.objects.get)(session_id=result.session_id)
    assert row.is_test is True and row.cost_usd == (None if _embedding_of(row)[1] else Decimal("0.00027"))
    answer = next(t for t in result.trace if t["kind"] == "ai_answer")
    assert answer["input_tokens"] == 1000 and answer["cost_usd"] == "0.00027" and "duration_ms" in answer
    assert answer["record"]["output"]


# ── 조회 API ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_api_lists_turns_for_own_session_only(client, tenant_admin_token, tenant_with_key):
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.tenants.models import Tenant

    tenant, _ = tenant_with_key
    session = await adb(_session)(tenant)
    await run_chat_turn_async(session, "첫 질문")
    await run_chat_turn_async(session, "두 번째 질문")

    def fetch(sid, token=tenant_admin_token):
        return client.get(f"/api/tenant/sessions/{sid}/decision-traces", HTTP_AUTHORIZATION=f"Bearer {token}")

    resp = await adb(fetch)(session.id)
    assert resp.status_code == 200
    turns = resp.json()["turns"]
    assert [t["turn"] for t in turns] == [1, 2]
    assert turns[0]["entries"][0]["kind"] == "start" and "cost_usd" in turns[0]

    other = await adb(Tenant.objects.create_with_key)(name="Other", raw_key="k-other-123456789")
    foreign = await adb(_session)(other)
    assert (await adb(fetch)(foreign.id)).status_code == 404
    assert (await adb(client.get)(f"/api/tenant/sessions/{session.id}/decision-traces")).status_code == 401
