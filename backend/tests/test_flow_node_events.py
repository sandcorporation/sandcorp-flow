"""시험 대화 노드 이벤트(#239, 우산 #238) — 실행기가 노드 앞뒤·예외 때 sink로 이벤트를 내고, 시험 대화 SSE가 턴 도중에
``node_start``/``node_end``/``node_error``를 흘린다.

- LLM만 Fake(conftest ``fake_chat_llm`` — ``delay``·``raises``), DB·인증·실행기·SSE는 실제 객체다.
- HTTP 노드는 예약 호스트(가짜 API)를 프로세스 안에서 부른다(``net`` 대역).
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets as pysecrets
import time
from collections.abc import AsyncGenerator

import pytest
from asgiref.sync import sync_to_async as adb
from django.test.client import AsyncClient

from apps.flows.schema import JSON
from tests.test_flow_api import BASE, OPS, admin_token, jpost, new_flow, sse_events  # noqa: F401
from tests.test_flow_http_tool import net, tool_pki  # noqa: F401

NODE_END_KEYS = {"node_id", "kind", "step", "handle", "duration_ms", "input_tokens", "output_tokens", "cost_usd",
                 "unpriced_models", "summary"}


def _graph(example_id: str) -> JSON:
    from apps.flows.example_catalog import example_graph

    return example_graph(example_id)


def _classify(fake, **result) -> None:
    fake.by_schema["ClassifyResult"] = lambda messages, schema: schema(**result)


async def _alines(response):
    """AsyncClient 스트림에서 (도착 시각, 이벤트 이름, 데이터)를 하나씩 꺼낸다."""
    buffer = ""
    async for chunk in response.streaming_content:
        buffer += chunk.decode() if isinstance(chunk, bytes) else chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            yield time.monotonic(), lines["event"], json.loads(lines["data"])


def _apost(token: str, flow_id: str, message: str, **extra):
    return AsyncClient().post(f"{BASE}{flow_id}/test/", data=json.dumps({"message": message, **extra}),
                              content_type="application/json", headers={"authorization": f"Bearer {token}"})


# ── 순서·필드 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_node_events_come_between_start_and_trace(client, tenant_with_key, admin_token, fake_chat_llm):
    fid = new_flow(client, admin_token)

    events = sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "영업시간이 어떻게 되나요?"}, admin_token))

    assert [n for n, _ in events] == ["start", "node_start", "node_end", "node_start", "node_end",
                                      "trace", "trace", "reply", "done"]
    starts = [d for n, d in events if n == "node_start"]
    ends = [d for n, d in events if n == "node_end"]
    traces = [d for n, d in events if n == "trace"]
    assert starts == [{"node_id": "start", "kind": "start", "step": 0}, {"node_id": "answer", "kind": "ai_answer", "step": 1}]
    assert all(set(e) == NODE_END_KEYS for e in ends)
    # node_end의 수치는 턴 뒤 trace 항목과 같다(step = trace 순번)
    for e in ends:
        t = traces[e["step"]]
        assert (e["node_id"], e["handle"], e["duration_ms"], e["input_tokens"], e["output_tokens"], e["cost_usd"],
                e["unpriced_models"]) == (t["node_id"], t["handle"], t["duration_ms"], t["input_tokens"],
                                          t["output_tokens"], t["cost_usd"], t["unpriced_models"])
    assert ends[0]["summary"] is None  # 시작 노드는 요약이 없다
    reply = dict(events)["reply"]["text"]
    assert ends[1]["summary"] == f"답 {len(reply)}자" and ends[1]["input_tokens"] == 120


@pytest.mark.django_db(transaction=True)
def test_unpriced_model_shows_on_node_end(client, tenant_with_key, admin_token, fake_chat_llm):
    fake_chat_llm.usage = ("acme/unknown-model", 50, 10)
    fid = new_flow(client, admin_token)

    ends = [d for n, d in sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token))
            if n == "node_end"]

    assert ends[1]["unpriced_models"] == ["acme/unknown-model"] and ends[1]["cost_usd"] is None
    assert ends[0]["unpriced_models"] == [] and ends[0]["cost_usd"] == "0"


@pytest.mark.django_db(transaction=True)
def test_invalid_draft_still_streams_only_start_and_done(client, admin_token, fake_chat_llm):
    fid = new_flow(client, admin_token, ops=None)
    events = sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token))
    assert [n for n, _ in events] == ["start", "done"]


# ── 실시간 ───────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_first_node_start_arrives_before_the_last_node_ends(tenant_with_key, tenant_admin_token, fake_chat_llm):
    """LLM이 느려도 앞 노드 이벤트는 턴이 끝나기 전에 도착한다 — 모았다 한꺼번에 보내지 않는다."""
    from tests.test_flow_services import make_flow

    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    fake_chat_llm.delay = 0.6

    response = await _apost(tenant_admin_token, str(flow.id), "안녕하세요")
    seen = [(t, name, data) async for t, name, data in _alines(response)]

    at = {(name, data.get("node_id")): t for t, name, data in seen}
    assert at[("node_end", "answer")] - at[("node_start", "start")] >= 0.5
    assert at[("node_start", "answer")] < at[("node_end", "answer")] - 0.5
    assert [name for _, name, _ in seen][-1] == "done"


@pytest.mark.django_db(transaction=True)
async def test_heartbeat_comment_lines_come_between_node_events(tenant_with_key, tenant_admin_token, fake_chat_llm, settings):
    """LLM을 기다리는 동안 SSE 주석 줄(`:`)이 간격마다 오고, 이벤트 이름·순서는 그대로다(#253)."""
    from tests.test_flow_services import make_flow

    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    settings.TEST_SSE_HEARTBEAT_SECONDS = 0.1
    fake_chat_llm.delay = 0.7

    response = await _apost(tenant_admin_token, str(flow.id), "안녕하세요")
    buffer, blocks = "", []
    async for chunk in response.streaming_content:
        buffer += chunk.decode() if isinstance(chunk, bytes) else chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            blocks.append(block)

    names = [b.splitlines()[0].removeprefix("event: ") for b in blocks if b.startswith("event: ")]
    assert names == ["start", "node_start", "node_end", "node_start", "node_end", "trace", "trace", "reply", "done"]
    beats = [i for i, b in enumerate(blocks) if b.startswith(":")]
    assert len(beats) >= 3
    answer_start = next(i for i, b in enumerate(blocks) if b.startswith("event: node_start") and '"answer"' in b)
    answer_end = next(i for i, b in enumerate(blocks) if b.startswith("event: node_end") and '"answer"' in b)
    assert all(answer_start < i < answer_end for i in beats)  # 노드가 끝난 뒤(trace 이후)에는 오지 않는다


# ── 종류별 summary ───────────────────────────────────────────────────────────


async def _summaries(tenant, graph: JSON, message: str) -> dict[str, str | None]:
    from apps.flows import services

    flow = await adb(services.create_flow)(tenant.id, f"f-{pysecrets.token_hex(3)}", graph)
    got: list[tuple[str, dict]] = []
    result = await services.run_test_turn(tenant.id, flow.id, message, on_node_event=lambda n, d: got.append((n, d)))
    assert result.ran, result
    return {str(d["node_id"]): (str(d["summary"]) if d["summary"] is not None else None) for n, d in got if n == "node_end"}


@pytest.mark.django_db(transaction=True)
async def test_summary_per_kind_on_the_order_lookup(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": "10021"})

    got = await _summaries(tenant, _graph("shop_support"), "10021 어디쯤이에요?")

    assert list(got) == ["start", "classify", "route", "has_order", "order", "result", "say_shipping"]
    assert got == {**got, "start": None, "classify": "라벨 'order_status' · 확신도 0.90", "route": "갈래 'status'",
                   "has_order": "갈래 'given'", "order": "HTTP 200", "result": "갈래 'shipping'"}
    assert re.fullmatch(r"답 \d+자", got["say_shipping"] or "")


@pytest.mark.django_db(transaction=True)
async def test_summary_for_http_failure_knowledge_and_handoff(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": "9500"})
    failed = await _summaries(tenant, _graph("shop_support"), "9500")
    assert re.fullmatch(r"실패\(\w+\)", failed["order"] or "")

    _classify(fake_chat_llm, label="return_or_faq", confidence=0.9, fields={"order_no": None})
    policy = await _summaries(tenant, _graph("shop_support"), "반품하고 싶어요")
    assert policy["route"] == "기본 갈래" and re.fullmatch(r"조각 \d+개", policy["policy"] or "")

    _classify(fake_chat_llm, label="human_request", confidence=0.9, fields={"order_no": None})
    human = await _summaries(tenant, _graph("shop_support"), "사람이랑 얘기할래요")
    assert human["handoff"] == "넘김"


@pytest.mark.django_db(transaction=True)
async def test_summary_for_guardrail(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    fake_chat_llm.by_schema["GuardrailVerdict"] = lambda m, schema: schema(violates=False)
    _classify(fake_chat_llm, label="course_info", confidence=0.9, fields={"date": None, "level": None, "name": None})

    got = await _summaries(tenant, _graph("academy_reservation"), "초급반은 언제 해요?")

    assert got["guard"] == "통과"


# ── 가림 ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_secret_never_reaches_node_end_or_node_error(client, tenant_with_key, admin_token, fake_chat_llm):
    """노드 이벤트 문자열도 저장 trace와 같은 가림을 거친다 — 실시간 경로로 비밀이 새지 않는다."""
    from apps.tenants.tenant_secrets import set_secret

    tenant, _ = tenant_with_key
    secret = "sk-" + pysecrets.token_hex(12)
    set_secret(tenant.id, "shop_key", secret)

    # node_end.summary: 분류 라벨이 비밀과 같은 문자열
    _classify(fake_chat_llm, label=secret, confidence=0.9)
    fid = new_flow(client, admin_token, ops=[
        {"op": "upsert_node", "id": "start", "kind": "start", "code": "N-01"},
        {"op": "upsert_node", "id": "cls", "kind": "ai_classify", "code": "N-02",
         "config": {"labels": [secret, "기타"]}},
        {"op": "connect", "from": "start", "to": "cls"},
    ])
    events = sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token))
    ends = json.dumps([d for n, d in events if n == "node_end"], ensure_ascii=False)
    assert secret not in ends and "[비밀]" in ends

    # node_error.message: Provider 오류 문구가 방문자 입력(비밀 포함)을 되돌려 줘도 가린다
    fake_chat_llm.raises = RuntimeError(f"bad request: {secret}")
    fid = new_flow(client, admin_token, name="오류")
    events = sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token))
    [error] = [d for n, d in events if n == "node_error"]
    assert secret not in error["message"] and "[비밀]" in error["message"]


# ── 예외·끊김 ────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_handler_exception_sends_node_error_then_done(client, tenant_with_key, admin_token, fake_chat_llm):
    fake_chat_llm.raises = RuntimeError("upstream 502")
    fid = new_flow(client, admin_token)

    events = sse_events(jpost(client, f"{BASE}{fid}/test/", {"message": "안녕"}, admin_token))

    assert [n for n, _ in events] == ["start", "node_start", "node_end", "node_start", "node_error", "done"]
    error = dict(events)["node_error"]
    assert error == {"node_id": "answer", "kind": "ai_answer", "step": 1, "message": error["message"]}
    assert "upstream 502" in error["message"] and "오류" in error["message"]
    done = dict(events)["done"]
    assert done["ran"] is False and done["code"] == "turn_error" and done["note"] and done["session_id"]


@pytest.mark.django_db(transaction=True)
async def test_disconnected_client_does_not_stop_the_turn(tenant_with_key, tenant_admin_token, fake_chat_llm):
    """첫 노드 이벤트 뒤 연결을 끊어도 턴은 끝까지 돌고 판단 기록이 남는다."""
    from apps.flows.models import DecisionTrace
    from tests.test_flow_services import make_flow

    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    fake_chat_llm.delay = 0.3

    response = await _apost(tenant_admin_token, str(flow.id), "안녕하세요")
    stream: AsyncGenerator[bytes, None] = getattr(response, "streaming_content")  # AsyncClient: 닫을 수 있는 async 반복
    async for chunk in stream:
        if b"node_start" in (chunk if isinstance(chunk, bytes) else chunk.encode()):
            break
    await stream.aclose()  # ASGI 서버가 끊긴 연결의 응답 반복을 닫는 것과 같다

    count = lambda: DecisionTrace.objects.filter(tenant_id=tenant.id, is_test=True).count()  # noqa: E731
    for _ in range(100):
        if await adb(count)():
            break
        await asyncio.sleep(0.05)
    entries = await adb(lambda: DecisionTrace.objects.get(tenant_id=tenant.id).entries)()
    assert [e["node_id"] for e in entries] == ["start", "answer"]


# ── 운영 턴은 그대로 ─────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_operational_turn_is_untouched_by_a_concurrent_test_turn(tenant_with_key, fake_chat_llm):
    """sink는 시험 턴의 맥락에만 있다 — 같은 때 도는 운영 턴(sink 없음)의 노드는 시험 턴 sink에 섞이지 않고,
    운영 턴의 trace·비용은 sink 없던 때와 같다."""
    from apps.chat.models import ChatSession
    from apps.flows import services
    from apps.flows.executor import run_flow_turn_async
    from tests.test_flow_services import make_flow

    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    graph = await adb(_graph_of)(tenant.id, flow.id)
    fake_chat_llm.delay = 0.2
    got: list[str] = []

    async def operational():
        session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-ops")
        return await run_flow_turn_async(session, "안녕하세요", graph)

    state, test = await asyncio.gather(
        operational(),
        services.run_test_turn(tenant.id, flow.id, "안녕하세요", on_node_event=lambda n, d: got.append(f"{n}:{d['node_id']}")),
    )

    assert test.ran
    assert got == ["node_start:start", "node_end:start", "node_start:answer", "node_end:answer"]
    assert [t["node_id"] for t in state["trace"]] == ["start", "answer"]
    assert state["trace"][1]["cost_usd"] == test.trace[1]["cost_usd"] and state["trace"][1]["input_tokens"] == 120


def _graph_of(tenant_id, flow_id) -> JSON:
    from apps.flows.services import flow_detail

    return flow_detail(tenant_id, flow_id).draft.graph
