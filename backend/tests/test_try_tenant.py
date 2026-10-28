"""체험 테넌트(Try Session 1/2, #134, ADR-0030).

Try Session마다 숨은 체험 테넌트(kind=try)를 만들고 예제 흐름 사본(초안)을 넣는다. 제약은 체험 테넌트면 한 자리
(``apps.tenants.try_tenant``)에서 강제한다: 플랫폼 키·``PLATFORM_LLM_MODEL`` 고정, HTTP 도구는 가짜 API만, 문서 업로드·
배포·실제 사람 넘김 거부. 지식은 공유 「체험 도서관」 테넌트의 그 예제 묶음만 검색한다. 24시간 뒤 테넌트째 지운다.

LLM은 conftest의 결정적 Fake, HTTP는 ``net`` 대역(네트워크 0 확인). DB·임베딩(ollama)·taskiq는 실제다.
"""
from __future__ import annotations

import datetime as dt
import secrets
from typing import cast

import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from django.utils import timezone

from tests.test_flow_http_tool import API, Net, _flow, _setup, _turn, net, tool_pki  # noqa: F401

EXAMPLE_IDS = ["sandcorp_home", "shop_support", "academy_reservation"]
PLATFORM_KEY = "sk-platform-test"


def _no_network(n: Net) -> None:
    assert n.dns.calls == [] and n.route == [] and n.hits.items == []


def _create(example_id: str = "shop_support", now: dt.datetime | None = None):
    from apps.tenants import try_tenant

    return try_tenant.create_try_tenant(example_id, now=now)


def _tenant(tenant_id):
    from apps.tenants.models import Tenant

    return Tenant.objects.get(pk=tenant_id)


@pytest.fixture
def prod_platform(settings):
    """운영처럼: dev 폴백 끔 + 플랫폼 OpenAI 키 있음."""
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = PLATFORM_KEY
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    settings.PLATFORM_EMBED_MODEL = "text-embedding-3-small"
    return settings


def _byok(tenant_id) -> None:
    """테넌트 자기 Provider(LLM·임베딩)를 넣는다 — 체험 테넌트면 무시돼야 한다."""
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantConfig

    config = TenantConfig.objects.get(tenant_id=tenant_id)
    config.llm_provider_type = "anthropic"
    config.llm_api_key = encrypt_secret("sk-tenant-own")
    config.model_id = "claude-sonnet"
    config.embed_provider_type = "custom"
    config.embed_base_url = "https://embed.example.com/v1"
    config.embed_model = "own-embed"
    config.embed_api_key = encrypt_secret("sk-embed-own")
    config.save()


# ── 만들기: 예제 사본 ────────────────────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize("example_id", EXAMPLE_IDS)
def test_try_tenant_gets_a_valid_draft_copy_of_the_example(example_id):
    from apps.flows import services
    from apps.flows.example_catalog import example_graph
    from apps.flows.models import Flow
    from apps.tenants import try_tenant

    before = timezone.now()
    created = _create(example_id)

    tenant = _tenant(created.tenant_id)
    assert tenant.kind == try_tenant.KIND_TRY and try_tenant.is_try(tenant.id)
    assert tenant.try_example_id == example_id == created.example_id
    assert before + dt.timedelta(hours=24) <= created.expires_at <= timezone.now() + dt.timedelta(hours=24)
    flow = Flow.objects.get(pk=created.flow_id)
    assert flow.tenant_id == tenant.id and flow.deployed_version_id is None and not flow.is_public
    graph = flow.draft.graph
    assert graph == example_graph(example_id)
    assert services.validate_for_tenant(tenant.id, graph) == []
    assert services.unsupported_node_errors(graph) == []


@pytest.mark.django_db
def test_unknown_example_is_refused_and_creates_nothing():
    from apps.tenants import try_tenant
    from apps.tenants.models import Tenant

    n = Tenant.objects.count()
    with pytest.raises(try_tenant.UnknownExample):
        _create("nope")
    assert Tenant.objects.count() == n


@pytest.mark.django_db
def test_editing_and_versions_still_work_but_deploy_is_refused():
    from apps.flows import services

    created = _create("academy_reservation")
    services.save_draft(created.tenant_id, created.flow_id, _tenant_graph(created))
    version = services.save_version(created.tenant_id, created.flow_id).version

    for deploy in (services.deploy_version, services.submit_deploy):
        with pytest.raises(services.FlowServiceError) as exc:
            deploy(created.tenant_id, created.flow_id, version.number)
        assert exc.value.code == "try_restricted"
    from apps.flows.models import Flow

    assert Flow.objects.get(pk=created.flow_id).deployed_version_id is None


def _tenant_graph(created):
    from apps.flows.models import Flow

    return Flow.objects.get(pk=created.flow_id).draft.graph


# ── 제약: 플랫폼 키 ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_try_tenant_uses_the_platform_key_even_with_its_own_provider(prod_platform):
    from apps.agent.providers import chat_provider, embedding_provider, extraction_provider
    from apps.tenants.models import TenantConfig

    created = _create()
    _byok(created.tenant_id)
    config = TenantConfig.objects.get(tenant_id=created.tenant_id)

    for p in (chat_provider(config), extraction_provider(config)):
        assert (p.type, p.api_key, p.model, p.base_url) == (
            "openai", PLATFORM_KEY, "gpt-4o-mini", prod_platform.PLATFORM_OPENAI_BASE_URL,
        )
    e = embedding_provider(config)
    assert (e.type, e.api_key, e.model) == ("openai", PLATFORM_KEY, "text-embedding-3-small")


@pytest.mark.django_db(transaction=True)
async def test_try_test_turn_runs_on_the_platform_key_with_a_tenant_provider(prod_platform, fake_chat_llm):
    from apps.flows import services

    created = await sync_to_async(_create)("sandcorp_home")
    await sync_to_async(_byok)(created.tenant_id)
    result = await services.run_test_turn(created.tenant_id, created.flow_id, "안녕하세요")

    assert result.ran
    p = fake_chat_llm.last_provider
    assert (p.type, p.api_key, p.model) == ("openai", PLATFORM_KEY, "gpt-4o-mini")


# ── 제약: HTTP 도구는 가짜 API만 ─────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_try_tenant_http_tool_refuses_a_real_url_even_when_allowed(net, fake_chat_llm):
    from apps.tenants import try_tenant

    created = await sync_to_async(_create)()
    await sync_to_async(_setup)(created.tenant_id, domains=(API,), secrets={"k": "sk-" + secrets.token_hex(8)})
    tenant = await sync_to_async(_tenant)(created.tenant_id)
    flow = _flow({"url": f"https://{API}/orders/1", "headers": {"X-Api-Key": "{{secret.k}}"}})

    result, _ = await _turn(tenant, flow)

    entry = next(t for t in result["trace"] if t["kind"] == "http_tool")
    assert entry["handle"] == "error" and entry["record"]["error"] == try_tenant.RESTRICTED
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_try_tenant_http_tool_reaches_the_fake_api(net, fake_chat_llm):
    created = await sync_to_async(_create)()
    tenant = await sync_to_async(_tenant)(created.tenant_id)
    flow = _flow({"url": "https://demo-api.flow.sandcorp.co.kr/orders/A1001?last=1234"})

    result, _ = await _turn(tenant, flow)

    entry = next(t for t in result["trace"] if t["kind"] == "http_tool")
    assert entry["handle"] != "error" and entry["record"].get("error") is None
    _no_network(net)


def test_try_restricted_is_a_known_failure_code():
    from apps.flows.http_tool import FAILURE_CODES
    from apps.tenants import try_tenant

    assert try_tenant.RESTRICTED in FAILURE_CODES


# ── 제약: 업로드·사람 넘김 ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_document_upload_and_url_add_are_refused_for_a_try_tenant(client, tenant_with_key, tenant_admin_token):
    """체험 테넌트에는 멤버가 없지만, 어떤 길로 와도 업로드 자리에서 막힌다(멤버를 억지로 붙여 확인)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.rag.models import Document
    from apps.tenants import try_tenant
    from apps.tenants.models import Tenant

    tenant, _ = tenant_with_key
    Tenant.objects.filter(pk=tenant.pk).update(kind=try_tenant.KIND_TRY)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {tenant_admin_token}"}

    r = client.post("/api/tenant/documents/", {"file": SimpleUploadedFile("a.txt", b"hello", "text/plain")}, **auth)
    assert r.status_code == 403 and try_tenant.RESTRICTED in r.content.decode()
    r = client.post("/api/tenant/documents/url", {"urls": ["https://example.com"]},
                    content_type="application/json", **auth)
    assert r.status_code == 403
    assert not Document.objects.filter(tenant_id=tenant.id).exists()


@pytest.mark.django_db(transaction=True)
async def test_try_tenant_handoff_sends_no_human_notification(fake_chat_llm):
    from apps.escalation.models import Escalation
    from apps.events.models import EventStore, Outbox
    from apps.flows.example_catalog import example_graph

    created = await sync_to_async(_create)("shop_support")
    tenant = await sync_to_async(_tenant)(created.tenant_id)
    events_before = await sync_to_async(Outbox.objects.count)()
    fake_chat_llm.by_schema["ClassifyResult"] = lambda messages, schema: schema(
        label="human_request", confidence=0.9, fields={"order_no": None})

    graph = cast(dict, example_graph("shop_support"))  # 예제 JSON은 객체다
    result, session = await _turn(tenant, graph, "사람이랑 얘기할래요")

    assert [t["node_id"] for t in result["trace"]][-1] == "handoff"
    assert await sync_to_async(Escalation.objects.filter(session_id=session.id).count)() == 0
    assert await sync_to_async(EventStore.objects.filter(tenant_id=str(tenant.id)).count)() == 0
    assert await sync_to_async(Outbox.objects.count)() == events_before


# ── 지식: 체험 도서관의 그 예제 묶음만 ────────────────────────────────────────

@pytest.mark.django_db
def test_try_knowledge_search_sees_only_its_example_in_the_library():
    from apps.flows.example_catalog import EXAMPLES
    from apps.flows.example_seed import seed_example_knowledge
    from apps.rag import services
    from apps.rag.models import Document
    from apps.tenants import try_tenant
    from apps.tenants.models import Tenant

    library = try_tenant.seed_library(["shop_support", "academy_reservation"])
    other = Tenant.objects.create_with_key(name="Other", raw_key=secrets.token_urlsafe(32))
    seed_example_knowledge(other.id, "shop_support")  # 다른 테넌트의 같은 예제 문서
    shop = _create("shop_support")

    assert _tenant(library).kind == try_tenant.KIND_TRY_LIBRARY
    assert not Document.objects.filter(tenant_id=shop.tenant_id).exists()  # 사본마다 재임베딩하지 않는다
    allowed = {str(d.id) for d in Document.objects.filter(tenant_id=library)
               if d.name in {x.name for x in EXAMPLES["shop_support"].docs}}
    assert len(allowed) == 3

    own = EXAMPLES["shop_support"].docs[0].name
    hits = services.local_search(shop.tenant_id, own, top_k=10)
    assert hits and all(h.source_document_id in allowed for h in hits)
    for foreign in EXAMPLES["academy_reservation"].docs:  # 도서관의 다른 예제 문서
        assert all(h.source_document_id in allowed for h in services.local_search(shop.tenant_id, foreign.name))


@pytest.mark.django_db
def test_try_source_fallback_reads_only_its_example_text():
    from asgiref.sync import async_to_sync

    from apps.agent.nodes import source_search_node
    from apps.flows.example_catalog import EXAMPLES
    from apps.tenants import try_tenant

    try_tenant.seed_library(["shop_support", "academy_reservation"])
    academy = _create("academy_reservation")
    texts = {d.text for d in EXAMPLES["academy_reservation"].docs}
    shop_text = EXAMPLES["shop_support"].docs[0].text

    out = async_to_sync(source_search_node)(
        {"tenant_id": str(academy.tenant_id), "user_message": shop_text, "rag_chunks": []})

    assert out["rag_chunks"], "자기 예제 원문은 찾아야 한다"
    # 원문 청크는 「문서 레이블 + 본문」이다 — 모두 학원 예제 본문에서 나와야 한다(도서관의 쇼핑몰 문서는 안 보임).
    def squash(x: str) -> str:
        return "".join(x.split())

    assert all(any(squash(t)[:30] in squash(chunk) for t in texts) for chunk in out["rag_chunks"])


@pytest.mark.django_db
def test_library_seed_uses_the_platform_embedding_path_and_is_idempotent(prod_platform):
    """운영(dev 폴백 끔)에서 도서관 테넌트는 자기 Provider 없이도 플랫폼 키 임베딩·추출 경로를 탄다."""
    from apps.agent.providers import embedding_provider, extraction_provider
    from apps.tenants import try_tenant
    from apps.tenants.models import Tenant, TenantConfig

    library = try_tenant.ensure_library()
    assert try_tenant.ensure_library() == library
    assert Tenant.objects.filter(kind=try_tenant.KIND_TRY_LIBRARY).count() == 1
    Tenant.objects.filter(pk=library).update(trial_turns_remaining=0)  # 도서관은 체험 턴과 무관하다
    config = TenantConfig.objects.get(tenant_id=library)
    assert embedding_provider(config).api_key == PLATFORM_KEY
    assert extraction_provider(config).api_key == PLATFORM_KEY


@pytest.mark.django_db
def test_seed_try_library_command_loads_every_example():
    from apps.flows.example_catalog import EXAMPLES
    from apps.rag.models import Document
    from apps.tenants import try_tenant

    call_command("seed_try_library", "--example", "sandcorp_home")
    library = try_tenant.ensure_library()
    names = set(Document.objects.filter(tenant_id=library, status=Document.STATUS_READY).values_list("name", flat=True))
    assert names == {d.name for d in EXAMPLES["sandcorp_home"].docs}


# ── 24시간 정리 ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_prune_keeps_a_try_tenant_just_before_24h_and_deletes_it_just_after():
    from apps.chat.models import ChatMessage, ChatSession
    from apps.flows.models import Flow
    from apps.tenants import try_tenant
    from apps.tenants.models import Tenant
    from apps.usage.recording import record_usage

    t0 = timezone.now()
    created = _create("shop_support", now=t0)
    standard = Tenant.objects.create_with_key(name="Keep", raw_key=secrets.token_urlsafe(32))
    library = try_tenant.ensure_library()
    session = ChatSession.objects.create(tenant_id=created.tenant_id, visitor_id="v", is_test=True)
    ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content="안녕")
    record_usage(created.tenant_id, "chat", "gpt-4o-mini", input_tokens=3, output_tokens=2)

    assert try_tenant.prune_expired(now=t0 + dt.timedelta(hours=24) - dt.timedelta(seconds=1)) == 0
    assert Tenant.objects.filter(pk=created.tenant_id).exists()

    assert try_tenant.prune_expired(now=t0 + dt.timedelta(hours=24, seconds=1)) == 1
    assert not Tenant.objects.filter(pk=created.tenant_id).exists()
    assert not Flow.objects.filter(pk=created.flow_id).exists()
    assert not ChatSession.objects.filter(tenant_id=created.tenant_id).exists()
    assert not ChatMessage.objects.filter(session_id=session.id).exists()
    from apps.usage.models import TokenUsage

    assert not TokenUsage.objects.filter(tenant_id=created.tenant_id).exists()
    assert Tenant.objects.filter(pk__in=[standard.id, library]).count() == 2  # 일반·도서관은 그대로


@pytest.mark.django_db
def test_prune_command_runs():
    from apps.tenants.models import Tenant

    created = _create(now=timezone.now() - dt.timedelta(hours=25))
    call_command("prune_try_tenants")
    assert not Tenant.objects.filter(pk=created.tenant_id).exists()


# ── 숨김: 일반 목록·관리 API·MCP·운영 집계 ────────────────────────────────────

@pytest.mark.django_db
def test_try_and_library_tenants_stay_out_of_operator_lists(client, tenant_with_key, operator_token):
    from apps.tenants import try_tenant
    from apps.usage.recording import record_usage

    tenant, _ = tenant_with_key
    created = _create()
    library = try_tenant.ensure_library()
    record_usage(created.tenant_id, "chat", "m", input_tokens=10, output_tokens=5)
    record_usage(tenant.id, "chat", "m", input_tokens=1, output_tokens=1)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {operator_token}"}

    ids = {row["id"] for row in client.get("/api/operator/tenants/", **auth).json()}
    assert str(tenant.id) in ids and str(created.tenant_id) not in ids and str(library) not in ids

    usage = client.get("/api/operator/usage/", **auth).json()
    tids = {row["tenant_id"] for row in usage["by_tenant"]}
    assert str(tenant.id) in tids and str(created.tenant_id) not in tids
    assert usage["total_tokens"] == 2


@pytest.mark.django_db
def test_memberships_never_surface_a_try_tenant():
    """멤버십 목록(로그인 후 선택·MCP list_tenants의 원천)에 체험 테넌트가 끼지 않는다."""
    from apps.tenants.members import active_memberships
    from apps.tenants.models import Tenant
    from tests.utils import make_membership

    created = _create()
    membership = make_membership(_tenant(created.tenant_id), "admin", name="누군가")
    assert all(m.tenant_id != created.tenant_id for m in active_memberships(membership.user))
    assert Tenant.objects.get(pk=created.tenant_id).memberships.count() == 1
