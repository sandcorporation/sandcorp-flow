"""판단 기록(Decision Trace) 만들기·저장(#24, ADR-0027).

실행기가 노드마다 쌓은 ``TraceEntry``(핸들러가 준 ``record`` + 실행기가 잰 시간·토큰·비용)를 한 턴에 한 번의 insert로 저장한다.

- **비밀 가림**: ``REDACTORS``(문자열 → 문자열)를 저장 직전 한 곳(``prepare``)에서 모든 문자열에 적용한다. 기본은
  Tenant Secret 가림(``apps.tenants.tenant_secrets.redact``, #33) — 이 프로세스가 복호화한 비밀 원문과 그 부분 문자열.
- **자르기**: 문자열 하나는 ``MAX_FIELD_CHARS``자까지(넘으면 ``…(잘림)``). 노드 수는 실행기 상한(50)이 막는다 → 한 행은
  많아야 50 × (몇 개 필드 × 2000자) 수준(수백 KB 미만)이고 JSONB 한 칸이다. 방문자 입력·답변이 이보다 긴 일은 드물고
  전문은 ChatMessage에 이미 있어 기록에서 잘라도 정보가 사라지지 않는다.
- 저장 실패는 턴을 깨지 않는다(로그만) — 기록은 부가 기능이다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.flows.contract import TraceEntry
from apps.flows.schema import JSON
from apps.tenants.tenant_secrets import redact as redact_tenant_secrets

if TYPE_CHECKING:
    from apps.chat.models import ChatSession

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 2000
TRUNCATED_MARK = "…(잘림)"

REDACTORS: list[Callable[[str], str]] = [redact_tenant_secrets]
"""비밀 가림 훅. 저장 직전 모든 문자열에 차례로 적용한다."""


def _clean(value: JSON) -> JSON:
    if isinstance(value, str):
        for redact in REDACTORS:
            value = redact(value)
        return value[:MAX_FIELD_CHARS] + TRUNCATED_MARK if len(value) > MAX_FIELD_CHARS else value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def prepare(trace: list[TraceEntry]) -> tuple[list[JSON], int, int, Decimal | None]:
    """저장할 entries(가림·자름 적용)와 합계(입력 토큰, 출력 토큰, 비용 — 모르는 모델이 있으면 None)."""
    entries: list[JSON] = [_clean(dict(e)) for e in trace]  # type: ignore[arg-type]  # TypedDict → JSON 객체
    input_tokens = sum(e["input_tokens"] for e in trace)
    output_tokens = sum(e["output_tokens"] for e in trace)
    unknown = any(e["cost_usd"] is None and (e["input_tokens"] or e["output_tokens"]) for e in trace)
    cost = None if unknown else sum((Decimal(e["cost_usd"] or "0") for e in trace), Decimal(0))
    return entries, input_tokens, output_tokens, cost


def save_decision_trace(session: ChatSession, trace: list[TraceEntry], duration_ms: int) -> None:
    """한 턴의 판단 기록을 한 번의 insert로 저장한다(sync). 지난 노드가 없으면 저장하지 않는다."""
    from apps.flows.models import DecisionTrace

    if not trace:
        return
    try:
        entries, input_tokens, output_tokens, cost = prepare(trace)
        DecisionTrace.objects.create(
            tenant_id=session.tenant_id,
            session=session,
            flow_version_id=session.flow_version_id,
            is_test=session.is_test,
            entries=entries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001 — 기록 실패가 방문자 답을 막지 않게
        logger.exception("판단 기록 저장 실패 session=%s", session.id)
