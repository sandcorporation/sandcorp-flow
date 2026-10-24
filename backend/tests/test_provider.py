import pytest

from tests.provider_http_fake import patch_provider_http


# ── Issue 92: 키 암호화 deep module ───────────────────────────────────────────

def test_encrypt_decrypt_round_trip():
    """Tenant API 키는 암호화 저장되고 복호화로 원문이 복원된다(평문 노출 없음)."""
    from apps.tenants.crypto import encrypt_secret, decrypt_secret

    secret = "sk-tenant-key-abcdef123456"
    enc = encrypt_secret(secret)

    assert enc != secret  # 저장값은 평문이 아니다
    assert decrypt_secret(enc) == secret  # 복호화 왕복
    assert encrypt_secret("") == ""  # 빈 값은 그대로
    assert decrypt_secret("") == ""


# ── Issue 92: ProviderResolver — 타입별 클라이언트 분기 ────────────────────────

def test_resolver_builds_openai_compatible_for_custom():
    """openai/custom 타입은 OpenAI-호환 클라이언트(base_url·model 반영)를 만든다."""
    from langchain_openai import ChatOpenAI
    from apps.agent.providers import LLMProvider, build_llm_client

    client = build_llm_client(LLMProvider(
        type="custom", model="some-model", base_url="https://my-endpoint/v1", api_key="sk-x",
    ))
    assert isinstance(client, ChatOpenAI)
    assert client.model_name == "some-model"


def test_resolver_builds_anthropic_native_for_anthropic():
    """anthropic 타입은 Anthropic 네이티브 클라이언트를 만든다(OpenAI-호환 아님)."""
    from langchain_anthropic import ChatAnthropic
    from apps.agent.providers import LLMProvider, build_llm_client

    client = build_llm_client(LLMProvider(
        type="anthropic", model="claude-sonnet-4-5", api_key="sk-ant",
    ))
    assert isinstance(client, ChatAnthropic)
    assert client.model == "claude-sonnet-4-5"


# ── Issue 92: provider 설정 API — 키 암호화·write-only ─────────────────────────

@pytest.mark.django_db
def test_llm_provider_config_key_encrypted_and_masked(client, tenant_admin_token, tenant_with_key, monkeypatch):
    """LLM provider 설정 저장 시 키는 암호화되고, GET 응답엔 평문이 노출되지 않는다."""
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import decrypt_secret
    from apps.agent import provider_models

    # provider 저장은 이제 연결을 검증한다(issue 116) → provider HTTP를 Fake로 통과시킨다.
    class _Resp:
        status_code = 200
        def json(self): return {"data": [{"id": "gpt-4o-mini"}]}
        def raise_for_status(self): pass
    patch_provider_http(monkeypatch, get=lambda *a, **k: _Resp())

    tenant, _ = tenant_with_key
    r = client.patch(
        "/api/tenant/config/",
        {
            "llm_provider_type": "custom",
            "llm_base_url": "https://my-endpoint/v1",
            "llm_api_key": "sk-tenant-secret",
            "extraction_model": "gpt-4o-mini",
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert r.status_code == 200

    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert g["llm_provider_type"] == "custom"
    assert g["llm_base_url"] == "https://my-endpoint/v1"
    assert g["llm_api_key"] != "sk-tenant-secret"  # 평문 미반환(마스킹)

    config = TenantConfig.objects.get(tenant=tenant)
    assert config.llm_api_key != "sk-tenant-secret"  # 암호화 저장
    assert decrypt_secret(config.llm_api_key) == "sk-tenant-secret"


@pytest.mark.django_db
def test_saving_masked_key_preserves_real_key(client, tenant_admin_token, tenant_with_key):
    """마스킹된 키('********')를 그대로 다시 저장해도 실제 키가 보존된다.

    어드민은 GET으로 받은 config(마스킹된 키 포함) 전체를 PATCH로 되돌려 보낸다.
    이때 마스크 값을 그대로 암호화 저장하면 실제 키가 파괴되므로, 마스크는 무시해야 한다.
    """
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import decrypt_secret

    tenant, _ = tenant_with_key

    def patch(body):
        return client.patch(
            "/api/tenant/config/", body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )

    patch({"llm_api_key": "sk-real-llm", "embed_api_key": "sk-real-embed"})
    masked = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert masked["llm_api_key"] == "********" and masked["embed_api_key"] == "********"

    # 어드민처럼 받은 config 전체(마스킹된 키 포함)를 무관한 변경과 함께 되돌려 보낸다
    patch({**masked, "welcome_message": "변경됨"})

    config = TenantConfig.objects.get(tenant=tenant)
    assert decrypt_secret(config.llm_api_key) == "sk-real-llm", "마스크 저장이 실제 키를 파괴함"
    assert decrypt_secret(config.embed_api_key) == "sk-real-embed"


# ── Issue 92: 챗 호출이 Tenant LLM provider로 라우팅 ──────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_chat_routes_through_tenant_llm_provider(tenant_with_key, fake_chat_llm):
    """run_chat_agent의 LLM 호출이 Tenant가 설정한 provider로 라우팅된다(키 복호화 전달)."""
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession
    from apps.tenants.models import TenantConfig
    from apps.tenants.crypto import encrypt_secret
    adb = sync_to_async

    tenant, _ = tenant_with_key

    def _setup():
        config = TenantConfig.objects.get(tenant=tenant)
        config.llm_provider_type = "custom"
        config.llm_base_url = "https://tenant-endpoint.example/v1"
        config.llm_api_key = encrypt_secret("sk-tenant")
        config.save()
    await adb(_setup)()

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-provider")
    await run_chat_turn_async(session, "안녕하세요")

    prov = fake_chat_llm.last_provider
    assert prov is not None and prov.type == "custom"
    assert prov.base_url == "https://tenant-endpoint.example/v1"
    assert prov.api_key == "sk-tenant"  # 복호화되어 경계로 전달


@pytest.mark.django_db(transaction=True)
async def test_chat_falls_back_to_platform_provider_when_unset(tenant_with_key, fake_chat_llm):
    """provider 미설정 Tenant는 플랫폼 기본(OpenRouter)으로 폴백한다."""
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session = await sync_to_async(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-default")
    await run_chat_turn_async(session, "안녕하세요")

    prov = fake_chat_llm.last_provider
    assert prov is not None and prov.type == ""  # 플랫폼 기본


@pytest.mark.django_db
def test_llm_provider_dev_fallback_and_prod_required(tenant_with_key, settings):
    """LLM 미설정 시 dev는 OpenRouter 폴백, prod(플래그 off)는 Tenant 설정을 강제한다."""
    from apps.tenants.models import TenantConfig
    from apps.agent.providers import chat_provider

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.llm_provider_type = ""  # 미설정
    config.save()

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = True
    assert chat_provider(config).type == ""  # dev 폴백(OpenRouter)

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    with pytest.raises(ValueError):
        chat_provider(config)


@pytest.mark.django_db
def test_extraction_falls_back_to_chat_model_when_unset(tenant_with_key):
    """추출 모델 미설정 시 챗 모델(model_id)을 그대로 쓴다(어드민 '대화 모델과 동일').

    어드민에서 'AI 모델' 하나만 고른 비개발자 테넌트는 자료 정리도 그 모델로 한다.
    명시한 추출 모델이 있으면 그쪽이 우선한다.
    """
    from apps.tenants.models import TenantConfig
    from apps.agent.providers import extraction_provider

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.llm_provider_type = "custom"  # 타입 지정 → 플랫폼 플래그와 무관하게 결정적
    config.llm_base_url = "https://x/v1"
    config.model_id = "chat-model-x"
    config.extraction_model = ""  # 미설정 → 챗 모델로 폴백
    config.save()
    assert extraction_provider(config).model == "chat-model-x"

    config.extraction_model = "explicit-extract"  # 명시 시 그쪽 우선
    config.save()
    assert extraction_provider(config).model == "explicit-extract"


@pytest.mark.django_db
def test_config_exposes_platform_default_providers_flag(client, tenant_admin_token, settings):
    """어드민 UI가 prod에서 '기본' Provider 옵션을 숨기도록 config가 플래그를 노출한다."""
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    resp = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.status_code == 200
    assert resp.json()["platform_default_providers_enabled"] is False
