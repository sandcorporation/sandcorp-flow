"""기본 흐름(#23): 시작 → 지식 검색 → AI 답변 → [AI가 넘김을 판단하면] 사람 넘김.

옛 운영 챗 build_graph(#72에서 제거, local_search → call_llm → [원문 폴백] → create_escalation | save_messages)와 같은 동작이다.
config는 #22 스키마에 정의된 필드만 쓴다(모르는 필드는 거부된다).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from apps.flows.schema import HANDOFF_HANDLE, JSON

if TYPE_CHECKING:
    from apps.flows.models import Flow

DEFAULT_FLOW_NAME = "기본 흐름"

DEFAULT_FLOW: JSON = {
    "nodes": [
        {"id": "start", "kind": "start", "code": "N-01", "name": "시작",
         "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "search", "kind": "knowledge_search", "code": "N-02", "name": "지식 검색",
         "position": {"x": 240, "y": 0}, "config": {"top_k": 5}},
        {"id": "answer", "kind": "ai_answer", "code": "N-03", "name": "AI 답변",
         "position": {"x": 480, "y": 0}, "config": {}},
        {"id": "handoff", "kind": "handoff", "code": "N-04", "name": "사람 넘김",
         "position": {"x": 720, "y": 0}, "config": {}},
    ],
    "edges": [
        {"from": "start", "to": "search"},
        {"from": "search", "to": "answer"},
        {"from": "answer", "to": "handoff", "handle": HANDOFF_HANDLE},
    ],
}


def ensure_default_flow(tenant_id: uuid.UUID) -> Flow:
    """흐름이 하나도 없는 테넌트에 기본 흐름을 만들어 배포 중·공개로 둔다. 이미 흐름이 있으면 그것을 돌려준다(멱등).

    테넌트 행을 잠가 동시에 두 번 불려도 하나만 만든다. 새 테넌트 생성 경로(온보딩·Operator)와 같은 트랜잭션에서 부른다.
    """
    from django.db import transaction

    from apps.flows.models import Flow, FlowDraft, FlowVersion
    from apps.tenants.models import Tenant

    with transaction.atomic():
        Tenant.objects.select_for_update().only("id").get(pk=tenant_id)
        existing = Flow.objects.for_tenant(tenant_id).order_by("created_at").first()
        if existing is not None:
            return existing
        flow = Flow.objects.create(tenant_id=tenant_id, name=DEFAULT_FLOW_NAME, is_public=True)
        FlowDraft.objects.create(flow=flow, graph=DEFAULT_FLOW)
        flow.deployed_version = FlowVersion.objects.create(flow=flow, graph=DEFAULT_FLOW)
        flow.save(update_fields=["deployed_version", "updated_at"])
        return flow
