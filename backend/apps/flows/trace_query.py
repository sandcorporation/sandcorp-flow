"""판단 기록 읽기(#24) — 편집기 패널(#30)·세션 화면·MCP가 같은 모양을 쓴다. 모두 테넌트 범위."""

from __future__ import annotations

import uuid

from apps.flows.models import DecisionTrace


def _turn_row(turn: int, row: DecisionTrace, *, entries: bool = True) -> dict[str, object]:
    version = row.flow_version
    out: dict[str, object] = {
        "turn": turn,
        "created_at": row.created_at.isoformat(),
        "is_test": row.is_test,
        "flow_version": version.number if version else None,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": None if row.cost_usd is None else format(row.cost_usd.normalize(), "f"),
        "duration_ms": row.duration_ms,
    }
    if entries:
        out["entries"] = row.entries
    return out


def session_turns(
    tenant_id: uuid.UUID, session_id: str, *, entries: bool = True, limit: int | None = None
) -> list[dict[str, object]] | None:
    """세션의 턴별 판단 기록(오래된 턴부터, ``turn``은 1부터). 이 테넌트의 세션이 아니면 None.

    ``limit``이면 **가장 최근** limit개(턴 번호는 전체 기준 그대로). ``entries=False``면 노드별 상세 없이 합계만.
    """
    from apps.chat.models import ChatSession

    if not ChatSession.objects.filter(id=session_id, tenant_id=tenant_id).exists():
        return None
    rows = list(DecisionTrace.objects.filter(tenant_id=tenant_id, session_id=session_id).select_related("flow_version"))
    numbered = [_turn_row(i, r, entries=entries) for i, r in enumerate(rows, start=1)]
    return numbered[-limit:] if limit else numbered
