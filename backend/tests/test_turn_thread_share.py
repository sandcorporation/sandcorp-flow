"""동시 턴이 thread-sensitive 스레드 하나를 나눠 쓸 때의 대기(#262).

운영 위젯 턴은 taskiq 워커(``--max-async-tasks 25``)에서 돈다. 워커에는 요청 경계(``ThreadSensitiveContext``)가 없어
프로세스의 모든 턴이 asgiref의 thread-sensitive 스레드 **하나**를 나눠 쓴다. 이 시험도 같은 조건이다 — 한 이벤트 루프에서
문맥 없이 턴 여럿을 ``asyncio.gather``로 띄운다(시험 대화 ``run_test_turn``은 운영 턴과 같은 실행기·노드를 쓴다).

느린 임베딩은 실제 HTTP 수신부(``slow_embeddings``, 0.8초)다. 고치기 전에는 kb 턴의 임베딩 HTTP가 그 스레드를 쥐어
kb 없는 턴의 DB 단계가 임베딩 시간만큼(kb 턴이 N개면 N배) 기다렸고, kb 턴끼리도 임베딩이 줄을 섰다.

http_tool(#268)의 외부 요청은 처음부터 ``asyncio.to_thread``로 나가 그 스레드를 쥐지 않는다 — 같은 꼴로 재어 고정한다
(느린 외부 API는 ``tests.test_flow_http_tool``의 실제 HTTPS 수신부 ``/slow``, 1.5초).

기준: kb 없는 턴 0.4초 — 혼자 돌 때(0.05초 안팎)와 Docker CPU 잡음보다 넉넉히 크고, 넣은 지연 0.8초보다 충분히 작다.
kb 턴 끝 시각 0.8 + 0.8초 — 임베딩이 겹쳐 돌면 0.8초 남짓, 줄을 서면 N × 0.8초라 둘이 갈린다.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from asgiref.sync import sync_to_async as adb

from tests.test_flow_http_tool import API, _setup, net, tool_pki  # noqa: F401 — 픽스처
from tests.test_flow_http_tool import _flow as _http_graph
from tests.test_flow_loop_block import EMBED_DELAY, _flow, _kb_graph, _plain_graph, slow_embeddings  # noqa: F401

PLAIN_TURN_LIMIT = 0.4
KB_TURNS_LIMIT = EMBED_DELAY + 0.8
HTTP_DELAY = 1.5
"""``tests.test_flow_http_tool`` 수신부의 ``/slow``가 답하기까지."""
HTTP_TURNS_LIMIT = HTTP_DELAY + 0.8


async def _run_turn(tenant_id, flow_id, message: str, t0: float):
    from apps.flows import services

    result = await services.run_test_turn(tenant_id, flow_id, message)
    assert result.ran, result
    return result, time.monotonic() - t0


async def _timed_turn(tenant_id, flow_id, message: str, t0: float) -> float:
    return (await _run_turn(tenant_id, flow_id, message, t0))[1]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_kb", [5, 10])
async def test_plain_turn_does_not_wait_for_concurrent_kb_embeddings(tenant_with_key, fake_chat_llm, slow_embeddings,
                                                                    n_kb):
    """kb 턴 N개가 임베딩 HTTP를 기다리는 동안 띄운 kb 없는 턴은 그 시간만큼 기다리지 않는다. kb 턴끼리도 겹쳐 돈다."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    kb_flow = await _flow(tenant, _kb_graph())
    plain_flow = await _flow(tenant, _plain_graph())
    await services.run_test_turn(tenant.id, plain_flow.id, "워밍업")  # 모듈 적재(프로세스당 한 번)를 재기 밖에서
    alone = await _timed_turn(tenant.id, plain_flow.id, "안녕하세요", time.monotonic())

    t0 = time.monotonic()
    kb_turns = [asyncio.create_task(_timed_turn(tenant.id, kb_flow.id, f"환불 규정 {i}", t0)) for i in range(n_kb)]
    while not slow_embeddings.calls:  # 첫 kb 턴이 임베딩 HTTP에 들어간 뒤에 kb 없는 턴을 띄운다
        await asyncio.sleep(0.005)
    p0 = time.monotonic()
    plain = await _timed_turn(tenant.id, plain_flow.id, "안녕하세요", p0)
    kb_done = await asyncio.gather(*kb_turns)

    print(f"\n[#262] N={n_kb} alone={alone:.3f}s plain={plain:.3f}s kb_max={max(kb_done):.3f}s kb_min={min(kb_done):.3f}s")
    assert len(slow_embeddings.calls) >= n_kb
    assert plain < PLAIN_TURN_LIMIT, (plain, kb_done)
    assert max(kb_done) < KB_TURNS_LIMIT, kb_done


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_http", [5, 10])
async def test_plain_turn_does_not_wait_for_concurrent_http_tool_calls(tenant_with_key, fake_chat_llm, net, n_http):
    """http_tool 턴 N개가 느린 외부 API(1.5초)를 기다리는 동안 띄운 kb 없는 턴은 그 시간만큼 기다리지 않는다(#268).

    외부 요청은 ``asyncio.to_thread``(기본 실행기 풀)로 나가 thread-sensitive 스레드를 쥐지 않는다. 그 스레드로 보내면
    kb 없는 턴이 1.5초 넘게, http 턴끼리도 N × 1.5초 줄을 선다 — 이 시험이 그 되돌림을 잡는다."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id, domains=(API,))
    http_flow = await _flow(tenant, _http_graph({"url": f"https://{API}/slow"}))
    plain_flow = await _flow(tenant, _plain_graph())
    await services.run_test_turn(tenant.id, plain_flow.id, "워밍업")
    alone = await _timed_turn(tenant.id, plain_flow.id, "안녕하세요", time.monotonic())

    t0 = time.monotonic()
    http_turns = [asyncio.create_task(_run_turn(tenant.id, http_flow.id, f"주문 {i}", t0)) for i in range(n_http)]
    while not net.hits.items:  # 첫 http 턴이 외부 요청에 들어간 뒤에 kb 없는 턴을 띄운다
        await asyncio.sleep(0.005)
    plain = await _timed_turn(tenant.id, plain_flow.id, "안녕하세요", time.monotonic())
    done = await asyncio.gather(*http_turns)
    ends = [at for _, at in done]

    print(f"\n[#268] N={n_http} alone={alone:.3f}s plain={plain:.3f}s http_max={max(ends):.3f}s http_min={min(ends):.3f}s")
    assert len(net.hits.items) == n_http
    for result, _ in done:  # 운영 동작 그대로: 2xx 응답 → next 갈래, 판단 기록에 상태 코드
        assert [s["node_id"] for s in result.trace] == ["s", "h", "ok"]
        assert next(s for s in result.trace if s["kind"] == "http_tool")["record"]["status"] == 200
    assert plain < PLAIN_TURN_LIMIT, (plain, ends)
    assert max(ends) < HTTP_TURNS_LIMIT, ends


# ── 운영 동작 고정: 사용량·무중단 ─────────────────────────────────────────────


def _embedding_rows(tenant_id) -> list[tuple[str, int, int]]:
    from apps.usage.models import TokenUsage

    return [(r.call_type, r.total_tokens, r.request_count) for r in TokenUsage.objects.filter(tenant_id=tenant_id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("node_name", ["local_search_node", "source_search_node"])
async def test_kb_node_records_embedding_usage_once(tenant_with_key, slow_embeddings, node_name):
    """임베딩 HTTP를 스레드 밖으로 옮겨도 사용량은 그대로다 — TokenUsage 1행, 노드 수집기(판단 기록 토큰)에 1회."""
    from asgiref.sync import sync_to_async as adb

    from apps.agent import nodes
    from apps.usage.context import collect_calls, set_usage_context

    tenant, _ = tenant_with_key
    set_usage_context(tenant.id, "chat")
    with collect_calls() as calls:
        out = await getattr(nodes, node_name)({"tenant_id": str(tenant.id), "user_message": "환불 규정"})

    assert len(slow_embeddings.calls) == 1
    assert [(c.input_tokens, c.output_tokens) for c in calls] == [(3, 0)]
    assert await adb(_embedding_rows)(tenant.id) == [("embedding", 3, 1)]
    assert out["rag_chunks"] == []  # 지식이 없는 테넌트 — 어휘·의미 모두 빈 결과로 끝난다


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("node_name", ["local_search_node", "source_search_node"])
async def test_kb_node_keeps_going_when_embedding_fails(tenant_with_key, settings, node_name):
    """임베딩 HTTP가 실패해도 검색은 멈추지 않는다(전과 같이 의미 보강만 빠진다). 사용량도 남지 않는다."""
    import socket

    from asgiref.sync import sync_to_async as adb

    from apps.agent import nodes
    from apps.usage.context import set_usage_context

    with socket.socket() as s:  # 닫힌 포트 — 연결 거부
        s.bind(("127.0.0.1", 0))
        settings.OLLAMA_BASE_URL = f"http://127.0.0.1:{s.getsockname()[1]}"
    tenant, _ = tenant_with_key
    set_usage_context(tenant.id, "chat")

    out = await getattr(nodes, node_name)({"tenant_id": str(tenant.id), "user_message": "환불 규정"})

    assert out["rag_chunks"] == []
    assert await adb(_embedding_rows)(tenant.id) == []
