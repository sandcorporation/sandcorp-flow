"""「단가 모름」 기록(#235) — 이름 맞추기, 롤업의 unpriced_requests, 판단 기록의 일부 미포함, 기존 행 채우기."""
import importlib
import uuid
from decimal import Decimal

import pytest

from apps.usage.models import TokenUsage
from apps.usage.recording import record_usage


@pytest.fixture(autouse=True)
def _clean_token_usage(db):
    TokenUsage.objects.all().delete()


# ── 이름 맞추기 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "claude-haiku-5-5",
    "anthropic/claude-haiku-5-5",
    "anthropic.claude-haiku-5-5",
    "claude-haiku-5-5@20260101",
    "claude-haiku-5-5-20260101",
    "anthropic/claude-haiku-5-5@20260101",
    "Claude-Haiku-5-5",
])
def test_canonical_model_maps_variants_to_table_name(name):
    from apps.usage.pricing import canonical_model, cost_usd, is_priced

    assert canonical_model(name) == "claude-haiku-5-5"
    assert is_priced(name)
    assert cost_usd(name, 1_000_000, 1_000_000) == cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000)


def test_canonical_model_prefers_exact_and_leaves_unknown_none():
    from apps.usage.pricing import canonical_model

    assert canonical_model("openai/gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert canonical_model("claude-opus-5-5") == "claude-opus-5-5"  # claude-opus-5 와 섞지 않는다
    assert canonical_model("openai/mystery-model") is None
    assert canonical_model("") is None and canonical_model(None) is None


# ── 롤업: 단가 모름 호출 수 ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_record_usage_counts_unpriced_requests_only_for_unknown_models():
    tid = uuid.uuid4()
    record_usage(tid, "chat", "gpt-4o-mini", input_tokens=10, output_tokens=5)
    record_usage(tid, "chat", "mystery-model", input_tokens=10)
    record_usage(tid, "chat", "mystery-model", input_tokens=10)

    known = TokenUsage.objects.get(tenant_id=tid, model="gpt-4o-mini")
    unknown = TokenUsage.objects.get(tenant_id=tid, model="mystery-model")
    assert known.unpriced_requests == 0
    assert unknown.unpriced_requests == 2 and unknown.request_count == 2 and unknown.cost_usd == 0


@pytest.mark.django_db
def test_price_added_later_still_reports_partial_not_silent_zero(client, tenant_with_key, tenant_admin_token):
    """단가 모름으로 쌓은 행은 표에 단가가 생겨도 「일부 미포함」으로 남는다(조용한 0원 없음)."""
    tenant, _ = tenant_with_key
    TokenUsage.objects.create(  # 단가가 없던 때 쌓인 행을 그대로 흉내 낸다
        tenant_id=tenant.id, call_type="chat", model="claude-haiku-5-5", date="2026-10-01",
        input_tokens=1000, output_tokens=100, total_tokens=1100, request_count=3, unpriced_requests=3,
    )
    record_usage(tenant.id, "chat", "gpt-4o-mini", input_tokens=1000, output_tokens=200)

    data = client.get("/api/tenant/usage/?days=60", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()

    assert data["unpriced_models"] == ["claude-haiku-5-5"] and data["unpriced_requests"] == 3
    chat = next(b for b in data["by_call_type"] if b["call_type"] == "chat")
    assert chat["unpriced_models"] == ["claude-haiku-5-5"] and chat["unpriced_requests"] == 3
    assert any(d["has_unpriced"] for d in data["daily"])


@pytest.mark.django_db
def test_mcp_get_usage_carries_unpriced_models(tenant_with_key):
    from apps.usage.api import tenant_usage_report

    tenant, _ = tenant_with_key
    record_usage(tenant.id, "chat", "mystery-model", input_tokens=500)
    report = tenant_usage_report(tenant.id)
    assert report["unpriced_models"] == ["mystery-model"] and report["unpriced_requests"] == 1


# ── 기존 행 채우기(마이그레이션) ─────────────────────────────────────────────


@pytest.mark.django_db
def test_migration_backfill_recomputes_priced_zero_rows_and_marks_unpriced_rows():
    from django.apps import apps as django_apps

    mig = importlib.import_module("apps.usage.migrations.0003_unpriced_requests")
    tid = uuid.uuid4()
    mk = lambda model, cost="0", **kw: TokenUsage.objects.create(  # noqa: E731
        tenant_id=tid, call_type="chat", model=model, date="2026-10-01",
        input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, request_count=4,
        cost_usd=Decimal(cost), **kw)
    굳은 = mk("claude-haiku-5-5")                       # #234 이전에 0으로 굳은 행 → 지금 단가로 다시 계산
    날짜꼬리 = mk("anthropic/claude-haiku-4-5@20251001")  # 이름 맞추기로 표에 닿는 행도 같은 경로
    이미 = mk("gpt-4o-mini", cost="0.75")                # 이미 값이 있으면 건드리지 않는다
    모름 = mk("mystery-model")                           # 지금도 단가가 없으면 호출 수만큼 모름 칸을 채운다

    mig.backfill(django_apps, None)

    for row in (굳은, 날짜꼬리, 이미, 모름):
        row.refresh_from_db()
    assert 굳은.cost_usd == Decimal("0.600000") and 굳은.unpriced_requests == 0
    assert 날짜꼬리.cost_usd == Decimal("6.000000") and 날짜꼬리.unpriced_requests == 0
    assert 이미.cost_usd == Decimal("0.750000") and 이미.unpriced_requests == 0
    assert 모름.cost_usd == 0 and 모름.unpriced_requests == 4


# ── 판단 기록: 아는 부분 합 + 일부 미포함 ────────────────────────────────────


def _entry(calls, node="n"):
    return {"node_id": node, "kind": "ai_answer", "handle": "next", "record": {}, "duration_ms": 1,
            "input_tokens": sum(c["input_tokens"] for c in calls), "output_tokens": sum(c["output_tokens"] for c in calls),
            "cost_usd": None, "calls": calls}


def _call(model, cost):
    return {"model": model, "input_tokens": 100, "output_tokens": 10, "cost_usd": cost}


def test_summarize_partial_turn_keeps_known_sum_and_names_unpriced_models():
    from apps.flows.trace import summarize

    entries = [_entry([_call("gpt-4o-mini", "0.001"), _call("mystery-model", None)], "a"),
               _entry([_call("gpt-4o-mini", "0.002")], "b"),
               _entry([], "c")]
    cost, unpriced = summarize(entries)
    assert cost == Decimal("0.003") and unpriced == ["mystery-model"]


def test_summarize_all_unknown_is_null_and_nothing_unknown_is_empty():
    from apps.flows.trace import summarize

    assert summarize([_entry([_call("mystery-model", None)])]) == (None, ["mystery-model"])
    assert summarize([_entry([_call("gpt-4o-mini", "0.001")])]) == (Decimal("0.001"), [])
    assert summarize([_entry([])]) == (Decimal(0), [])


@pytest.mark.django_db(transaction=True)
async def test_unknown_model_node_entry_names_the_model(tenant_with_key, fake_chat_llm):
    from asgiref.sync import sync_to_async

    from apps.chat.models import ChatSession
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    fake_chat_llm.usage = ("mystery-model-9", 500, 50)
    session = await sync_to_async(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-unpriced")

    await run_chat_turn_async(session, "안녕")

    row = await sync_to_async(DecisionTrace.objects.get)(session=session)
    answer = next(e for e in row.entries if e["kind"] == "ai_answer")
    assert answer["unpriced_models"] == ["mystery-model-9"] and answer["cost_usd"] is None
    assert next(e for e in row.entries if e["kind"] == "start")["unpriced_models"] == []


@pytest.mark.django_db
def test_trace_query_reads_legacy_rows_and_names_unpriced_models():
    """예전 기록(턴 비용 null, 항목에 unpriced_models 없음)도 calls 로 아는 부분 합과 모르는 모델을 낸다."""
    from apps.chat.models import ChatSession
    from apps.flows import trace_query
    from apps.flows.models import DecisionTrace
    from tests.mcp_helpers import make_tenant

    tenant = make_tenant(f"t-{uuid.uuid4().hex[:6]}")
    s = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v")
    DecisionTrace.objects.create(
        tenant_id=tenant.id, session=s, input_tokens=200, output_tokens=20, cost_usd=None, duration_ms=3,
        entries=[_entry([_call("gpt-4o-mini", "0.001"), _call("mystery-model", None)])])

    (turn,) = trace_query.session_turns(tenant.id, str(s.id))
    assert turn["cost_usd"] == "0.001" and turn["unpriced_models"] == ["mystery-model"]


def test_try_session_turn_cost_charges_platform_price_for_unpriced_calls_inside_partial_entry(settings):
    from apps.tenants.try_session import turn_cost
    from apps.usage.pricing import cost_usd

    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    entry = _entry([_call("gpt-4o-mini", "0.001"), _call("mystery-model", None)])
    entry["unpriced_models"] = ["mystery-model"]
    expected = Decimal("0.001") + cost_usd("gpt-4o-mini", 100, 10)
    assert turn_cost([entry]) == expected
