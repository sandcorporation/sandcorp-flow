"""흐름별 임베드(#246, ADR-0034): 불변 embed_key, 기본 흐름, 세션 키 (tenant, visitor, flow), 공개 API ``flow=``.

- ``/chatbot/<slug>/<embed_key>/``는 그 흐름이, ``/chatbot/<slug>/``는 기본 흐름(``is_public``)이 답한다.
- 키가 없거나·임베드가 꺼졌거나·보관됐거나·미배포거나·다른 테넌트 키면 모두 같은 404다(존재 여부를 흘리지 않는다).
- 키 주소로 연 세션은 흐름의 임베드가 꺼지면 다음 메시지부터 403으로 막힌다.
LLM은 결정적 Fake(conftest), DB·체크포인터·taskiq(InMemory)는 실제 객체.
"""
import copy
import importlib
import re

import pytest
from asgiref.sync import sync_to_async
from django.apps import apps as django_apps

adb = sync_to_async


def _graph(answer_id):
    """기본 흐름에서 AI 답변 노드 id만 바꾼 것 — 어느 흐름으로 돌았는지 trace로 가린다."""
    from apps.flows.defaults import DEFAULT_FLOW

    graph = copy.deepcopy(DEFAULT_FLOW)
    for node in graph["nodes"]:
        if node["id"] == "answer":
            node["id"] = answer_id
    for edge in graph["edges"]:
        edge["from"] = answer_id if edge["from"] == "answer" else edge["from"]
        edge["to"] = answer_id if edge["to"] == "answer" else edge["to"]
    return graph


def _flow(tenant, name, *, answer_id=None, deployed=True, embed=True, public=False):
    """흐름 하나(배포·임베드 켬이 기본). 흐름을 켜는 API는 E2 몫이라 시험은 모델로 만든다."""
    from apps.flows.models import Flow, FlowDraft, FlowVersion

    graph = _graph(answer_id or f"answer_{name}")
    flow = Flow.objects.create(tenant=tenant, name=name, embed_enabled=embed, is_public=public)
    FlowDraft.objects.create(flow=flow, graph=graph)
    if deployed:
        flow.deployed_version = FlowVersion.objects.create(flow=flow, graph=graph)
        flow.save(update_fields=["deployed_version", "updated_at"])
    return flow


def _slugged(tenant, slug):
    tenant.slug = slug
    tenant.save(update_fields=["slug"])
    return tenant


def _other_tenant(slug):
    import secrets

    from apps.tenants.models import Tenant

    return _slugged(Tenant.objects.create_with_key(name=f"Other {slug}", raw_key=secrets.token_urlsafe(32)), slug)


async def _turn(session_id, message="안녕"):
    from apps.chat.chat_task import chat_task

    sent = await chat_task.kiq(str(session_id), message)
    await sent.wait_result(timeout=20)


def _turn_nodes(session_id):
    from apps.agent.graph import session_retrievals

    return [t["nodes"] for t in session_retrievals(str(session_id))]


# ── 모델 ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_new_flow_gets_immutable_embed_key_and_embed_off(tenant_with_key):
    """흐름을 만들면 8자 소문자·숫자 키가 붙고 임베드는 꺼져 있다. 저장해도 키는 바뀌지 않는다."""
    from apps.flows.models import Flow

    tenant, _ = tenant_with_key
    flows = [Flow.objects.create(tenant=tenant, name=f"흐름{i}") for i in range(5)]

    keys = [f.embed_key for f in flows]
    assert all(re.fullmatch(r"[a-z0-9]{8}", k) for k in keys)
    assert len(set(keys)) == len(keys)
    assert not any(f.embed_enabled for f in flows)

    flows[0].name = "이름 바꿈"
    flows[0].save()
    flows[0].refresh_from_db()
    assert flows[0].embed_key == keys[0]


@pytest.mark.django_db
def test_archiving_embedded_flow_turns_embed_off(tenant_with_key):
    """임베드를 켠 흐름을 보관하면 임베드도 꺼진다(경고 화면은 E2 몫)."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    flow = _flow(tenant, "상담")

    services.archive_flow(tenant.id, flow.id)

    flow.refresh_from_db()
    assert flow.archived_at is not None
    assert flow.embed_enabled is False


# ── 공개 API: resolve ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_resolve_with_key_returns_that_flow_name(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _slugged(tenant, "embed-shop")
    flow = _flow(tenant, "가격 상담")

    resp = client.get(f"/api/chat/resolve?slug=embed-shop&flow={flow.embed_key}")

    assert resp.status_code == 200
    assert resp.json()["flow_name"] == "가격 상담"


@pytest.mark.django_db
def test_resolve_without_key_returns_default_flow_name(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _slugged(tenant, "default-shop")
    _flow(tenant, "기본 상담", embed=False, public=True)
    _flow(tenant, "다른 상담")

    resp = client.get("/api/chat/resolve?slug=default-shop")

    assert resp.status_code == 200
    assert resp.json()["flow_name"] == "기본 상담"


@pytest.mark.django_db
def test_closed_keys_are_indistinguishable_404(client, tenant_with_key):
    """없는 키·임베드 꺼짐·보관·미배포·다른 테넌트 키 — resolve·stream 모두 같은 404 몸통."""
    from apps.flows import services

    tenant, _ = tenant_with_key
    _slugged(tenant, "closed-shop")
    off = _flow(tenant, "꺼짐", embed=False)
    archived = _flow(tenant, "보관")
    services.archive_flow(tenant.id, archived.id)
    undeployed = _flow(tenant, "미배포", deployed=False)
    foreign = _flow(_other_tenant("other-shop"), "남의 흐름")
    keys = ["zzzzzzzz", off.embed_key, archived.embed_key, undeployed.embed_key, foreign.embed_key]

    resolves = [client.get(f"/api/chat/resolve?slug=closed-shop&flow={k}") for k in keys]
    streams = [client.get(f"/api/chat/stream?slug=closed-shop&flow={k}&visitor_id=v-1") for k in keys]

    assert {(r.status_code, r.content) for r in resolves} == {(404, resolves[0].content)}
    assert {(r.status_code, b"".join(r.streaming_content)) for r in streams} == {(404, b"")}


@pytest.mark.django_db
def test_default_flow_key_is_open_even_with_embed_off(client, tenant_with_key):
    """기본 흐름은 토글과 상관없이 늘 열려 있다 — 자기 키 주소로도."""
    tenant, _ = tenant_with_key
    _slugged(tenant, "default-key")
    default = _flow(tenant, "기본", embed=False, public=True)

    assert client.get(f"/api/chat/resolve?slug=default-key&flow={default.embed_key}").status_code == 200


# ── 세션 키 (tenant, visitor, flow) ───────────────────────────────────────────

@pytest.mark.django_db
def test_same_visitor_gets_separate_session_per_flow(client, tenant_with_key):
    """같은 방문자라도 흐름이 다르면 세션이 따로고, 같은 흐름으로 다시 오면 같은 세션을 잇는다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    _slugged(tenant, "multi-shop")
    default = _flow(tenant, "기본", embed=False, public=True)
    a = _flow(tenant, "A")
    b = _flow(tenant, "B")

    def sid(query=""):
        return client.get(f"/api/chat/stream?slug=multi-shop&visitor_id=v-same{query}")["X-Session-Id"]

    s_default, s_a, s_b = sid(), sid(f"&flow={a.embed_key}"), sid(f"&flow={b.embed_key}")
    assert len({s_default, s_a, s_b}) == 3
    assert sid(f"&flow={a.embed_key}") == s_a and sid() == s_default

    pinned = {str(s.id): (s.flow_id, s.flow_version_id) for s in ChatSession.objects.filter(visitor_id="v-same")}
    assert pinned[s_default] == (default.id, default.deployed_version_id)
    assert pinned[s_a] == (a.id, a.deployed_version_id)
    assert pinned[s_b] == (b.id, b.deployed_version_id)


@pytest.mark.django_db(transaction=True)
async def test_key_address_answers_with_that_flow_and_base_with_default(client, tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    await adb(_slugged)(tenant, "answer-shop")
    await adb(_flow)(tenant, "기본", answer_id="answer_default", embed=False, public=True)
    keyed = await adb(_flow)(tenant, "키 흐름", answer_id="answer_keyed")

    base_sid = (await adb(client.get)("/api/chat/stream?slug=answer-shop&visitor_id=v-ans"))["X-Session-Id"]
    key_sid = (await adb(client.get)(
        f"/api/chat/stream?slug=answer-shop&visitor_id=v-ans&flow={keyed.embed_key}"))["X-Session-Id"]
    await _turn(base_sid)
    await _turn(key_sid)

    assert "answer_default" in (await adb(_turn_nodes)(base_sid))[-1]
    assert "answer_keyed" in (await adb(_turn_nodes)(key_sid))[-1]


@pytest.mark.django_db(transaction=True)
async def test_changing_default_flow_applies_to_new_sessions_only(client, tenant_with_key, fake_chat_llm):
    """기본 흐름을 바꾸면 기존 주소의 새 세션부터 새 흐름이고, 열린 세션은 고정된 버전으로 계속 답한다(막히지 않는다)."""
    from apps.flows.models import Flow

    tenant, _ = tenant_with_key
    await adb(_slugged)(tenant, "switch-shop")
    old = await adb(_flow)(tenant, "옛 기본", answer_id="answer_old", embed=False, public=True)
    new = await adb(_flow)(tenant, "새 기본", answer_id="answer_new", embed=False)
    open_sid = (await adb(client.get)("/api/chat/stream?slug=switch-shop&visitor_id=v-open"))["X-Session-Id"]

    def switch():  # 「기본 흐름 바꾸기」 API는 E2 몫 — 모델로 바꾼다
        Flow.objects.filter(pk=old.pk).update(is_public=False)
        Flow.objects.filter(pk=new.pk).update(is_public=True)

    await adb(switch)()
    resp = await adb(client.post)(
        "/api/chat/message", {"session_id": open_sid, "content": "계속"}, content_type="application/json")
    assert resp.status_code == 202
    await _turn(open_sid)
    fresh_sid = (await adb(client.get)("/api/chat/stream?slug=switch-shop&visitor_id=v-fresh"))["X-Session-Id"]
    await _turn(fresh_sid)

    assert all("answer_old" in t for t in await adb(_turn_nodes)(open_sid))
    assert "answer_new" in (await adb(_turn_nodes)(fresh_sid))[-1]


# ── message 막기 ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize("close", ["embed_off", "archive"])
def test_message_blocked_after_key_flow_closes(client, tenant_with_key, close):
    """키 주소로 연 세션은 흐름의 임베드가 꺼지거나 보관되면 다음 메시지부터 403으로 막힌다(저장도 하지 않는다)."""
    from apps.chat.models import ChatMessage
    from apps.flows import services
    from apps.flows.models import Flow

    tenant, _ = tenant_with_key
    _slugged(tenant, "block-shop")
    flow = _flow(tenant, "막힐 흐름")
    sid = client.get(f"/api/chat/stream?slug=block-shop&visitor_id=v-blk&flow={flow.embed_key}")["X-Session-Id"]

    def post():
        return client.post("/api/chat/message", {"session_id": sid, "content": "안녕"}, content_type="application/json")

    if close == "embed_off":
        Flow.objects.filter(pk=flow.pk).update(embed_enabled=False)
    else:
        services.archive_flow(tenant.id, flow.id)
    resp = post()

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Flow not open"}
    assert not ChatMessage.objects.filter(session_id=sid).exists()


# ── 마이그레이션 ──────────────────────────────────────────────────────────────

FLOW_MIGRATION = "apps.flows.migrations.0007_flow_embed_key"
SESSION_MIGRATION = "apps.chat.migrations.0004_chatsession_flow"


@pytest.mark.django_db
def test_migration_issues_keys_to_existing_flows(tenant_with_key):
    """키가 빈 기존 흐름에 테넌트 안에서 겹치지 않는 키를 발급하고, 이미 있는 키는 그대로 둔다(멱등)."""
    from apps.flows.models import Flow

    tenant, _ = tenant_with_key
    other = _other_tenant("mig-other")
    blank = [Flow.objects.create(tenant=t, name="옛 흐름") for t in (tenant, other)]
    keep = Flow.objects.create(tenant=tenant, name="키 있는 흐름")
    Flow.objects.filter(pk__in=[f.pk for f in blank]).update(embed_key="")

    migration = importlib.import_module(FLOW_MIGRATION)
    migration.issue_embed_keys(django_apps, None)
    migration.issue_embed_keys(django_apps, None)

    keys = dict(Flow.objects.values_list("pk", "embed_key"))
    assert all(re.fullmatch(r"[a-z0-9]{8}", keys[f.pk]) for f in blank)
    assert keys[keep.pk] == keep.embed_key
    assert keys[blank[0].pk] != keep.embed_key


@pytest.mark.django_db
def test_migration_fills_session_flow_from_pinned_version(tenant_with_key):
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    flow = _flow(tenant, "기본", public=True)
    pinned = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-old")
    unpinned = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-test", is_test=True)
    ChatSession.objects.filter(pk__in=[pinned.pk, unpinned.pk]).update(flow=None)

    importlib.import_module(SESSION_MIGRATION).fill_session_flow(django_apps, None)

    pinned.refresh_from_db()
    unpinned.refresh_from_db()
    assert pinned.flow_id == flow.id
    assert unpinned.flow_id is None
