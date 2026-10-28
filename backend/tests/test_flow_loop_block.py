"""지식 검색 노드 중 이벤트 루프·SSE 막힘(#256).

운영 측정: kb 노드의 ``duration_ms``는 2810인데 SSE의 kb ``node_start``·``node_end``가 같은 ms에 도착했다.
느린 임베딩(실제 HTTP 수신부에 지연을 넣은 OpenAI 호환 ``/v1/embeddings``)으로 재현하고, 루프 지연 탐침
(``tests.loop_probe.LoopProbe``)으로 「루프가 막혔는가」를 잰다.

기준 200ms: 탐침 주기(50ms)·Docker CPU 잡음보다 넉넉히 크고, 막는 호출이 있으면 넣은 지연(0.8초)만큼 커지므로
둘을 가른다.
"""

from __future__ import annotations

import json
import secrets as pysecrets
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from asgiref.sync import sync_to_async as adb

from apps.flows.schema import JSON
from tests.loop_probe import LoopProbe
from tests.test_flow_http_tool import net, tool_pki  # noqa: F401 — 픽스처
from tests.test_flow_node_events import _alines, _apost, _classify, _graph

LOOP_LAG_LIMIT = 0.2
EMBED_DELAY = 0.8


@dataclass
class SlowEmbeddings:
    delay: float = EMBED_DELAY
    calls: list[float] = field(default_factory=list)


@pytest.fixture
def slow_embeddings(settings) -> Iterator[SlowEmbeddings]:
    """``delay``초 뒤에 답하는 OpenAI 호환 임베딩 수신부. 플랫폼 기본 임베딩(``OLLAMA_BASE_URL``)을 여기로 돌린다."""
    state = SlowEmbeddings()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server 관례
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
            state.calls.append(time.monotonic())
            time.sleep(state.delay)
            texts = body.get("input") or [""]
            payload = json.dumps({
                "object": "list", "model": body.get("model", "slow"),
                "data": [{"object": "embedding", "index": i, "embedding": [0.01] * 1024} for i, _ in enumerate(texts)],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    settings.OLLAMA_BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()
    srv.server_close()


def _kb_graph() -> JSON:
    """start → kb → answer — 운영 측정과 같은 「분류 없이 지식 검색 뒤 답」 갈래만."""
    def node(node_id: str, kind: str, n: int) -> dict[str, JSON]:
        return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id, "position": {"x": 0, "y": 0},
                "config": {}}

    return {"nodes": [node("start", "start", 1), node("kb", "knowledge_search", 2), node("answer", "ai_answer", 3)],
            "edges": [{"from": "start", "to": "kb"}, {"from": "kb", "to": "answer"}]}


def _plain_graph() -> JSON:
    """start → answer — 첫 요청의 모듈 적재(프로세스당 한 번, 수백 ms 루프 점유)를 탐침 밖에서 치르는 워밍업용."""
    graph = _kb_graph()
    return {"nodes": [n for n in graph["nodes"] if n["id"] != "kb"], "edges": [{"from": "start", "to": "answer"}]}


async def _flow(tenant, graph: JSON):
    from apps.flows import services

    return await adb(services.create_flow)(tenant.id, f"f-{pysecrets.token_hex(3)}", graph)


# ── SSE 도착 시각 ────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
async def test_kb_node_start_arrives_before_the_slow_search_ends(tenant_with_key, tenant_admin_token, fake_chat_llm,
                                                                 slow_embeddings):
    """kb ``node_start``는 검색이 끝나기 전에 도착한다 — 끝날 때 ``node_end``와 함께 오지 않는다."""
    tenant, _ = tenant_with_key
    flow = await _flow(tenant, _kb_graph())

    warm = await _flow(tenant, _plain_graph())
    _ = [x async for x in _alines(await _apost(tenant_admin_token, str(warm.id), "워밍업"))]
    async with LoopProbe() as probe:
        response = await _apost(tenant_admin_token, str(flow.id), "환불 규정이 어떻게 되나요?")
        seen = [(t, name, data) async for t, name, data in _alines(response)]

    at = {(name, data.get("node_id")): t for t, name, data in seen}
    end = next(d for _, n, d in seen if n == "node_end" and d["node_id"] == "kb")
    assert slow_embeddings.calls, "kb가 임베딩을 부르지 않았다 — 재현 조건이 아니다"
    assert int(end["duration_ms"]) >= EMBED_DELAY * 1000
    assert at[("node_start", "kb")] <= at[("node_end", "kb")] - (EMBED_DELAY - 0.2)
    t0 = probe.at[0]  # 실패하면 언제 늦었는지(탐침 시각, 늦음)와 이벤트 도착 시각을 같이 보인다
    late = [(round(t - t0, 3), round(lag, 3)) for t, lag in zip(probe.at, probe.lags) if lag > 0.05]
    arrivals = [(round(t - t0, 3), n, d.get("node_id")) for t, n, d in seen]
    assert probe.max_lag < LOOP_LAG_LIMIT, (late, arrivals)
    assert [name for _, name, _ in seen][-1] == "done"


# ── 루프 지연(노드별) ────────────────────────────────────────────────────────


async def _probe_turn(tenant, graph: JSON, message: str):
    from apps.flows import services

    warm = await _flow(tenant, _plain_graph())
    await services.run_test_turn(tenant.id, warm.id, "워밍업")
    flow = await _flow(tenant, graph)
    async with LoopProbe() as probe:
        result = await services.run_test_turn(tenant.id, flow.id, message)
    assert result.ran, result
    return result, probe


@pytest.mark.django_db(transaction=True)
async def test_loop_stays_responsive_during_slow_knowledge_search(tenant_with_key, fake_chat_llm, slow_embeddings):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="other", confidence=0.9, fields={"requirements_complete": False})

    result, probe = await _probe_turn(tenant, _graph("sandcorp_home"), "회사 소개 좀 해 주세요")

    assert [s["node_id"] for s in result.trace] == ["start", "classify", "route", "kb", "general"]
    assert slow_embeddings.calls
    assert probe.max_lag < LOOP_LAG_LIMIT, probe.max_lag


@pytest.mark.django_db(transaction=True)
async def test_loop_stays_responsive_during_slow_llm_nodes_and_guardrail(tenant_with_key, fake_chat_llm,
                                                                        slow_embeddings):
    """ai_classify·ai_answer(느린 LLM)·guardrail 동안에도 루프가 돈다."""
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="course_info", confidence=0.9, fields={"date": "", "level": "", "name": ""})
    fake_chat_llm.delay = 0.5

    result, probe = await _probe_turn(tenant, _graph("academy_reservation"), "수업 종류가 뭐가 있나요?")

    kinds = [s["kind"] for s in result.trace]
    assert {"ai_classify", "knowledge_search", "ai_answer", "guardrail"} <= set(kinds), kinds
    assert probe.max_lag < LOOP_LAG_LIMIT, probe.max_lag


@pytest.mark.django_db(transaction=True)
async def test_loop_stays_responsive_during_http_tool(tenant_with_key, fake_chat_llm, net):
    tenant, _ = tenant_with_key
    _classify(fake_chat_llm, label="order_status", confidence=0.9, fields={"order_no": "10021"})

    result, probe = await _probe_turn(tenant, _graph("shop_support"), "10021 어디쯤이에요?")

    assert "http_tool" in [s["kind"] for s in result.trace]
    assert probe.max_lag < LOOP_LAG_LIMIT, probe.max_lag



# ── 가림 함수(cleaner) ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_cleaner_masks_stored_and_later_derived_secrets_without_db(tenant_with_key, django_assert_num_queries):
    """턴 전에 한 번 만든 가림 함수는 저장된 비밀과 그 뒤에 생긴 파생값을 모두 가리고, 부를 때 DB를 읽지 않는다."""
    from apps.flows import node_events
    from apps.tenants import tenant_secrets as ts

    tenant, _ = tenant_with_key
    stored, derived = "sk-" + pysecrets.token_hex(12), "dXNlcjp" + pysecrets.token_hex(8)
    ts.set_secret(tenant.id, "shop_key", stored)
    clean = node_events.cleaner(tenant.id)
    ts.register_revealed(tenant.id, derived)

    with django_assert_num_queries(0):
        out = clean("node_error", {"node_id": "h", "message": f"401 {stored} / {derived} " + "x" * 400})

    message = str(out["message"])
    assert stored not in message and derived not in message
    assert message.endswith("…(잘림)") and len(message) <= node_events.ERROR_MESSAGE_MAX + len("…(잘림)")
