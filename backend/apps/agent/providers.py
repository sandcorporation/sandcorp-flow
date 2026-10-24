"""ProviderResolver (deep module).

Tenant provider 설정(타입·base_url·키·model)을 LLM 클라이언트로 해석한다(ADR-0012).
타입 분기: openai/custom → OpenAI-호환 클라이언트, anthropic → Anthropic 네이티브.
LLM 경계가 전역 settings 대신 이 리졸버로 per-Tenant 클라이언트를 얻는다.
"""
import contextvars
from dataclasses import dataclass
from typing import Any

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_CUSTOM = "custom"


@dataclass
class LLMProvider:
    type: str          # openai | anthropic | custom | "" (플랫폼 기본)
    model: str
    base_url: str = ""
    api_key: str = ""


@dataclass
class EmbeddingProvider:
    type: str          # openai | custom | "" (플랫폼 기본; anthropic 없음)
    base_url: str      # OpenAI-호환 base (예: https://host/v1)
    model: str
    dim: int = 1024
    api_key: str = ""
    # True면 /embeddings에 `dimensions`를 실어 보낸다(플랫폼 OpenAI 키 — 차원을 embed_dim에 맞춤, #41).
    send_dimensions: bool = False


def build_llm_client(provider: LLMProvider):
    if provider.type == PROVIDER_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        # langchain_anthropic는 model을 alias로 받지만 타입 스텁은 model_name만 노출한다.
        return ChatAnthropic(model=provider.model, api_key=provider.api_key or None)  # pyright: ignore[reportCallIssue]

    # openai / custom / "" → OpenAI-호환
    from langchain_openai import ChatOpenAI

    # stream_usage: 스트리밍 요청에 stream_options.include_usage를 싣는다. OpenAI는 이게 없으면 스트리밍 응답에 usage를
    # 주지 않는다. langchain_openai는 base_url을 지정하면 이 기본을 끄므로(플랫폼 키·custom이 그렇다) 늘 명시한다(#86).
    kwargs: dict[str, Any] = {"model": provider.model, "stream_usage": True}
    if provider.api_key:
        kwargs["api_key"] = provider.api_key
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    from apps.core.safe_http import guarded_async_http_client, guarded_http_client, private_allowed

    if provider.type and not private_allowed():
        # 운영: 테넌트가 고른 Provider(custom·openai, 체험 플랫폼 키의 api.openai.com 포함)로 가는 연결마다
        # 해석·IP 검사(#108). type=""(dev 플랫폼 기본 OpenRouter·fake-llm)는 플랫폼 고정 주소라 그대로 둔다.
        # 저장 뒤 DNS가 사설로 바뀌어도(리바인딩) 연결하지 않는다. 시간 상한은 SDK가 요청마다 건다.
        kwargs["http_client"] = guarded_http_client(None)
        kwargs["http_async_client"] = guarded_async_http_client(None)
    # 플랫폼 기본(OpenRouter)은 구조화 출력 시 provider가 파라미터를 강제 준수하게 한다.
    if provider.type == "":
        kwargs["extra_body"] = {"provider": {"require_parameters": True}}
    return ChatOpenAI(**kwargs)


def _provider_from_config(config, model: str) -> LLMProvider:
    """TenantConfig + 모델명 → LLMProvider. type이 비면 dev는 플랫폼 기본(OpenRouter)으로
    폴백하고, prod(PLATFORM_DEFAULT_PROVIDERS_ENABLED=False)는 Tenant 설정을 강제한다."""
    from apps.tenants import trial

    if config.llm_provider_type and not trial.ignores_tenant_provider(config):  # 체험 테넌트는 Provider 무시(#134)
        from apps.tenants.crypto import decrypt_secret

        return LLMProvider(
            type=config.llm_provider_type,
            model=model,
            base_url=config.llm_base_url,
            api_key=decrypt_secret(config.llm_api_key),
        )
    from django.conf import settings

    if getattr(settings, "PLATFORM_DEFAULT_PROVIDERS_ENABLED", False):
        return LLMProvider(
            type="", model=model,
            base_url=settings.OPEN_ROUTER_BASE_URL, api_key=settings.OPEN_ROUTER_API_KEY,
        )
    raise ValueError("LLM Provider가 설정되지 않았습니다 (프로덕션은 Tenant 설정 필수)")


def _platform_trial_llm(config, *, require_turns: bool) -> LLMProvider | None:
    """체험 테넌트(자기 Provider 없음, prod)면 플랫폼 키 LLM. 모델은 테넌트 model_id가 아니라
    PLATFORM_LLM_MODEL이다(테넌트 model_id는 다른 공급자 이름일 수 있다). 챗은 턴을 chat_task에서
    미리 차감하므로 남은 턴을 다시 보지 않고, 자료 정리(추출)는 남은 턴이 있을 때만 플랫폼 키를 쓴다."""
    from apps.tenants import trial

    if not trial.uses_platform_key(config) or not trial.platform_key_ready():
        return None
    # 체험·도서관 테넌트(#134)는 Trial Quota와 무관하다 — 추출도 남은 턴을 보지 않는다(턴 한도는 Try Session 몫).
    if require_turns and trial.remaining(config) <= 0 and not trial.ignores_tenant_provider(config):
        return None
    return trial.platform_llm()


def chat_provider(config) -> LLMProvider:
    """챗·메모리용 LLM provider (모델 = config.model_id)."""
    return _platform_trial_llm(config, require_turns=False) or _provider_from_config(config, config.model_id)


# 챗 그래프 노드가 쓸 chat provider를 흘리는 contextvar. 비밀키를 LangGraph state에
# 넣으면 Checkpoint(Postgres)에 영속되므로, state 대신 호출 컨텍스트로 전달한다.
_current_chat_provider: contextvars.ContextVar["LLMProvider | None"] = contextvars.ContextVar(
    "chat_provider", default=None
)


def set_chat_provider(provider: LLMProvider) -> None:
    _current_chat_provider.set(provider)


def get_chat_provider():
    return _current_chat_provider.get()


def extraction_provider(config) -> LLMProvider:
    """GraphRAG 추출용 LLM provider. 모델 우선순위: 명시한 추출 모델 → 챗 모델(model_id)
    → 플랫폼 기본. 어드민에서 'AI 모델' 하나만 고른 테넌트는 자료 정리도 챗 모델로 한다
    (테넌트 자기 provider에 없을 수 있는 플랫폼 모델명 대신 본인이 고른 모델 사용)."""
    from django.conf import settings

    model = config.extraction_model or config.model_id or settings.GRAPH_EXTRACTION_MODEL
    return _platform_trial_llm(config, require_turns=True) or _provider_from_config(config, model)


def ocr_provider(config) -> LLMProvider:
    """OCR(Vision)용 LLM provider. LLM Provider와 독립 설정이며 LLM-shaped(anthropic 포함).

    `ocr_provider_type`이 설정된 경우에만 호출된다(get_ocr_backend이 미설정 시 UnconfiguredOCR로 분기).
    그래서 여기선 플랫폼 기본 폴백 분기가 없다 — 항상 테넌트가 고른 provider를 해석한다.
    """
    from apps.tenants.crypto import decrypt_secret

    return LLMProvider(
        type=config.ocr_provider_type,
        model=config.ocr_model,
        base_url=config.ocr_base_url,
        api_key=decrypt_secret(config.ocr_api_key) if config.ocr_api_key else "",
    )


def embedding_provider(config) -> EmbeddingProvider:
    """LLM Provider와 독립된 Embedding Provider. 미설정 시 dev는 ollama 폴백,
    prod(PLATFORM_DEFAULT_PROVIDERS_ENABLED=False)는 Tenant 설정을 강제한다."""
    from django.conf import settings

    from apps.tenants import trial

    if config and config.embed_provider_type and not trial.ignores_tenant_provider(config):  # 체험은 무시(#134)
        from apps.tenants.crypto import decrypt_secret
        from apps.agent.provider_models import _openai_base

        return EmbeddingProvider(
            type=config.embed_provider_type,
            # openai는 어드민에서 base_url을 노출하지 않으므로 표준 주소로 보정한다.
            base_url=_openai_base(config.embed_provider_type, config.embed_base_url),
            model=config.embed_model,
            dim=config.embed_dim,
            api_key=decrypt_secret(config.embed_api_key),
        )
    if config and trial.uses_platform_key(config) and trial.platform_key_ready():
        return trial.platform_embedding(config.embed_dim)
    if getattr(settings, "PLATFORM_DEFAULT_PROVIDERS_ENABLED", False):
        return EmbeddingProvider(
            type="", base_url=f"{settings.OLLAMA_BASE_URL}/v1",
            model=settings.OLLAMA_EMBED_MODEL, dim=1024, api_key="ollama",
        )
    raise ValueError("Embedding Provider가 설정되지 않았습니다 (프로덕션은 Tenant 설정 필수)")
