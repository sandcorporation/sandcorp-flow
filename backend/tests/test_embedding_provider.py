import pytest

from tests.provider_http_fake import patch_provider_http


# ── Issue 93: get_embeddings를 OpenAI-호환 /v1/embeddings로 ───────────────────

def test_get_embeddings_uses_provider_v1_endpoint():
    """get_embeddings가 EmbeddingProvider의 OpenAI-호환 /v1/embeddings로 임베딩을 얻는다."""
    from django.conf import settings
    from apps.rag.ingesters import get_embeddings
    from apps.agent.providers import EmbeddingProvider

    prov = EmbeddingProvider(
        type="custom",
        base_url=f"{settings.OLLAMA_BASE_URL}/v1",
        api_key="ollama",
        model=settings.OLLAMA_EMBED_MODEL,
        dim=1024,
    )
    embs = get_embeddings(["hello world"], provider=prov)
    assert len(embs) == 1
    assert len(embs[0]) == 1024  # bge-m3 차원


def test_get_embeddings_default_platform_still_works():
    """provider 미지정 시 플랫폼 기본(ollama /v1)으로 동작한다(기존 호출부 호환)."""
    from apps.rag.ingesters import get_embeddings

    embs = get_embeddings(["another text"])
    assert len(embs[0]) == 1024


@pytest.mark.django_db
def test_embedding_provider_independent_config_key_encrypted(client, tenant_admin_token, tenant_with_key, monkeypatch):
    """Embedding Provider는 LLM과 독립 설정되고, 키는 암호화·마스킹된다."""
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import decrypt_secret
    from apps.agent.providers import embedding_provider
    from apps.agent import provider_models

    # embed provider 저장은 연결을 검증한다(issue 116) → provider HTTP를 Fake로 통과시킨다.
    class _Resp:
        status_code = 200
        def json(self): return {"data": [{"embedding": [0.1]}]}
        def raise_for_status(self): pass
    patch_provider_http(monkeypatch, post=lambda *a, **k: _Resp())

    tenant, _ = tenant_with_key
    r = client.patch(
        "/api/tenant/config/",
        {
            "embed_provider_type": "openai",
            "embed_base_url": "https://api.openai.com/v1",
            "embed_api_key": "sk-embed-secret",
            "embed_model": "text-embedding-3-small",
            "embed_dim": 1536,
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert r.status_code == 200

    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["embed_provider_type"] == "openai"
    assert g["embed_dim"] == 1536
    assert g["embed_api_key"] != "sk-embed-secret"  # 마스킹

    config = TenantConfig.objects.get(tenant=tenant)
    assert decrypt_secret(config.embed_api_key) == "sk-embed-secret"

    ep = embedding_provider(config)
    assert ep.type == "openai" and ep.model == "text-embedding-3-small" and ep.dim == 1536
    assert ep.api_key == "sk-embed-secret"  # 복호화 전달


@pytest.mark.django_db
def test_embedding_provider_openai_defaults_base_url(tenant_with_key):
    """openai 임베딩 타입은 base_url 미입력 시 표준 OpenAI 주소로 보정된다.

    어드민은 custom일 때만 base_url을 노출하므로, openai는 빈 base_url로 저장된다.
    런타임 임베딩이 `f"{base_url}/embeddings"`로 조립되므로 보정이 없으면 깨진다.
    """
    from apps.tenants.models import TenantConfig
    from apps.agent.providers import embedding_provider

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.embed_provider_type = "openai"
    config.embed_base_url = ""
    config.embed_model = "text-embedding-3-small"
    config.save()
    assert embedding_provider(config).base_url == "https://api.openai.com/v1"


@pytest.mark.django_db
def test_embedding_provider_dev_fallback_and_prod_required(tenant_with_key, settings):
    """미설정 시 dev는 ollama 폴백, prod(폴백 비활성)는 Tenant 설정을 강제(거부)."""
    from apps.tenants.models import TenantConfig
    from apps.agent.providers import embedding_provider

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)  # embed provider 미설정

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = True
    assert embedding_provider(config).type == ""  # dev 폴백(ollama)

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    with pytest.raises(ValueError):
        embedding_provider(config)  # prod는 거부
