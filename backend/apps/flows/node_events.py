"""실행기 노드 이벤트(#239, ADR-0035) — 시험 대화가 턴 도중에 「지금 어느 노드를 도는지」를 흘려보낸다.

- 실행기(``executor._make_step``)는 노드마다 handler 직전 ``node_start``, trace 항목을 만든 직후 ``node_end``,
  handler 예외 때 ``node_error``를 ``emit``한다. 받는 쪽(sink)은 ``ContextVar``에 넣은 콜백이다.
  **sink가 없으면(운영 턴) 아무 일도 하지 않는다** — 운영 턴의 동작·비용은 그대로다.
- ``ContextVar``라서 sink는 그것을 넣은 작업(시험 턴)과 그 작업이 띄운 하위 작업(LangGraph 노드 실행)에만 보인다.
  같은 프로세스에서 동시에 도는 운영 턴에는 섞이지 않는다.
- 콜백은 동기·즉시 반환이어야 한다(예: ``asyncio.Queue.put_nowait``). 실행기는 송신을 기다리지 않는다.
- 이벤트 데이터는 **가리기 전 값**이다. 밖으로 내보내는 쪽이 ``clean``(판단 기록과 같은 비밀 가림·자름, sync — DB를
  읽을 수 있어 async 안에서는 ``sync_to_async``)을 거친다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from apps.flows.schema import ELSE_HANDLE, HANDOFF_HANDLE, JSON, NodeKind

NodeEventName = Literal["node_start", "node_end", "node_error"]
NodeEventSink = Callable[[NodeEventName, dict[str, JSON]], None]

ERROR_MESSAGE_MAX = 300
"""``node_error.message`` 길이 상한(가린 뒤)."""

_sink: ContextVar[NodeEventSink | None] = ContextVar("flow_node_event_sink", default=None)


@contextmanager
def sink(callback: NodeEventSink | None) -> Iterator[None]:
    """이 맥락(과 여기서 띄우는 하위 작업)에서 실행기 노드 이벤트를 ``callback``으로 받는다."""
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(name: NodeEventName, data: dict[str, JSON]) -> None:
    callback = _sink.get()
    if callback is not None:
        callback(name, data)


def active() -> bool:
    return _sink.get() is not None


def summary(kind: str, handle: str | None, record: Mapping[str, object]) -> str | None:
    """노드 종류별 한 줄 요약 — 화면이 ``record`` 모양을 몰라도 배지에 쓸 수 있게 서버가 만든다(우산 #238 표)."""
    out = record.get("output")
    out_map: Mapping[str, object] = out if isinstance(out, Mapping) else {}
    match kind:
        case NodeKind.AI_CLASSIFY.value:
            confidence = out_map.get("confidence")
            conf = f"확신도 {float(confidence):.2f}" if isinstance(confidence, int | float) else "확신도 -"
            if "label" in out_map:
                label = out_map.get("label")
                return f"라벨 '{label}' · {conf}" if label else f"라벨 없음 · {conf}"
            return conf
        case NodeKind.CONDITION.value:
            branch = record.get("branch", handle)
            return "기본 갈래" if branch == ELSE_HANDLE else f"갈래 '{branch}'"
        case NodeKind.KNOWLEDGE_SEARCH.value:
            chunks = out_map.get("chunks")
            return f"조각 {chunks if isinstance(chunks, int) else 0}개"
        case NodeKind.GUARDRAIL.value:
            if not record.get("hits"):
                return "통과"
            return {"mask": "가림", "rewrite": "다시 씀"}.get(str(record.get("action")), "차단")
        case NodeKind.HTTP_TOOL.value:
            if record.get("error"):
                return f"실패({record['error']})"
            return f"HTTP {record.get('status')}"
        case NodeKind.HANDOFF.value:
            return "넘김" if record.get("dry_run") or str(record.get("reason", "")).startswith("넘김") else "넘기지 않음"
        case NodeKind.AI_ANSWER.value:
            if handle == HANDOFF_HANDLE:
                return "넘김 판단"
            return f"답 {len(str(out or ''))}자"
    return None


def error_message(exc: BaseException) -> str:
    """사람이 읽는 짧은 오류 문구(가리기 전). 예외 문구에 방문자 입력·비밀이 섞일 수 있어 내보내기 전에 ``clean``을 거친다."""
    detail = str(exc).strip()
    return f"노드 실행 중 오류가 났습니다 ({type(exc).__name__}{': ' + detail if detail else ''})"


def clean(tenant_id: uuid.UUID, name: NodeEventName, data: dict[str, JSON]) -> dict[str, JSON]:
    """판단 기록 저장과 같은 가림·자름(``trace._clean``)을 거친 이벤트 데이터(sync)."""
    from apps.flows.trace import TRUNCATED_MARK, _clean

    cleaned = _clean(tenant_id, data)
    assert isinstance(cleaned, dict)
    message = cleaned.get("message")
    if name == "node_error" and isinstance(message, str) and len(message) > ERROR_MESSAGE_MAX:
        cleaned["message"] = message[:ERROR_MESSAGE_MAX] + TRUNCATED_MARK
    return cleaned
