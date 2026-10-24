"""ProviderModels (deep module) — provider API에서 모델 목록 조회 + 연결 검증 (issue 114).

provider HTTP는 외부 경계다. 타입별로 모델 목록 엔드포인트를 정규화하고, 저장 전 연결을
provider의 실제 기능 호출로 검증한다(ADR-0012 확장).

custom base_url은 테넌트가 넣은 값이다(#108): 저장 때 `ensure_provider_url`(https·해석 IP 모두 공용)로 보고,
부를 때도 `guarded_http_client`가 연결마다 다시 검사한다(저장 뒤 DNS 리바인딩 대비).
"""
from apps.core.safe_http import (
    HttpClientError,
    SafeHttpError,
    check_provider_url,
    guarded_http_client,
    user_message,
)

DEFAULT_TIMEOUT = 15.0
ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com/v1"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class ProviderError(Exception):
    """provider 조회·검증 실패(연결 불가·키 오류 등). 사람이 읽는 메시지."""


def ensure_provider_url(type: str, base_url: str) -> None:
    """테넌트가 넣은 base_url이면 저장·조회 전에 검사한다. 플랫폼 기본(type="")·빈 값(표준 주소)은 건너뛴다."""
    if not type or not base_url:
        return
    try:
        check_provider_url(base_url)
    except SafeHttpError as err:
        import logging

        logging.getLogger(__name__).warning("provider base_url refused: %s", err)  # 해석 IP는 로그에만
        raise ProviderError(f"Provider 주소를 쓸 수 없습니다: {user_message(err)}") from err


def _get(url: str, headers: dict[str, str] | None = None):
    with guarded_http_client(DEFAULT_TIMEOUT) as client:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def _openai_base(type: str, base_url: str) -> str:
    """openai 타입은 base_url 미입력 시 표준 OpenAI 주소로 보정한다.
    어드민은 custom일 때만 base_url을 노출하므로 openai는 빈 값으로 들어온다."""
    return base_url or (OPENAI_DEFAULT_BASE if type == "openai" else base_url)


def _ollama_tags(base_url: str) -> list[str]:
    # 플랫폼 기본 임베딩(dev ollama)은 /api/tags. base_url 끝의 /v1를 떼어낸다.
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return [m["name"] for m in _get(f"{base}/api/tags").get("models", [])]


def _anthropic_models(base_url: str, api_key: str) -> list[str]:
    body = _get(
        f"{(base_url or ANTHROPIC_DEFAULT_BASE).rstrip('/')}/models",
        headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
    )
    return [m["id"] for m in body.get("data", [])]


def _openai_models(base_url: str, api_key: str) -> list[str]:
    body = _get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key or 'x'}"})
    return [m["id"] for m in body.get("data", [])]


def list_provider_models(kind: str, type: str, base_url: str, api_key: str) -> list[str]:
    """provider의 모델 id 목록을 정규화해 반환한다. 실패 시 ProviderError.

    type="" 플랫폼 기본은 kind로 분기한다: embed→ollama /api/tags, llm→OpenRouter /models.
    """
    ensure_provider_url(type, base_url)
    try:
        if type == "anthropic":
            return _anthropic_models(base_url, api_key)
        if type == "" and kind == "embed":
            return _ollama_tags(base_url)
        # openai / custom / "" (llm, OpenAI-호환) → /models
        return _openai_models(_openai_base(type, base_url), api_key)
    except HttpClientError as e:
        raise ProviderError(f"모델 조회 실패: {e}") from e


def validate_provider(kind: str, type: str, base_url: str, api_key: str, model: str, tenant_id=None) -> None:
    """provider 연결을 실제 기능 호출로 검증한다. 실패 시 ProviderError.

    kind=embed는 1-텍스트 임베딩 호출(provider 실제 용도), kind=llm은 모델 목록 조회로
    연결+키를 확인한다. 특정 model 상장 여부는 강제하지 않는다(연결성만 검증).

    tenant_id가 주어지면(어드민 저장 경로) 검증 임베딩 프로브도 사용량으로 기록한다 — OpenAI엔
    과금되지만 어디에도 안 잡히던 갭을 닫는다(TokenUsage + Langfuse, issue 204).
    """
    ensure_provider_url(type, base_url)
    if kind == "embed":
        try:
            with guarded_http_client(DEFAULT_TIMEOUT) as client:
                r = client.post(
                    f"{_openai_base(type, base_url).rstrip('/')}/embeddings",
                    json={"model": model, "input": ["ok"]},
                    headers={"Authorization": f"Bearer {api_key or 'x'}"},
                )
                r.raise_for_status()
        except HttpClientError as e:
            raise ProviderError(f"임베딩 Provider 검증 실패: {e}") from e
        if tenant_id:
            try:
                body = r.json()
            except Exception:
                body = {}
            from apps.usage.recording import record_embedding_usage
            from apps.usage.langfuse_client import record_embedding_langfuse
            record_embedding_usage(body, tenant_id, model)
            record_embedding_langfuse(body, tenant_id, model, ["ok"], call_type="embedding")
        return
    # llm → 목록 조회 성공으로 연결 검증(실패 시 ProviderError 전파)
    list_provider_models(kind, type, base_url, api_key)
