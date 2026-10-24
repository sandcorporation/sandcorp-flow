"""실행기: 흐름 JSON → StateGraph, 핸들러 레지스트리, 그래프 캐시, 한 턴 노드 상한(#23, ADR-0027).

LLM은 결정적 Fake(conftest autouse). DB·체크포인터는 실제 객체.
"""
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _node(node_id, kind, n, config=None):
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _chain(kinds):
    """start → kinds[0] → kinds[1] … 한 줄로 이은 흐름."""
    nodes = [_node("start", "start", 1)]
    edges = []
    prev = "start"
    for i, (kind, config) in enumerate(kinds, start=2):
        nid = f"n{i}"
        nodes.append(_node(nid, kind, i, config))
        edges.append({"from": prev, "to": nid})
        prev = nid
    return {"nodes": nodes, "edges": edges}


@pytest.fixture
def passthrough_guardrail(monkeypatch):
    """아직 핸들러가 없는 가드레일 종류에 「그냥 다음으로」 핸들러를 잠시 등록한다(실행기 자체를 시험)."""
    from apps.flows import executor

    async def passthrough(state, ctx):
        return executor.NodeOutcome(update={})

    monkeypatch.setitem(executor.HANDLERS, executor.NodeKind.GUARDRAIL, passthrough)
    return passthrough


def test_flow_with_unhandled_kind_is_rejected_at_build():
    """핸들러가 없는 종류(아직 HTTP 도구)가 든 흐름은 지을 때 거부한다 — 실행 중에 조용히 멈추지 않게."""
    from apps.flows.executor import UnsupportedNodeKind, flow_builder

    flow = _chain([("http_tool", {"method": "GET", "url": "https://example.com/x", "headers": {}})])
    with pytest.raises(UnsupportedNodeKind):
        flow_builder(flow)


def test_builder_is_cached_by_version_hash(passthrough_guardrail):
    """같은 JSON(키 순서만 다른 것 포함)은 한 번만 짓는다."""
    from apps.flows.executor import flow_builder

    a = _chain([("guardrail", {"rules": ["pii"]})])
    b = {"edges": a["edges"], "nodes": a["nodes"]}
    assert flow_builder(a) is flow_builder(b)


@pytest.mark.django_db(transaction=True)
async def test_turn_stops_at_max_steps(tenant_with_key, passthrough_guardrail):
    """한 턴에 노드를 MAX_STEPS_PER_TURN개 넘게 돌지 않는다 — 넘으면 거기서 턴을 끝낸다."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async
    from apps.flows.schema import MAX_STEPS_PER_TURN

    tenant, _ = tenant_with_key
    flow = _chain([("guardrail", {"rules": ["pii"]})] * (MAX_STEPS_PER_TURN + 10))
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-steps")

    result = await run_flow_turn_async(session, "안녕하세요", flow)

    assert len(result["trace"]) == MAX_STEPS_PER_TURN
    assert result["step_limit_exceeded"] is True


@pytest.mark.django_db(transaction=True)
async def test_trace_records_visited_nodes_and_handles(tenant_with_key, passthrough_guardrail):
    """턴마다 지난 노드와 고른 갈래를 순서대로 남긴다(판단 기록 자리 — #24가 채움)."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    tenant, _ = tenant_with_key
    flow = _chain([("guardrail", {"rules": ["pii"]})] * 2)
    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-trace")

    result = await run_flow_turn_async(session, "안녕하세요", flow)

    assert [(t["node_id"], t["kind"], t["handle"]) for t in result["trace"]] == [
        ("start", "start", "next"), ("n2", "guardrail", "next"), ("n3", "guardrail", "next"),
    ]
    assert result["step_limit_exceeded"] is False
