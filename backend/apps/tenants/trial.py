"""체험 한도(Trial Quota) — deep module (#41, ADR-0026 「플랫폼 키는 OpenAI 하나」).

자기 LLM Provider가 없는 테넌트는 체험 턴이 남아 있는 동안 플랫폼 OpenAI 키로 돈다.

- `trial_state(config)`      : 관리 화면에 보일 상태(byok / trial / exhausted / unavailable / platform_default).
- `admit_turn(config)`       : 방문자 한 턴을 들일지 정한다. 체험 테넌트면 조건부 UPDATE로 1 차감(원자적).
- `platform_llm(...)`        : 플랫폼 키 LLM Provider. `platform_embedding(dim)`: 플랫폼 키 임베딩.
- `uses_platform_key(config)`: 이 테넌트의 LLM 호출이 플랫폼 키 경로인가.

dev(`PLATFORM_DEFAULT_PROVIDERS_ENABLED=True`)는 기존 fake-llm/ollama 폴백을 그대로 쓰고 턴을 세지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from django.conf import settings
from django.db.models import F

from apps.tenants.models import Tenant

if TYPE_CHECKING:
    from apps.agent.providers import EmbeddingProvider, LLMProvider
    from apps.tenants.models import TenantConfig

TrialState = Literal["byok", "trial", "exhausted", "unavailable", "platform_default"]

# 방문자에게 보이는 문구 — 한도·키 같은 내부 사정을 말하지 않는다.
VISITOR_PAUSED_MESSAGE = "지금은 상담을 준비하고 있어요. 잠시 후 다시 찾아 주세요."


def _platform_default_enabled() -> bool:
    return bool(getattr(settings, "PLATFORM_DEFAULT_PROVIDERS_ENABLED", False))


def platform_key_ready() -> bool:
    return bool(settings.PLATFORM_OPENAI_API_KEY)


def uses_platform_key(config: TenantConfig) -> bool:
    """자기 LLM Provider가 없고 dev 폴백도 꺼져 있으면 플랫폼 키 경로다(키가 실제로 있는지는 따로)."""
    return not config.llm_provider_type and not _platform_default_enabled()


def remaining(config: TenantConfig) -> int:
    value = Tenant.objects.filter(pk=config.tenant_id).values_list("trial_turns_remaining", flat=True).first()
    return value or 0


def trial_state(config: TenantConfig) -> TrialState:
    if config.llm_provider_type:
        return "byok"
    if _platform_default_enabled():
        return "platform_default"
    if not platform_key_ready():
        return "unavailable"
    return "trial" if remaining(config) > 0 else "exhausted"


def admit_turn(config: TenantConfig) -> bool:
    """방문자 한 턴을 들이면 True. 체험 테넌트는 남은 턴이 있을 때만 1 차감한다.

    `UPDATE … SET n = n - 1 WHERE n > 0`은 DB가 행 잠금으로 직렬화하므로, 동시 요청이 몰려도
    음수가 되거나 남은 수보다 많이 들어가지 않는다.
    """
    if not uses_platform_key(config):
        return True
    if not platform_key_ready():
        return False
    updated = Tenant.objects.filter(pk=config.tenant_id, trial_turns_remaining__gt=0).update(
        trial_turns_remaining=F("trial_turns_remaining") - 1
    )
    return updated == 1


def platform_llm(model: str | None = None) -> LLMProvider:
    from apps.agent.providers import PROVIDER_OPENAI, LLMProvider

    return LLMProvider(
        type=PROVIDER_OPENAI,
        model=model or settings.PLATFORM_LLM_MODEL,
        base_url=settings.PLATFORM_OPENAI_BASE_URL,
        api_key=settings.PLATFORM_OPENAI_API_KEY,
    )


def platform_embedding(dim: int) -> EmbeddingProvider:
    """플랫폼 키 임베딩. OpenAI `dimensions` 인자로 테넌트 embed_dim에 맞춰, 인덱스 차원과
    자기 Provider로 바꿀 때의 재구축 규칙(설정 튜플이 바뀌면 재임베딩)을 그대로 쓴다."""
    from apps.agent.providers import PROVIDER_OPENAI, EmbeddingProvider

    return EmbeddingProvider(
        type=PROVIDER_OPENAI,
        base_url=settings.PLATFORM_OPENAI_BASE_URL,
        model=settings.PLATFORM_EMBED_MODEL,
        dim=dim,
        api_key=settings.PLATFORM_OPENAI_API_KEY,
        send_dimensions=True,
    )
