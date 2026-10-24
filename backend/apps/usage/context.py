"""사용량 귀속 컨텍스트 (ContextVar).

진입점(chat 그래프·인제스션·OCR)이 tenant_id·call_type·session_id를 set하면, LLM/임베딩 경계의
계측이 호출부 시그니처 변경 없이 이를 읽어 record_usage에 귀속한다. apps.agent.providers의
_current_chat_provider와 같은 패턴.
"""
import contextlib
import contextvars
from dataclasses import dataclass


@dataclass
class UsageContext:
    tenant_id: str | None = None
    call_type: str = "chat"
    session_id: str | None = None


_current: contextvars.ContextVar[UsageContext | None] = contextvars.ContextVar(
    "usage_context", default=None
)


def set_usage_context(tenant_id, call_type: str, session_id=None) -> None:
    _current.set(UsageContext(
        tenant_id=str(tenant_id) if tenant_id else None,
        call_type=call_type,
        session_id=str(session_id) if session_id else None,
    ))


def get_usage_context() -> UsageContext | None:
    return _current.get()


@contextlib.contextmanager
def override_call_type(call_type: str):
    """현재 컨텍스트의 call_type만 일시 교체한다(예: 인제스션 중 OCR 호출을 'ocr'로 귀속)."""
    ctx = _current.get()
    if not ctx or not ctx.tenant_id:
        yield
        return
    token = _current.set(UsageContext(ctx.tenant_id, call_type, ctx.session_id))
    try:
        yield
    finally:
        _current.reset(token)


# ── 노드 단위 사용량 수집 ──────────────────────────────────────────────────────
# 실행기가 노드를 돌리는 동안 LLM 경계가 보고한 호출을 모은다(판단 기록의 노드별 토큰·비용). 롤업(record_usage)과 별개다.

@dataclass(frozen=True)
class LLMCall:
    model: str
    input_tokens: int
    output_tokens: int


_collector: contextvars.ContextVar[list[LLMCall] | None] = contextvars.ContextVar("usage_collector", default=None)


@contextlib.contextmanager
def collect_calls():
    """블록 안에서 보고된 LLM 호출을 모아 준다(리스트는 블록이 끝난 뒤에도 읽는다)."""
    calls: list[LLMCall] = []
    token = _collector.set(calls)
    try:
        yield calls
    finally:
        _collector.reset(token)


def note_call(call: LLMCall) -> None:
    calls = _collector.get()
    if calls is not None:
        calls.append(call)
