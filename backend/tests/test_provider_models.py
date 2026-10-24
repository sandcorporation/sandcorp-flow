# pyright: reportOptionalSubscript=false, reportArgumentType=false
"""Issue 114 — ProviderModels 딥모듈(모델 조회 + 기능호출 검증).

provider HTTP는 외부 경계이므로 결정적 Fake로 교체한다(CLAUDE.md). DB 불필요.
"""
import pytest

from tests.provider_http_fake import patch_provider_http


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


# ── Tracer: OpenAI-호환 /models ───────────────────────────────────────────────

def test_list_openai_compatible_models(monkeypatch):
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/models")
        assert headers["Authorization"] == "Bearer sk-x"
        return _FakeResp(200, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})

    patch_provider_http(monkeypatch, get=fake_get)

    models = provider_models.list_provider_models("llm", "openai", "https://api.openai.com/v1", "sk-x")
    assert models == ["gpt-4o", "gpt-4o-mini"]


def test_openai_empty_base_url_defaults_to_canonical(monkeypatch):
    """openai 타입은 base_url 미입력 시 표준 OpenAI 주소로 보정한다(어드민 custom만 base_url)."""
    from apps.agent import provider_models

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResp(200, {"data": [{"id": "gpt-4o"}]})

    patch_provider_http(monkeypatch, get=fake_get)
    provider_models.list_provider_models("llm", "openai", "", "sk-x")
    assert seen["url"] == "https://api.openai.com/v1/models"


def test_validate_embed_openai_empty_base_defaults(monkeypatch):
    """openai 임베딩 검증도 base_url 미입력 시 표준 OpenAI 주소로 보정한다."""
    from apps.agent import provider_models

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResp(200, {"data": [{"embedding": [0.1]}]})

    patch_provider_http(monkeypatch, post=fake_post)
    provider_models.validate_provider("embed", "openai", "", "sk", "text-embedding-3-small")
    assert seen["url"] == "https://api.openai.com/v1/embeddings"


def test_list_anthropic_models(monkeypatch):
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        assert "anthropic" in url and url.endswith("/models")
        assert headers["x-api-key"] == "sk-ant"
        assert headers["anthropic-version"]
        return _FakeResp(200, {"data": [{"id": "claude-opus-4"}, {"id": "claude-haiku"}]})

    patch_provider_http(monkeypatch, get=fake_get)
    models = provider_models.list_provider_models("llm", "anthropic", "", "sk-ant")
    assert models == ["claude-opus-4", "claude-haiku"]


def test_list_ollama_tags_for_platform_embed(monkeypatch):
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/api/tags")  # /v1 떼고 /api/tags
        return _FakeResp(200, {"models": [{"name": "bge-m3"}, {"name": "nomic-embed"}]})

    patch_provider_http(monkeypatch, get=fake_get)
    models = provider_models.list_provider_models("embed", "", "http://ollama:11434/v1", "ollama")
    assert models == ["bge-m3", "nomic-embed"]


def test_list_models_raises_provider_error_on_failure(monkeypatch):
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(401, {})  # 키 오류

    patch_provider_http(monkeypatch, get=fake_get)
    with pytest.raises(provider_models.ProviderError):
        provider_models.list_provider_models("llm", "openai", "https://api.openai.com/v1", "bad")


def test_validate_llm_uses_model_list(monkeypatch):
    from apps.agent import provider_models

    calls = {"get": 0}

    def fake_get(url, headers=None, timeout=None):
        calls["get"] += 1
        return _FakeResp(200, {"data": [{"id": "m"}]})

    patch_provider_http(monkeypatch, get=fake_get)
    provider_models.validate_provider("llm", "openai", "https://x/v1", "sk", "m")  # 통과
    assert calls["get"] == 1

    def fail_get(url, headers=None, timeout=None):
        return _FakeResp(403, {})

    patch_provider_http(monkeypatch, get=fail_get)
    with pytest.raises(provider_models.ProviderError):
        provider_models.validate_provider("llm", "openai", "https://x/v1", "bad", "m")


def test_validate_embed_uses_real_embedding_call(monkeypatch):
    from apps.agent import provider_models

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/embeddings")
        assert json["input"] == ["ok"]
        return _FakeResp(200, {"data": [{"embedding": [0.1]}]})

    patch_provider_http(monkeypatch, post=fake_post)
    provider_models.validate_provider("embed", "openai", "https://x/v1", "sk", "text-embedding-3-small")

    def fail_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(401, {})

    patch_provider_http(monkeypatch, post=fail_post)
    with pytest.raises(provider_models.ProviderError):
        provider_models.validate_provider("embed", "openai", "https://x/v1", "bad", "m")


# ── 엔드포인트 POST /api/tenant/providers/models (issue 115) ───────────────────

def _patch_get(monkeypatch, payload, status=200, capture=None):
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        if capture is not None:
            capture["headers"] = headers or {}
        return _FakeResp(status, payload)

    patch_provider_http(monkeypatch, get=fake_get)


@pytest.mark.django_db
def test_provider_models_endpoint_returns_models(client, tenant_admin_token, monkeypatch):
    _patch_get(monkeypatch, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "llm", "type": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-x"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert "api_key" not in data and "sk-x" not in resp.content.decode()


@pytest.mark.django_db
def test_provider_models_endpoint_masked_key_uses_stored(client, tenant_admin_token, tenant_with_key, monkeypatch):
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import encrypt_secret

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.llm_api_key = encrypt_secret("real-stored-key")
    config.save()

    cap = {}
    _patch_get(monkeypatch, {"data": [{"id": "m"}]}, capture=cap)
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "llm", "type": "openai", "base_url": "https://x/v1", "api_key": "********"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    assert cap["headers"]["Authorization"] == "Bearer real-stored-key"


@pytest.mark.django_db
def test_provider_models_endpoint_failure_is_4xx(client, tenant_admin_token, monkeypatch):
    _patch_get(monkeypatch, {}, status=401)
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "llm", "type": "openai", "base_url": "https://x/v1", "api_key": "bad"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


@pytest.mark.django_db
def test_provider_models_endpoint_requires_auth(client):
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "llm", "type": "openai", "base_url": "https://x/v1", "api_key": "x"},
        content_type="application/json",
    )
    assert resp.status_code == 401


# ── update_config provider 검증 (issue 116) ──────────────────────────────────

def _patch_validate(monkeypatch, get_status=200, get_payload=None, counter=None):
    """provider HTTP(get/post)를 Fake로 — validate_provider가 이걸 친다."""
    from apps.agent import provider_models

    def fake_get(url, headers=None, timeout=None):
        if counter is not None:
            counter["n"] += 1
        return _FakeResp(get_status, get_payload if get_payload is not None else {"data": []})

    def fake_post(url, json=None, headers=None, timeout=None):
        if counter is not None:
            counter["n"] += 1
        return _FakeResp(get_status, {"data": [{"embedding": [0.1]}]})

    patch_provider_http(monkeypatch, get=fake_get)
    patch_provider_http(monkeypatch, post=fake_post)


def _patch_config(client, token, body):
    return client.patch(
        "/api/tenant/config/", body, content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
def test_update_config_rejects_invalid_provider(client, tenant_admin_token, monkeypatch):
    _patch_validate(monkeypatch, get_status=401)  # provider 연결 실패
    resp = _patch_config(client, tenant_admin_token, {
        "llm_provider_type": "openai", "llm_base_url": "https://x/v1", "llm_api_key": "bad-key",
    })
    assert resp.status_code == 400
    # 저장되지 않아야 한다(롤백)
    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["llm_provider_type"] == ""


@pytest.mark.django_db
def test_update_config_saves_when_provider_valid(client, tenant_admin_token, monkeypatch):
    _patch_validate(monkeypatch, get_status=200, get_payload={"data": [{"id": "m"}]})
    resp = _patch_config(client, tenant_admin_token, {
        "llm_provider_type": "openai", "llm_base_url": "https://x/v1", "llm_api_key": "sk-new",
    })
    assert resp.status_code == 200
    assert resp.json()["llm_provider_type"] == "openai"


@pytest.mark.django_db
def test_update_config_skips_validation_when_provider_unchanged(client, tenant_admin_token, monkeypatch):
    counter = {"n": 0}
    _patch_validate(monkeypatch, counter=counter)
    resp = _patch_config(client, tenant_admin_token, {"system_prompt": "새 프롬프트"})
    assert resp.status_code == 200
    assert counter["n"] == 0  # provider 미변경 → 검증(HTTP) 미호출
    assert resp.json()["system_prompt"] == "새 프롬프트"


# ── OCR(Vision) Provider (issue 159) ──────────────────────────────────────────

@pytest.mark.django_db
def test_provider_models_endpoint_ocr_kind(client, tenant_admin_token, monkeypatch):
    """kind="ocr" 모델 목록은 LLM-style /models로 조회된다(vision 모델 선택용)."""
    _patch_get(monkeypatch, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "ocr", "type": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-x"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["gpt-4o", "gpt-4o-mini"]


@pytest.mark.django_db
def test_provider_models_ocr_masked_key_uses_stored_ocr_key(client, tenant_admin_token, tenant_with_key, monkeypatch):
    """kind="ocr" + 마스크 키는 저장된 ocr_api_key를 복호화해 쓴다(llm 키 아님)."""
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import encrypt_secret

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.ocr_api_key = encrypt_secret("ocr-stored-key")
    config.save()

    cap = {}
    _patch_get(monkeypatch, {"data": [{"id": "m"}]}, capture=cap)
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "ocr", "type": "openai", "base_url": "https://x/v1", "api_key": "********"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    assert cap["headers"]["Authorization"] == "Bearer ocr-stored-key"


@pytest.mark.django_db
def test_update_config_ocr_provider_roundtrip(client, tenant_admin_token, monkeypatch):
    """OCR Provider 저장: 검증 통과 시 저장되고 GET은 키를 마스킹한다(키 미노출)."""
    _patch_validate(monkeypatch, get_status=200, get_payload={"data": [{"id": "gpt-4o"}]})
    resp = _patch_config(client, tenant_admin_token, {
        "ocr_provider_type": "openai", "ocr_base_url": "https://x/v1",
        "ocr_model": "gpt-4o", "ocr_api_key": "sk-ocr",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ocr_provider_type"] == "openai"
    assert body["ocr_model"] == "gpt-4o"
    assert body["ocr_api_key"] == "********"  # 마스킹

    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["ocr_provider_type"] == "openai"
    assert "sk-ocr" not in str(g)  # 평문 키 미노출


@pytest.mark.django_db
def test_update_config_rejects_invalid_ocr_provider(client, tenant_admin_token, monkeypatch):
    """OCR Provider 연결 실패 시 400 + 미저장(롤백)."""
    _patch_validate(monkeypatch, get_status=401)
    resp = _patch_config(client, tenant_admin_token, {
        "ocr_provider_type": "openai", "ocr_base_url": "https://x/v1", "ocr_api_key": "bad",
    })
    assert resp.status_code == 400
    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["ocr_provider_type"] == ""


# ── OpenAI 한방 quick-setup (issue 168) ──────────────────────────────────────

def _quick_setup(client, token, api_key):
    return client.post(
        "/api/tenant/providers/quick-setup", {"api_key": api_key},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
def test_quick_setup_configures_all_three_openai(client, tenant_admin_token, monkeypatch):
    """유효 키 한 번으로 LLM·Embedding·OCR 3종이 openai 기본값으로 설정된다."""
    _patch_get(monkeypatch, {"data": [{"id": "gpt-4o-mini"}]})  # 키 검증(models 조회) 통과
    resp = _quick_setup(client, tenant_admin_token, "sk-openai-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_provider_type"] == "openai" and body["model_id"] == "gpt-4o-mini"
    assert body["embed_provider_type"] == "openai" and body["embed_model"] == "text-embedding-3-small"
    assert body["embed_dim"] == 1536
    assert body["ocr_provider_type"] == "openai" and body["ocr_model"] == "gpt-4o-mini"
    assert body["llm_api_key"] == "********"          # 마스킹
    assert "sk-openai-123" not in resp.content.decode()  # 평문 미노출


@pytest.mark.django_db
def test_quick_setup_invalid_key_400_nothing_saved(client, tenant_admin_token, monkeypatch):
    """키 검증 실패 시 400이고 config는 변경되지 않는다(원자성)."""
    _patch_get(monkeypatch, {}, status=401)
    resp = _quick_setup(client, tenant_admin_token, "bad")
    assert resp.status_code == 400
    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["llm_provider_type"] == "" and g["embed_provider_type"] == "" and g["ocr_provider_type"] == ""


@pytest.mark.django_db
def test_quick_setup_validates_key_once(client, tenant_admin_token, monkeypatch):
    """같은 키를 3번이 아니라 1회만 검증한다."""
    from apps.agent import provider_models
    cap = {"n": 0}
    def fake_get(url, headers=None, timeout=None):
        cap["n"] += 1
        return _FakeResp(200, {"data": [{"id": "gpt-4o-mini"}]})
    patch_provider_http(monkeypatch, get=fake_get)
    _quick_setup(client, tenant_admin_token, "sk-x")
    assert cap["n"] == 1


@pytest.mark.django_db
def test_quick_setup_key_encrypted(client, tenant_admin_token, tenant_with_key, monkeypatch):
    """저장 키는 암호화(복호화 시 원문) — 3종 모두 같은 키."""
    _patch_get(monkeypatch, {"data": [{"id": "gpt-4o-mini"}]})
    _quick_setup(client, tenant_admin_token, "sk-secret")
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import decrypt_secret
    tenant, _ = tenant_with_key
    cfg = TenantConfig.objects.get(tenant=tenant)
    assert cfg.llm_api_key != "sk-secret"
    assert decrypt_secret(cfg.llm_api_key) == "sk-secret"
    assert decrypt_secret(cfg.embed_api_key) == "sk-secret"
    assert decrypt_secret(cfg.ocr_api_key) == "sk-secret"
