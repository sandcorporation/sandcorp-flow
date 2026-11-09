"""동시 턴이 thread-sensitive 스레드 하나를 나눠 쓸 때의 대기(#262).

운영 위젯 턴은 taskiq 워커(``--max-async-tasks 25``)에서 돈다. 워커에는 요청 경계(``ThreadSensitiveContext``)가 없어
프로세스의 모든 턴이 asgiref의 thread-sensitive 스레드 **하나**를 나눠 쓴다. 이 시험도 같은 조건이다 — 한 이벤트 루프에서
문맥 없이 턴 여럿을 띄운다(시험 대화 ``run_test_turn``은 운영 턴과 같은 실행기·노드를 쓴다).

고치기 전에는 kb 턴의 임베딩 HTTP가 그 스레드를 쥐어 kb 없는 턴의 DB 단계가 임베딩 시간만큼 기다렸고, kb 턴끼리도
임베딩이 줄을 섰다. 루프 기본 풀이 작으면(#322) 동시 턴이 풀 자리를 두고 줄을 선다.

http_tool(#268)의 외부 요청은 처음부터 ``asyncio.to_thread``로 나가 그 스레드를 쥐지 않는다 — 같은 꼴로 고정한다.

계약은 벽시계가 아니라 **사건**으로 확인한다(#345): 느린 수신부(임베딩·외부 API)를 ``threading.Event`` 관문으로 붙잡아
둔다. ① N개 턴이 모두 수신부에 들어온다(줄을 서지 않고 겹쳐 돈다) ② 관문이 닫힌 채로 kb 없는 턴이 끝난다
③ 그때까지 붙잡힌 턴은 하나도 끝나지 않았다 ④ 관문을 풀면 모두 끝난다. 계약이 깨지면 ①이나 ②가 영영 오지 않으므로
아래 한도는 실패를 알리는 안전망일 뿐 통과의 경계가 아니다(통과 쪽은 0.1초 안팎).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import pytest

from asgiref.sync import sync_to_async as adb

from tests.test_flow_http_tool import API, _setup, net, tool_pki  # noqa: F401 — 픽스처
from tests.test_flow_http_tool import _flow as _http_graph
from tests.test_flow_loop_block import _flow, _kb_graph, _plain_graph, slow_embeddings  # noqa: F401

NEVER = 3.0
"""계약이 깨졌을 때 「사건이 오지 않음」을 판정하는 안전 한도(초). http_tool 총 한도(5초)보다 작다."""


async def _until(pred: Callable[[], bool], what: Callable[[], str]) -> None:
    """``pred``가 참이 될 때(수신부에 요청이 들어올 때)까지 기다린다. ``NEVER``초 안에 안 오면 실패."""
    deadline = time.monotonic() + NEVER
    while not pred():
        assert time.monotonic() < deadline, what()
        await asyncio.sleep(0.005)  # 다른 스레드(수신부)의 사건을 루프에서 기다리는 짧은 양보


async def _turn(tenant_id, flow_id, message: str):
    from apps.flows import services

    result = await services.run_test_turn(tenant_id, flow_id, message)
    assert result.ran, result
    return result


async def _plain_finishes_while_held(tenant_id, flow_id, held: list[asyncio.Task]) -> None:
    """관문이 닫힌 채로 kb 없는 턴이 끝나고, 그동안 붙잡힌 턴은 하나도 끝나지 않았다."""
    try:
        await asyncio.wait_for(_turn(tenant_id, flow_id, "안녕하세요"), NEVER)
    except TimeoutError:
        pytest.fail("kb 없는 턴이 붙잡힌 턴들을 기다렸다 — 관문을 풀기 전에 끝나지 않음")
    assert not any(t.done() for t in held), [t for t in held if t.done()]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_kb", [5, 10])
async def test_plain_turn_does_not_wait_for_concurrent_kb_embeddings(tenant_with_key, fake_chat_llm, slow_embeddings,
                                                                    n_kb):
    """kb 턴 N개가 임베딩 HTTP를 기다리는 동안 띄운 kb 없는 턴은 그것을 기다리지 않는다. kb 턴끼리도 겹쳐 돈다."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    kb_flow = await _flow(tenant, _kb_graph())
    plain_flow = await _flow(tenant, _plain_graph())
    await services.run_test_turn(tenant.id, plain_flow.id, "워밍업")  # 모듈 적재(프로세스당 한 번)

    gate = slow_embeddings.gate = threading.Event()
    kb_turns = [asyncio.create_task(_turn(tenant.id, kb_flow.id, f"환불 규정 {i}")) for i in range(n_kb)]
    try:
        await _until(lambda: len(slow_embeddings.calls) >= n_kb,
                     lambda: f"kb 턴 {n_kb}개의 임베딩이 겹쳐 돌지 않았다 — 들어온 것 {len(slow_embeddings.calls)}개")
        await _plain_finishes_while_held(tenant.id, plain_flow.id, kb_turns)
    finally:
        gate.set()
    await asyncio.gather(*kb_turns)
    assert len(slow_embeddings.calls) == n_kb


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_http", [5, 10])
async def test_plain_turn_does_not_wait_for_concurrent_http_tool_calls(tenant_with_key, fake_chat_llm, net, n_http):
    """http_tool 턴 N개가 느린 외부 API를 기다리는 동안 띄운 kb 없는 턴은 그것을 기다리지 않는다(#268).

    외부 요청은 ``asyncio.to_thread``(기본 실행기 풀)로 나가 thread-sensitive 스레드를 쥐지 않는다. 그 스레드로 보내면
    kb 없는 턴이 외부 응답을, http 턴끼리도 서로를 기다린다 — 이 시험이 그 되돌림을 잡는다."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id, domains=(API,))
    http_flow = await _flow(tenant, _http_graph({"url": f"https://{API}/slow"}))
    plain_flow = await _flow(tenant, _plain_graph())
    await services.run_test_turn(tenant.id, plain_flow.id, "워밍업")

    gate = net.hits.gate = threading.Event()
    http_turns = [asyncio.create_task(_turn(tenant.id, http_flow.id, f"주문 {i}")) for i in range(n_http)]
    try:
        await _until(lambda: len(net.hits.items) >= n_http,
                     lambda: f"http 턴 {n_http}개의 외부 요청이 겹쳐 돌지 않았다 — 들어온 것 {len(net.hits.items)}개")
        await _plain_finishes_while_held(tenant.id, plain_flow.id, http_turns)
    finally:
        gate.set()
    done = await asyncio.gather(*http_turns)

    assert len(net.hits.items) == n_http
    for result in done:  # 운영 동작 그대로: 2xx 응답 → next 갈래, 판단 기록에 상태 코드
        assert [s["node_id"] for s in result.trace] == ["s", "h", "ok"]
        assert next(s for s in result.trace if s["kind"] == "http_tool")["record"]["status"] == 200


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
