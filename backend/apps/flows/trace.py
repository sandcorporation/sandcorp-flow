"""판단 기록(Decision Trace) 만들기·저장(#24, ADR-0027).

실행기가 노드마다 쌓은 ``TraceEntry``(핸들러가 준 ``record`` + 실행기가 잰 시간·토큰·비용)를 한 턴에 한 번의 insert로 저장한다.

- **비밀 가림**: ``REDACTORS``(``(tenant_id, 문자열) → 문자열``)를 저장 직전 한 곳(``prepare``)에서 모든 문자열에 적용한다.
  기본은 Tenant Secret 가림(``apps.tenants.tenant_secrets.redact``, #33) — **그 기록의 테넌트가** DB에 저장한 비밀 전체(#118,
  짧은 캐시)·파생값과 그 부분 문자열. 다른 테넌트 비밀로는 가리지 않는다(가림 오라클 방지, #115). DB를 부를 수 있어
  ``prepare``는 sync다(async 안에서는 ``sync_to_async``로).
- **자르기**: 문자열 하나는 ``MAX_FIELD_CHARS``자까지(넘으면 ``…(잘림)``). 노드 수는 실행기 상한(50)이 막는다 → 한 행은
  많아야 50 × (몇 개 필드 × 2000자) 수준(수백 KB 미만)이고 JSONB 한 칸이다. 방문자 입력·답변이 이보다 긴 일은 드물고
  전문은 ChatMessage에 이미 있어 기록에서 잘라도 정보가 사라지지 않는다.
- 저장 실패는 턴을 깨지 않는다(로그만) — 기록은 부가 기능이다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.flows.contract import TraceEntry
from apps.flows.schema import JSON
from apps.tenants.tenant_secrets import redact as redact_tenant_secrets
from apps.tenants.tenant_secrets import redactor

if TYPE_CHECKING:
    from apps.chat.models import ChatSession

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 2000
TRUNCATED_MARK = "…(잘림)"

REDACTORS: list[Callable[[uuid.UUID, str], str]] = [redact_tenant_secrets]
"""비밀 가림 훅 ``(기록의 tenant_id, 문자열) → 문자열``. 저장 직전 모든 문자열에 차례로 적용한다."""


def _clean(tenant_id: uuid.UUID, value: JSON) -> JSON:
    def redact(text: str) -> str:
        for r in REDACTORS:
            text = r(tenant_id, text)
        return text

    return scrub(value, redact)


def scrub(value: JSON, redact: Callable[[str], str]) -> JSON:
    """모든 문자열에 ``redact``를 걸고 ``MAX_FIELD_CHARS``로 자른 값(판단 기록 저장과 같은 가림·자름)."""
    if isinstance(value, str):
        value = redact(value)
        return value[:MAX_FIELD_CHARS] + TRUNCATED_MARK if len(value) > MAX_FIELD_CHARS else value
    if isinstance(value, dict):
        return {k: scrub(v, redact) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, redact) for v in value]
    return value


def bound_redactor(tenant_id: uuid.UUID) -> Callable[[str], str]:
    """``REDACTORS``를 이 테넌트에 묶어 한 함수로(sync — 테넌트 비밀 가림 집합을 지금 한 번 읽는다).

    돌려준 함수는 DB를 부르지 않는다 — 시험 대화 SSE가 이벤트마다 sync 스레드로 건너가지 않고 루프에서 가린다(#256).
    """
    bound = [redactor(tenant_id) if r is redact_tenant_secrets else (lambda text, r=r: r(tenant_id, text))
             for r in REDACTORS]

    def redact(text: str) -> str:
        for r in bound:
            text = r(text)
        return text

    return redact


def prepare(trace: list[TraceEntry], *, tenant_id: uuid.UUID) -> tuple[list[JSON], int, int, Decimal | None]:
    """저장할 entries(이 테넌트 비밀 가림·자름 적용)와 합계(입력 토큰, 출력 토큰, 비용 — 모르는 모델이 있으면 None)."""
    entries: list[JSON] = [_clean(tenant_id, dict(e)) for e in trace]  # type: ignore[arg-type]  # TypedDict → JSON 객체
    input_tokens = sum(e["input_tokens"] for e in trace)
    output_tokens = sum(e["output_tokens"] for e in trace)
    cost, _ = summarize(trace)
    return entries, input_tokens, output_tokens, cost


def summarize(entries: Sequence[Mapping[str, object]]) -> tuple[Decimal | None, list[str]]:
    """판단 기록 항목들의 (아는 부분의 비용 합, 단가 모름 모델 이름들).

    모르는 모델의 호출은 합계에 더하지 않고 이름만 남긴다(「일부 미포함」). 단가를 아는 호출이 하나도 없는데
    모르는 호출이 있으면 비용은 None(전부 모름). 호출이 없으면 0. 항목에 ``calls``가 있으면 호출별로, 없으면(예전 기록)
    항목의 ``cost_usd``로 센다 — 예전 기록은 하나라도 모르면 턴 비용이 null이었는데, 여기서는 호출별 값으로 다시 합친다.
    """
    total = Decimal(0)
    priced = False
    unpriced: set[str] = set()
    anonymous_unpriced = False
    for e in entries:
        calls = e.get("calls")
        if isinstance(calls, list) and calls:
            for c in calls:
                cost = c.get("cost_usd")
                if cost is not None:
                    total += Decimal(str(cost))
                    priced = True
                elif c.get("input_tokens") or c.get("output_tokens"):
                    unpriced.add(str(c.get("model") or ""))
            continue
        cost = e.get("cost_usd")
        if cost is not None:
            total += Decimal(str(cost))
            priced = priced or bool(e.get("input_tokens") or e.get("output_tokens"))
        elif e.get("input_tokens") or e.get("output_tokens"):
            anonymous_unpriced = True
    names = sorted(n for n in unpriced if n)
    if (unpriced or anonymous_unpriced) and not priced:
        return None, names
    return total, names


def save_decision_trace(session: ChatSession, trace: list[TraceEntry], duration_ms: int) -> None:
    """한 턴의 판단 기록을 한 번의 insert로 저장한다(sync). 지난 노드가 없으면 저장하지 않는다."""
    from apps.flows.models import DecisionTrace

    if not trace:
        return
    try:
        entries, input_tokens, output_tokens, cost = prepare(trace, tenant_id=session.tenant_id)
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
