"""노드 종류별 핸들러(#23). 기존 챗 노드 함수(apps/agent/nodes.py)를 불러 재사용한다 — 옮기지 않는다."""

from __future__ import annotations

from apps.flows.contract import FlowState, NodeContext, NodeOutcome, register
from apps.flows.schema import NodeKind


@register(NodeKind.START)
async def start(state: FlowState, ctx: NodeContext) -> NodeOutcome:
    return NodeOutcome(update={})
