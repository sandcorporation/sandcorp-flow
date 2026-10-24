"""흐름 편집 공용 서비스 층(#76) — MCP 도구·흐름 API(#28)·편집기(#29)가 함께 쓰는 층.

LLM만 결정적 Fake(conftest ``fake_chat_llm``), DB·체크포인터·검증기는 실제 객체.
"""

from __future__ import annotations

import secrets

import pytest
from asgiref.sync import sync_to_async

from apps.flows import services
from apps.flows.services import Connect, DeleteNode, Disconnect, UpsertNode

adb = sync_to_async


def node(node_id: str, kind: str, n: int, config: dict | None = None) -> UpsertNode:
    return UpsertNode(op="upsert_node", id=node_id, kind=kind, code=f"N-{n:02d}", name=node_id, config=config)


def link(src: str, dst: str, handle: str = "next") -> Connect:
    return Connect(op="connect", **{"from": src, "to": dst, "handle": handle})


SIMPLE = [node("start", "start", 1), node("answer", "ai_answer", 2), link("start", "answer")]
WITH_HANDOFF = [*SIMPLE, node("human", "handoff", 3), link("answer", "human", "handoff")]


def other_tenant():
    from apps.tenants.models import Tenant

    return Tenant.objects.create_with_key(name="Other Corp", raw_key=secrets.token_urlsafe(16))


def make_flow(tenant, ops=SIMPLE, name="상담"):
    flow = services.create_flow(tenant.id, name)
    services.patch_draft(tenant.id, flow.id, list(ops))
    return flow


# ── 초안·패치 ────────────────────────────────────────────────────────────────


def test_new_flow_has_empty_draft_that_is_saved_but_invalid(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = services.create_flow(tenant.id, "빈 흐름")

    detail = services.flow_detail(tenant.id, flow.id)
    assert detail.draft.graph == {"nodes": [], "edges": []}
    assert not detail.draft.valid
    assert detail.versions == []
    assert detail.flow.deployed_version is None


def test_duplicate_name_is_a_service_error(tenant_with_key):
    tenant, _ = tenant_with_key
    services.create_flow(tenant.id, "상담")
    with pytest.raises(services.FlowServiceError) as exc:
        services.create_flow(tenant.id, "상담")
    assert exc.value.code == "duplicate_name"


def test_patch_builds_a_valid_flow(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = services.create_flow(tenant.id, "상담")

    result = services.patch_draft(tenant.id, flow.id, list(SIMPLE))

    assert result.valid, result.errors
    assert services.flow_detail(tenant.id, flow.id).draft.graph == result.graph


def test_invalid_patch_result_is_saved_with_node_errors(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)

    result = services.patch_draft(tenant.id, flow.id, [node("bad", "ai_answer", 3, {"nope": 1})])

    assert not result.valid
    by_node = {e.node_id: e.code for e in result.errors}
    assert "bad" in by_node  # 모르는 config 필드 + start에서 못 감
    assert services.flow_detail(tenant.id, flow.id).draft.graph == result.graph  # 틀려도 작업본으로 저장


def test_patch_is_atomic_when_one_op_cannot_apply(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    before = services.flow_detail(tenant.id, flow.id).draft.graph

    with pytest.raises(services.PatchError) as exc:
        services.patch_draft(tenant.id, flow.id, [node("x", "handoff", 3), DeleteNode(op="delete_node", id="ghost")])

    assert exc.value.index == 1
    assert services.flow_detail(tenant.id, flow.id).draft.graph == before


def test_new_node_needs_kind_and_code(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    with pytest.raises(services.PatchError):
        services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="x", name="이름만")])


def test_upsert_existing_node_changes_only_given_fields(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)

    result = services.patch_draft(
        tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", config={"await_reply": True})]
    )

    answer = next(n for n in result.graph["nodes"] if n["id"] == "answer")
    assert answer["kind"] == "ai_answer" and answer["code"] == "N-02"
    assert answer["config"] == {"await_reply": True}


def test_connect_replaces_the_edge_on_the_same_handle_and_delete_node_drops_its_edges(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant, [*SIMPLE, node("other", "ai_answer", 3)])

    graph = services.patch_draft(tenant.id, flow.id, [link("start", "other")]).graph
    assert graph["edges"] == [{"from": "start", "to": "other"}]

    graph = services.patch_draft(tenant.id, flow.id, [DeleteNode(op="delete_node", id="other")]).graph
    assert graph["edges"] == []


def test_disconnect_missing_edge_is_rejected(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    with pytest.raises(services.PatchError):
        services.patch_draft(tenant.id, flow.id, [Disconnect(op="disconnect", **{"from": "answer"})])
    graph = services.patch_draft(tenant.id, flow.id, [Disconnect(op="disconnect", **{"from": "start"})]).graph
    assert graph["edges"] == []


def test_save_draft_replaces_whole_graph_even_if_invalid(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    result = services.save_draft(tenant.id, flow.id, {"nodes": "broken"})
    assert not result.valid
    assert services.flow_detail(tenant.id, flow.id).draft.graph == {"nodes": "broken"}


# ── 버전·배포·되돌리기 ───────────────────────────────────────────────────────


def test_save_version_refuses_invalid_draft_with_errors(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = services.create_flow(tenant.id, "빈 흐름")
    with pytest.raises(services.FlowInvalid) as exc:
        services.save_version(tenant.id, flow.id)
    assert exc.value.errors
    assert services.flow_detail(tenant.id, flow.id).versions == []


def test_versions_are_numbered_immutable_and_deduplicated(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)

    first = services.save_version(tenant.id, flow.id)
    again = services.save_version(tenant.id, flow.id)
    assert first.created and first.version.number == 1
    assert not again.created and again.version.pk == first.version.pk

    services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", name="바뀐 이름")])
    second = services.save_version(tenant.id, flow.id)
    assert second.version.number == 2
    v1 = services.get_version(tenant.id, flow.id, 1)
    assert next(n for n in v1.graph["nodes"] if n["id"] == "answer")["name"] == "answer"  # 초안이 바뀌어도 그대로


def test_deploy_and_rollback(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    services.save_version(tenant.id, flow.id)
    services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", name="v2")])
    services.save_version(tenant.id, flow.id)

    deployed = services.deploy_version(tenant.id, flow.id, 2)
    assert deployed.deployed_version.number == 2

    rolled = services.rollback(tenant.id, flow.id)
    assert rolled.deployed_version.number == 1
    assert services.flow_detail(tenant.id, flow.id).flow.deployed_version.number == 1
    with pytest.raises(services.VersionNotFound):
        services.rollback(tenant.id, flow.id)
    with pytest.raises(services.VersionNotFound):
        services.deploy_version(tenant.id, flow.id, 9)


def test_deploy_refuses_a_version_with_nodes_lacking_handlers(tenant_with_key, monkeypatch):
    from apps.flows.executor import HANDLERS
    from apps.flows.schema import NodeKind

    monkeypatch.delitem(HANDLERS, NodeKind.HTTP_TOOL)  # 핸들러 없는 종류 흉내(이제 모든 종류에 핸들러가 있다)
    tenant, _ = tenant_with_key
    ops = [node("start", "start", 1), node("guard", "http_tool", 2, {"url": "https://example.com/x"}), link("start", "guard")]
    flow = make_flow(tenant, ops)
    services.save_version(tenant.id, flow.id)  # 스키마는 통과 — 버전 저장은 된다

    with pytest.raises(services.FlowInvalid) as exc:
        services.deploy_version(tenant.id, flow.id, 1)

    assert [(e.node_id, e.code) for e in exc.value.errors] == [("guard", "no_handler")]
    assert services.get_flow(tenant.id, flow.id).deployed_version is None


def test_other_tenants_flow_is_not_found_everywhere(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    services.save_version(tenant.id, flow.id)
    stranger = other_tenant()

    calls = [
        lambda: services.get_flow(stranger.id, flow.id),
        lambda: services.flow_detail(stranger.id, flow.id),
        lambda: services.patch_draft(stranger.id, flow.id, list(SIMPLE)),
        lambda: services.save_draft(stranger.id, flow.id, {}),
        lambda: services.save_version(stranger.id, flow.id),
        lambda: services.deploy_version(stranger.id, flow.id, 1),
        lambda: services.rollback(stranger.id, flow.id),
        lambda: services.get_flow(tenant.id, "not-a-uuid"),
    ]
    for fn in calls:
        with pytest.raises(services.FlowNotFound):
            fn()
    assert services.list_flows(stranger.id) == []


# ── 시험 한 턴 ───────────────────────────────────────────────────────────────


def _session_facts(session_id):
    from apps.chat.models import ChatMessage, ChatSession
    from apps.escalation.models import Escalation

    session = ChatSession.objects.get(pk=session_id)
    roles = [m.role for m in ChatMessage.objects.filter(session=session).order_by("created_at")]
    return session.is_test, session.is_hitl, session.flow_version_id, roles, Escalation.objects.filter(session=session).count()


@pytest.mark.django_db(transaction=True)
async def test_test_turn_runs_the_draft_in_a_test_session(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)

    result = await services.run_test_turn(tenant.id, flow.id, "영업시간이 어떻게 되나요?")

    assert result.ran and not result.errors
    assert result.reply == "안녕하세요! 무엇을 도와드릴까요?"
    assert [t["node_id"] for t in result.trace] == ["start", "answer"]
    is_test, is_hitl, pinned, roles, escalations = await adb(_session_facts)(result.session_id)
    assert is_test and pinned is None and roles == ["user", "assistant"]


@pytest.mark.django_db(transaction=True)
async def test_test_turn_handoff_is_dry_run(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant, WITH_HANDOFF)

    result = await services.run_test_turn(tenant.id, flow.id, "상담원 연결해 주세요")

    assert result.ran and result.handoff
    assert [t["node_id"] for t in result.trace] == ["start", "answer", "human"]
    _, is_hitl, _, _, escalations = await adb(_session_facts)(result.session_id)
    assert not is_hitl and escalations == 0  # 실제 넘김·알림 없음


@pytest.mark.django_db(transaction=True)
async def test_test_turn_continues_the_same_test_session(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    first = await services.run_test_turn(tenant.id, flow.id, "안녕")
    second = await services.run_test_turn(tenant.id, flow.id, "또 안녕", session_id=first.session_id)
    assert second.session_id == first.session_id
    *_, roles, _ = await adb(_session_facts)(first.session_id)
    assert roles == ["user", "assistant", "user", "assistant"]


@pytest.mark.django_db(transaction=True)
async def test_test_turn_refuses_flows_with_missing_handlers_without_a_session(tenant_with_key, fake_chat_llm, monkeypatch):
    from apps.flows.executor import HANDLERS
    from apps.flows.schema import NodeKind

    monkeypatch.delitem(HANDLERS, NodeKind.HTTP_TOOL)  # 핸들러 없는 종류 흉내(이제 모든 종류에 핸들러가 있다)
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    ops = [node("start", "start", 1), node("guard", "http_tool", 2, {"url": "https://example.com/x"}), link("start", "guard")]
    flow = await adb(make_flow)(tenant, ops)

    result = await services.run_test_turn(tenant.id, flow.id, "안녕")

    assert not result.ran and result.session_id is None
    assert [(e.node_id, e.code) for e in result.errors] == [("guard", "no_handler")]
    assert fake_chat_llm.captured == []
    assert await adb(ChatSession.objects.filter(tenant_id=tenant.id).count)() == 0


@pytest.mark.django_db(transaction=True)
async def test_test_turn_uses_a_trial_turn_and_stops_without_llm_when_none_left(tenant_with_key, fake_chat_llm, settings):
    from apps.tenants.models import Tenant

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = "sk-platform-test"
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    tenant, _ = tenant_with_key
    await adb(Tenant.objects.filter(pk=tenant.pk).update)(trial_turns_remaining=1)
    flow = await adb(make_flow)(tenant)

    used = await services.run_test_turn(tenant.id, flow.id, "안녕")
    assert used.ran
    assert (await adb(Tenant.objects.get)(pk=tenant.pk)).trial_turns_remaining == 0

    fake_chat_llm.captured.clear()
    stopped = await services.run_test_turn(tenant.id, flow.id, "또 안녕")
    assert not stopped.ran and stopped.note == services.TRIAL_EXHAUSTED_NOTE
    assert fake_chat_llm.captured == []


@pytest.mark.django_db
def test_test_sessions_stay_out_of_operating_session_lists(client, tenant_admin_token, tenant_with_key):
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="real")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="flow-test:x", is_test=True)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {tenant_admin_token}"}

    sessions = client.get("/api/tenant/sessions/", **auth).json()
    visitors = client.get("/api/tenant/visitors/", **auth).json()
    test_visitor = client.get("/api/tenant/visitors/flow-test:x/sessions/", **auth).json()

    assert [r["visitor_id"] for r in sessions] == ["real"]
    assert "flow-test:x" not in str(visitors)
    assert test_visitor == []


def test_version_note_is_stored_blank_by_default_and_capped(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = make_flow(tenant)
    first = services.save_version(tenant.id, flow.id)
    assert first.version.note == ""

    services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", name="v2")])
    second = services.save_version(tenant.id, flow.id, note="  인사말 수정  ")
    assert services.get_version(tenant.id, flow.id, 2).note == "인사말 수정"

    services.patch_draft(tenant.id, flow.id, [UpsertNode(op="upsert_node", id="answer", name="v3")])
    with pytest.raises(services.FlowServiceError) as exc:
        services.save_version(tenant.id, flow.id, note="가" * (services.VERSION_NOTE_MAX + 1))
    assert exc.value.code == "note_too_long"
    assert [v.number for v in services.flow_detail(tenant.id, flow.id).versions] == [1, 2]
    assert second.created
