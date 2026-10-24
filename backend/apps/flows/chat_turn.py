"""운영 챗 한 턴의 진입: 세션이 고정된 Flow Version을 골라 실행기로 돈다(#72, ADR-0027).

- 세션에 고정된 버전이 있으면 그 흐름. 배포가 진행 중 대화를 바꾸지 않는다.
- 고정 버전이 없는 세션(이 칸이 생기기 전 세션)은 첫 턴에 테넌트 공개 흐름의 **배포 중 버전에 고정**하고 이어 간다.
- 공개 흐름이 없는 테넌트는 고정하지 않고 ``DEFAULT_FLOW``로 돈다.

체크포인트(thread_id=session_id)는 옛 build_graph와 같은 자리다. 옛 체크포인트에는 실행기 키(``resume_node`` 등)가 없어
시작 노드부터 돌고, 대화 기록(``messages``)은 그대로 이어진다. 체험 턴 차감은 이 함수를 부르는 chat_task가 먼저 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.flows.schema import JSON

if TYPE_CHECKING:
    from apps.chat.models import ChatSession

__all__ = ["run_chat_turn_async", "session_flow_graph"]


def session_flow_graph(session: ChatSession) -> JSON:
    """이 세션이 돌 흐름 JSON(sync). 고정 버전이 없으면 공개 흐름 배포 중 버전에 고정한다(조건부 UPDATE라 동시 턴에도 하나)."""
    from apps.chat.models import ChatSession
    from apps.flows.defaults import DEFAULT_FLOW
    from apps.flows.models import Flow, FlowVersion

    if session.flow_version_id is None and not session.is_test:
        version = Flow.public_version_for(session.tenant_id)
        if version is None:
            return DEFAULT_FLOW
        ChatSession.objects.filter(pk=session.pk, flow_version__isnull=True).update(flow_version=version)
        session.refresh_from_db(fields=["flow_version"])
    if session.flow_version_id is None:
        return DEFAULT_FLOW
    return FlowVersion.objects.only("graph").get(pk=session.flow_version_id).graph


async def run_chat_turn_async(session: ChatSession, user_message: str) -> str:
    """방문자 한 턴을 세션의 흐름으로 돌리고 AI 답을 돌려준다. SSE·저장·Escalation은 노드 핸들러가 한다."""
    from asgiref.sync import sync_to_async

    from apps.flows.executor import run_flow_turn_async

    graph = await sync_to_async(session_flow_graph)(session)
    state = await run_flow_turn_async(session, user_message, graph)
    return state.get("assistant_response", "")
