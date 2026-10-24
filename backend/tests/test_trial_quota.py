"""체험 한도(#41, ADR-0026): 자기 LLM Provider가 없는 테넌트는 남은 턴 동안만 플랫폼 OpenAI 키로 돈다.

LLM 호출은 외부 경계라 conftest의 결정적 Fake(`fake_chat_llm`)가 받고, 그 Fake가 받은 Provider로
「플랫폼 키로 불렸는가」를 본다. 차감·상태·세션은 실제 DB·Redis·taskiq(InMemory)로 돈다.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from asgiref.sync import sync_to_async

PLATFORM_KEY = "sk-platform-test"


@pytest.fixture
def prod_platform(settings):
    """운영처럼: dev 폴백 끔 + 플랫폼 OpenAI 키 있음."""
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = PLATFORM_KEY
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    settings.PLATFORM_EMBED_MODEL = "text-embedding-3-small"
    settings.TRIAL_TURNS = 200
    return settings


def _set_remaining(tenant, n):
    from apps.tenants.models import Tenant

    Tenant.objects.filter(pk=tenant.pk).update(trial_turns_remaining=n)


def _remaining(tenant):
    from apps.tenants.models import Tenant

    return Tenant.objects.get(pk=tenant.pk).trial_turns_remaining


def _byok(tenant):
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantConfig

    config = TenantConfig.objects.get(tenant=tenant)
    config.llm_provider_type = "openai"
    config.llm_api_key = encrypt_secret("sk-tenant-own")
    config.model_id = "gpt-4.1"
    config.save()


async def _turn(tenant, content="안녕"):
    """방문자 한 턴: POST /chat/message → taskiq(InMemory) → 답 저장. 마지막 assistant 답을 돌려준다."""
    from django.test import AsyncClient
    from apps.chat.models import ChatMessage, ChatSession

    session = await sync_to_async(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id=f"v-{content}")
    resp = await AsyncClient().post(
        "/api/chat/message",
        data={"session_id": str(session.id), "content": content},
        content_type="application/json",
    )
    assert resp.status_code == 202
    return await sync_to_async(
        lambda: list(ChatMessage.objects.filter(session=session, role="assistant").values_list("content", flat=True))
    )()


@pytest.mark.django_db(transaction=True)
async def test_trial_turns_199_200_ok_201_paused(tenant_with_key, prod_platform, fake_chat_llm):
    from apps.tenants.trial import VISITOR_PAUSED_MESSAGE

    tenant, _ = tenant_with_key
    await sync_to_async(_set_remaining)(tenant, 2)  # 198턴을 이미 쓴 테넌트

    assert await _turn(tenant, "199번째") == ["안녕하세요! 무엇을 도와드릴까요?"]
    assert await sync_to_async(_remaining)(tenant) == 1
    assert await _turn(tenant, "200번째") == ["안녕하세요! 무엇을 도와드릴까요?"]
    assert await sync_to_async(_remaining)(tenant) == 0

    calls_before = len(fake_chat_llm.captured)
    assert await _turn(tenant, "201번째") == [VISITOR_PAUSED_MESSAGE]
    assert len(fake_chat_llm.captured) == calls_before  # 플랫폼 키를 더 부르지 않는다
    assert await sync_to_async(_remaining)(tenant) == 0


@pytest.mark.django_db(transaction=True)
async def test_trial_turn_runs_on_platform_openai_key(tenant_with_key, prod_platform, fake_chat_llm):
    tenant, _ = tenant_with_key
    await _turn(tenant)
    p = fake_chat_llm.last_provider
    assert (p.type, p.api_key, p.model, p.base_url) == (
        "openai", PLATFORM_KEY, "gpt-4o-mini", prod_platform.PLATFORM_OPENAI_BASE_URL,
    )


@pytest.mark.django_db(transaction=True)
async def test_byok_tenant_never_consumes_turns(tenant_with_key, prod_platform, fake_chat_llm):
    tenant, _ = tenant_with_key
    await sync_to_async(_byok)(tenant)
    await sync_to_async(_set_remaining)(tenant, 0)  # 체험을 다 써도

    assert await _turn(tenant) == ["안녕하세요! 무엇을 도와드릴까요?"]  # 자기 키로 돈다
    assert fake_chat_llm.last_provider.api_key == "sk-tenant-own"
    assert await sync_to_async(_remaining)(tenant) == 0


@pytest.mark.django_db(transaction=True)
async def test_missing_platform_key_pauses_politely_without_error(tenant_with_key, prod_platform, fake_chat_llm):
    from apps.tenants.trial import VISITOR_PAUSED_MESSAGE

    prod_platform.PLATFORM_OPENAI_API_KEY = ""
    tenant, _ = tenant_with_key
    assert await _turn(tenant) == [VISITOR_PAUSED_MESSAGE]
    assert fake_chat_llm.captured == []
    assert await sync_to_async(_remaining)(tenant) == 200  # 키가 없어서 못 돈 턴은 세지 않는다


@pytest.mark.django_db(transaction=True)
async def test_dev_platform_default_does_not_count_turns(tenant_with_key, settings, fake_chat_llm):
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = True
    settings.PLATFORM_OPENAI_API_KEY = PLATFORM_KEY
    tenant, _ = tenant_with_key
    before = await sync_to_async(_remaining)(tenant)
    await _turn(tenant)
    assert fake_chat_llm.last_provider.type == ""  # 기존 dev 폴백(fake-llm/OpenRouter 자리)
    assert await sync_to_async(_remaining)(tenant) == before


def test_visitor_message_does_not_leak_internals():
    from apps.tenants.trial import VISITOR_PAUSED_MESSAGE

    for word in ("체험", "한도", "키", "LLM", "턴", "요금"):
        assert word not in VISITOR_PAUSED_MESSAGE


@pytest.mark.django_db(transaction=True)
def test_concurrent_admits_never_go_negative(tenant_with_key, prod_platform):
    from django.db import connection
    from apps.tenants.models import TenantConfig
    from apps.tenants.trial import admit_turn

    tenant, _ = tenant_with_key
    _set_remaining(tenant, 5)
    results: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def worker():
        from django.db import connection as conn

        try:
            config = TenantConfig.objects.get(tenant_id=tenant.id)
            start.wait()
            ok = admit_turn(config)
            with lock:
                results.append(ok)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    connection.close()

    assert results.count(True) == 5
    assert results.count(False) == 15
    assert _remaining(tenant) == 0


# ── 관리 화면 상태 ─────────────────────────────────────────────────────────


def _trial(client, token):
    r = client.get("/api/tenant/trial/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 200
    return r.json()


@pytest.mark.django_db
def test_admin_sees_trial_remaining(client, tenant_with_key, tenant_admin_token, prod_platform):
    tenant, _ = tenant_with_key
    _set_remaining(tenant, 37)
    assert _trial(client, tenant_admin_token) == {"state": "trial", "remaining": 37, "limit": 200}


@pytest.mark.django_db
def test_admin_sees_exhausted_banner_state(client, tenant_with_key, tenant_member_token, prod_platform):
    tenant, _ = tenant_with_key
    _set_remaining(tenant, 0)
    assert _trial(client, tenant_member_token)["state"] == "exhausted"


@pytest.mark.django_db
def test_admin_sees_unavailable_when_platform_key_missing(client, tenant_admin_token, prod_platform):
    prod_platform.PLATFORM_OPENAI_API_KEY = ""
    assert _trial(client, tenant_admin_token)["state"] == "unavailable"


@pytest.mark.django_db
def test_byok_and_dev_states(client, tenant_with_key, tenant_admin_token, settings):
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = True
    assert _trial(client, tenant_admin_token)["state"] == "platform_default"
    tenant, _ = tenant_with_key
    _byok(tenant)
    assert _trial(client, tenant_admin_token)["state"] == "byok"


# ── 자료 정리(임베딩·추출)도 체험 안에서 ─────────────────────────────────────


@pytest.mark.django_db
def test_extraction_uses_platform_key_only_while_turns_remain(tenant_with_key, prod_platform):
    from apps.agent.providers import extraction_provider
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    assert extraction_provider(config).api_key == PLATFORM_KEY
    _set_remaining(tenant, 0)
    with pytest.raises(ValueError):
        extraction_provider(config)


class _EmbeddingsCapture(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append({"auth": self.headers["Authorization"], **body})
        data = [{"embedding": [0.0] * body.get("dimensions", 3)} for _ in body["input"]]
        out = json.dumps({"data": data, "usage": {"prompt_tokens": 1, "total_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


@pytest.mark.django_db
def test_trial_embeddings_use_platform_key_sized_to_tenant_dim(tenant_with_key, prod_platform):
    from apps.agent.providers import embedding_provider
    from apps.rag.ingesters import get_embeddings
    from apps.tenants.models import TenantConfig

    server = HTTPServer(("127.0.0.1", 0), _EmbeddingsCapture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    prod_platform.PLATFORM_OPENAI_BASE_URL = f"http://127.0.0.1:{server.server_port}/v1"
    _EmbeddingsCapture.bodies = []
    try:
        tenant, _ = tenant_with_key
        config = TenantConfig.objects.get(tenant=tenant)
        vectors = get_embeddings(["가", "나"], provider=embedding_provider(config))
    finally:
        server.shutdown()

    assert [len(v) for v in vectors] == [config.embed_dim, config.embed_dim]
    sent = _EmbeddingsCapture.bodies[0]
    assert sent["auth"] == f"Bearer {PLATFORM_KEY}"
    assert (sent["model"], sent["dimensions"]) == ("text-embedding-3-small", config.embed_dim)
