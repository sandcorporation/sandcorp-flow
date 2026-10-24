"""기본 흐름(#23): 시작 → 지식 검색 → AI 답변 → [AI가 넘김을 판단하면] 사람 넘김.

운영 챗의 build_graph(local_search → call_llm → [원문 폴백] → create_escalation | save_messages)와 같은 동작이다.
config는 #22 스키마에 정의된 필드만 쓴다(모르는 필드는 거부된다).
"""

from __future__ import annotations

from apps.flows.schema import HANDOFF_HANDLE, JSON

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
