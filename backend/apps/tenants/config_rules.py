"""테넌트 설정 저장 규칙 — HTTP ``PATCH /api/tenant/config/``와 MCP ``update_settings``가 같이 쓴다(#212)."""

from __future__ import annotations

from apps.tenants.models import TenantConfig


def topic_scope_error(config: TenantConfig, enabled: bool | None, description: str | None) -> str | None:
    """주제범위 제어를 켜려면 범위 설명이 있어야 한다(빈 채로 켜면 판정 기준이 없어 fail-open으로 무력화됨, issue 199).

    ``enabled``·``description``은 이번에 바꾸려는 값(None = 안 바꿈 → 저장된 값을 본다). 막아야 하면 사용자에게 보일 문구.
    """
    eff_enabled = enabled if enabled is not None else config.topic_scope_enabled
    eff_description = description if description is not None else config.scope_description
    if eff_enabled and not (eff_description or "").strip():
        return "주제범위 제어를 켜려면 응대 범위 설명을 입력해야 합니다."
    return None
