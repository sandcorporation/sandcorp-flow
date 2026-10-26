"""MCP 읽기 도구 확대(#211) — 설정·팀·초대·비밀 이름·허용 도메인·사용량·배포 요청·판단 기록·방문자.

외부 경계는 JWKS·시계만 대역이다. DB·체크포인터는 실제 객체다.
핵심은 **비밀 유출 회귀 시험** — 심어 둔 키·주소·토큰이 어떤 읽기 도구 응답에도 없어야 한다.
"""

from __future__ import annotations

import json
import secrets as pysecrets
from datetime import date

import pytest

from apps.chat.models import ChatSession
from apps.flows.models import DecisionTrace
from apps.mcp import registry
from apps.mcp.models import McpAuditEvent
from apps.memory.models import VisitorMemory
from apps.tenants import tenant_secrets
from apps.tenants.members import create_invite
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import AGENTS_MANAGE, FLOW_APPROVE
from apps.usage.models import TokenUsage
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator는 픽스처
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    signer,
    tool_text,
)
from resource_token_fakes import Signer

SIMPLE_OPS = [
    {"op": "upsert_node", "id": "start", "kind": "start", "code": "N-01"},
    {"op": "upsert_node", "id": "answer", "kind": "ai_answer", "code": "N-02"},
    {"op": "connect", "from": "start", "to": "answer"},
]
NEW_TOOLS = [
    "get_settings", "list_members", "list_invites", "list_secrets", "list_allowed_domains", "get_usage",
    "list_deploy_requests", "get_session_trace", "list_visitors", "get_visitor_memory",
]
# 기존 읽기 도구(인자 없이 불러도 되는 것) — 유출 회귀 시험은 이것까지 훑는다.
OLD_TOOLS = ["list_tenants", "list_flows", "get_flow_schema", "list_documents", "list_sessions"]

# 심어 두는 값. 어느 도구 응답에도 나오면 안 된다.
LLM_KEY = "sk-LEAK-llm-key-0001"
EMBED_KEY = "sk-LEAK-embed-key-0002"
OCR_KEY = "sk-LEAK-ocr-key-0003"
LLM_BASE = "https://llm-leak.internal.example/v1"
EMBED_BASE = "https://embed-leak.internal.example/v1"
OCR_BASE = "https://ocr-leak.internal.example/v1"
WEBHOOK = "https://hooks.leak.example/services/T000/B000/XXXXLEAK"
SECRET_VALUE = "super-secret-value-LEAK-0004"
TENANT_KEY = "tenant-key-LEAK-" + pysecrets.token_hex(8)
MASK = "********"


def ok(body: dict) -> dict:
    result = body["result"]
    assert not result.get("isError"), tool_text(body)
    return result["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def team(slug: str = "acme", *, role: str = Membership.ROLE_ADMIN, mine: bool = True) -> Tenant:
    tenant = Tenant.objects.create_with_key(name=f"{slug} corp", raw_key=TENANT_KEY if slug == "acme" else pysecrets.token_hex(8))
    tenant.slug = slug
    tenant.save(update_fields=["slug"])
    if mine:
        member_of(tenant, role)
    return tenant


def plant_secrets(tenant: Tenant) -> str:
    """키·주소·비밀·초대를 심는다 → 초대 원문 토큰."""
    config = tenant.config
    config.llm_provider_type, config.llm_base_url, config.llm_api_key = "openai", LLM_BASE, LLM_KEY
    config.embed_provider_type, config.embed_base_url, config.embed_api_key = "openai", EMBED_BASE, EMBED_KEY
    config.ocr_provider_type, config.ocr_base_url, config.ocr_api_key = "openai", OCR_BASE, OCR_KEY
    config.webhook_url, config.webhook_type = WEBHOOK, "slack"
    config.save()
    tenant_secrets.set_secret(tenant.id, "CRM_TOKEN", SECRET_VALUE)
    _, raw_invite = create_invite(tenant, Membership.ROLE_MEMBER)
    return raw_invite


# ── 등록 ──────────────────────────────────────────────────────────────────────


def test_new_tools_are_read_only_flow_read_with_endpoint_permissions() -> None:
    expected = {"list_invites": AGENTS_MANAGE, "list_deploy_requests": FLOW_APPROVE}
    for name in NEW_TOOLS:
        tool = registry.get_tool(name)
        assert tool is not None and not tool.writes and tool.scope == "flow.read" and tool.idempotent, name
        assert tool.permission == expected.get(name), name
        assert tool.annotations() == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}


# ── get_settings ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_settings_gives_plain_settings_and_flags_only(signer: Signer) -> None:
    tenant = team()
    plant_secrets(tenant)
    config = tenant.config
    config.system_prompt, config.model_id, config.embed_model, config.ocr_model = "친절하게", "gpt-x", "emb-x", "ocr-x"
    config.deploy_requires_approval, config.hitl_enabled = True, True
    config.save()

    got = ok(call(mcp_token(signer, scope="flow.read"), "get_settings"))

    assert got["system_prompt"] == "친절하게" and got["deploy_requires_approval"] is True and got["hitl_enabled"] is True
    assert got["slug"] == "acme"
    assert got["providers"]["llm"] == {
        "provider_type": "openai", "model": "gpt-x", "extraction_model": config.extraction_model, "api_key_set": True
    }
    assert got["providers"]["embed"]["model"] == "emb-x" and got["providers"]["embed"]["api_key_set"] is True
    assert got["providers"]["ocr"] == {"provider_type": "openai", "model": "ocr-x", "api_key_set": True}
    assert got["webhook"] == {"webhook_set": True, "webhook_type": "slack"}


@pytest.mark.django_db
def test_get_settings_reports_unset_keys_and_webhook_as_false(signer: Signer) -> None:
    team()

    got = ok(call(mcp_token(signer, scope="flow.read"), "get_settings"))

    assert [got["providers"][k]["api_key_set"] for k in ("llm", "embed", "ocr")] == [False, False, False]
    assert got["webhook"]["webhook_set"] is False


@pytest.mark.django_db
def test_get_settings_matches_http_config_on_every_plain_field(signer: Signer) -> None:
    """같은 값에서 나온다 — HTTP 설정 응답의 비비밀 칸이 그대로 있다."""
    from apps.tenants.api import _config_out

    tenant = team()
    plant_secrets(tenant)
    http = _config_out(tenant.config)

    got = ok(call(mcp_token(signer, scope="flow.read"), "get_settings"))

    for key in ("system_prompt", "welcome_message", "brand_name", "hitl_timezone", "scope_description", "public_flow_view"):
        assert got[key] == http[key], key


# ── list_members / list_invites ──────────────────────────────────────────────


@pytest.mark.django_db
def test_list_members_gives_name_email_role_active_without_other_ids(signer: Signer) -> None:
    tenant = team(role=Membership.ROLE_MEMBER)
    other = member_of(tenant, Membership.ROLE_ADMIN, sub="sub-other-admin", active=False)
    other.user.name, other.user.email = "박관리", "admin@acme.test"
    other.user.save(update_fields=["name", "email"])

    got = ok(call(mcp_token(signer, scope="flow.read"), "list_members"))

    assert [(m["role"], m["is_active"]) for m in got["members"]] == [("member", True), ("admin", False)]
    row = got["members"][1]
    assert set(row) == {"id", "name", "email", "role", "is_active"} and row["email"] == "admin@acme.test"
    assert got["next_cursor"] is None


@pytest.mark.django_db
def test_list_members_pages_with_cursor(signer: Signer) -> None:
    tenant = team()
    for i in range(3):
        member_of(tenant, Membership.ROLE_MEMBER, sub=f"sub-{i}")
    token = mcp_token(signer, scope="flow.read")

    first = ok(call(token, "list_members", {"limit": 2}))
    second = ok(call(token, "list_members", {"limit": 2, "cursor": first["next_cursor"]}))

    assert len(first["members"]) == 2 and len(second["members"]) == 2 and second["next_cursor"] is None
    assert not {m["id"] for m in first["members"]} & {m["id"] for m in second["members"]}
    assert "invalid_cursor" in err(call(token, "list_members", {"cursor": "!!"}))


@pytest.mark.django_db
def test_list_invites_has_no_token_or_url(signer: Signer) -> None:
    tenant = team()
    raw = plant_secrets(tenant)

    got = ok(call(mcp_token(signer, scope="flow.read"), "list_invites"))

    [invite] = got["invites"]
    assert set(invite) == {"id", "role", "expires_at", "created_at"} and invite["role"] == "member"
    assert raw not in json.dumps(got)


@pytest.mark.django_db
def test_member_cannot_list_invites_or_deploy_requests(signer: Signer) -> None:
    team(role=Membership.ROLE_MEMBER)
    token = mcp_token(signer, scope="flow.read")

    assert "admin_required" in err(call(token, "list_invites"))
    assert "admin_required" in err(call(token, "list_deploy_requests"))


# ── 비밀 이름·허용 도메인 ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_secrets_names_only_and_domains(signer: Signer) -> None:
    tenant = team(role=Membership.ROLE_MEMBER)
    plant_secrets(tenant)
    tenant_secrets.add_domain(tenant.id, "api.crm.example.com")
    token = mcp_token(signer, scope="flow.read")

    secrets = ok(call(token, "list_secrets"))
    domains = ok(call(token, "list_allowed_domains"))

    [row] = secrets["secrets"]
    assert set(row) == {"name", "created_at", "updated_at"} and row["name"] == "CRM_TOKEN"
    assert [d["pattern"] for d in domains["domains"]] == ["api.crm.example.com"]


# ── get_usage ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_usage_matches_http_report_and_is_tenant_scoped(signer: Signer) -> None:
    from apps.usage.api import tenant_usage_report

    tenant = team()
    other = team("other", mine=False)
    row = dict(call_type="chat", model="gpt-x", date=date.today(), input_tokens=10, output_tokens=5, total_tokens=15, request_count=1)
    TokenUsage.objects.create(tenant_id=tenant.id, **row)
    TokenUsage.objects.create(tenant_id=other.id, **{**row, "total_tokens": 9999})

    got = ok(call(mcp_token(signer, scope="flow.read"), "get_usage", {"days": 7}))

    assert got["total_tokens"] == 15
    assert got == json.loads(json.dumps(tenant_usage_report(tenant.id, 7)))
    assert [b["call_type"] for b in got["by_call_type"]] == ["chat"]


# ── list_deploy_requests ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_list_deploy_requests_admin_sees_requests_filtered_by_status(signer: Signer) -> None:
    from apps.flows import services
    tenant = team()
    config = tenant.config
    config.deploy_requires_approval = True
    config.save()
    token = mcp_token(signer)
    flow_id = ok(call(token, "create_flow", {"name": "상담"}))["id"]
    ok(call(token, "update_flow", {"flow_id": flow_id, "ops": SIMPLE_OPS}))
    ok(call(token, "save_version", {"flow_id": flow_id}))
    services.submit_deploy(tenant.id, flow_id, 1)

    got = ok(call(token, "list_deploy_requests"))
    none = ok(call(token, "list_deploy_requests", {"status": "approved"}))

    [req] = got["deploy_requests"]
    assert (req["flow_name"], req["version"], req["status"]) == ("상담", 1, "pending")
    assert none["deploy_requests"] == []


# ── get_session_trace ────────────────────────────────────────────────────────


def trace(tenant: Tenant, session: ChatSession, tokens: int) -> None:
    DecisionTrace.objects.create(
        tenant_id=tenant.id, session=session, entries=[{"node_id": "answer", "kind": "ai_answer"}], input_tokens=tokens
    )


@pytest.mark.django_db
def test_get_session_trace_gives_turns_and_pages(signer: Signer) -> None:
    tenant = team(role=Membership.ROLE_MEMBER)
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")
    for t in (1, 2, 3):
        trace(tenant, session, t)
    token = mcp_token(signer, scope="flow.read")

    first = ok(call(token, "get_session_trace", {"session_id": str(session.id), "limit": 2}))
    second = ok(call(token, "get_session_trace", {"session_id": str(session.id), "limit": 2, "cursor": first["next_cursor"]}))

    assert [t["turn"] for t in first["decision_traces"]] == [1, 2] and first["decision_traces"][0]["entries"]
    assert [t["turn"] for t in second["decision_traces"]] == [3] and second["next_cursor"] is None
    assert first["retrievals"] == []  # 체크포인트가 없는 세션


@pytest.mark.django_db(transaction=True)
async def test_get_session_trace_carries_retrievals_of_a_real_turn(signer: Signer) -> None:
    """체크포인트 히스토리에서 복원한 검색 근거가 JSON으로 나온다(HTTP retrievals와 같은 정보)."""
    from asgiref.sync import sync_to_async

    from apps.flows.chat_turn import run_chat_turn_async

    tenant = await sync_to_async(team)(role=Membership.ROLE_MEMBER)
    session = await sync_to_async(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-real")
    await run_chat_turn_async(session, "안녕하세요")

    body = await sync_to_async(call)(mcp_token(signer, scope="flow.read"), "get_session_trace", {"session_id": str(session.id)})

    got = body["result"]["structuredContent"]
    assert any(t["user_message"] == "안녕하세요" and isinstance(t["nodes"], list) for t in got["retrievals"])
    assert got["decision_traces"], "턴 하나가 판단 기록을 남긴다"


@pytest.mark.django_db
def test_get_session_trace_hides_other_tenants_and_bad_ids(signer: Signer) -> None:
    team()
    other = team("other", mine=False)
    theirs = ChatSession.objects.create(tenant_id=other.id, visitor_id="v")
    token = mcp_token(signer, scope="flow.read")

    assert "session_not_found" in err(call(token, "get_session_trace", {"session_id": str(theirs.id)}))
    assert "session_not_found" in err(call(token, "get_session_trace", {"session_id": "nope"}))


# ── 방문자 ───────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_visitors_and_memory_are_tenant_scoped_without_test_sessions(signer: Signer) -> None:
    tenant = team(role=Membership.ROLE_MEMBER)
    other = team("other", mine=False)
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="alice")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="alice")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="bob")
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="try-only", is_test=True)
    ChatSession.objects.create(tenant_id=other.id, visitor_id="eve")
    VisitorMemory.objects.create(tenant_id=tenant.id, visitor_id="alice", key="이름", value="앨리스")
    VisitorMemory.objects.create(tenant_id=other.id, visitor_id="alice", key="남의", value="남의 기억")
    token = mcp_token(signer, scope="flow.read")

    visitors = ok(call(token, "list_visitors"))
    found = ok(call(token, "list_visitors", {"search": "ALI"}))
    memory = ok(call(token, "get_visitor_memory", {"visitor_id": "alice"}))
    nobody = ok(call(token, "get_visitor_memory", {"visitor_id": "eve"}))

    assert visitors["visitor_ids"] == ["alice", "bob"] and found["visitor_ids"] == ["alice"]
    assert [(m["key"], m["value"]) for m in memory["memories"]] == [("이름", "앨리스")]
    assert nobody["memories"] == []


# ── 비밀 유출 회귀 ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_no_read_tool_response_contains_any_planted_secret(signer: Signer) -> None:
    """키·가림 문자열·base_url·웹훅 주소·비밀 값·TENANT_KEY·초대 토큰을 심고, 새 도구와 기존 읽기 도구를 모두 훑는다."""
    tenant = team()
    raw_invite = plant_secrets(tenant)
    tenant_secrets.add_domain(tenant.id, "api.crm.example.com")
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")
    trace(tenant, session, 1)
    VisitorMemory.objects.create(tenant_id=tenant.id, visitor_id="v1", key="k", value="v")
    token = mcp_token(signer, scope="flow.read")
    args = {"get_session_trace": {"session_id": str(session.id)}, "get_visitor_memory": {"visitor_id": "v1"}}
    planted = [
        LLM_KEY, EMBED_KEY, OCR_KEY, LLM_BASE, EMBED_BASE, OCR_BASE, WEBHOOK, SECRET_VALUE, TENANT_KEY, raw_invite, MASK,
        tenant.tenant_key_hash,
        "llm-leak.internal", "hooks.leak.example",
    ]

    for tool in [*NEW_TOOLS, *OLD_TOOLS]:
        body = call(token, tool, args.get(tool))
        assert not body["result"].get("isError"), (tool, tool_text(body))
        text = json.dumps(body, ensure_ascii=False)
        for value in planted:
            assert value not in text, (tool, value)
            assert value not in tool_text(body), (tool, value)


# ── 감사·테넌트 경계 ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_new_read_tools_are_not_audited(signer: Signer) -> None:
    tenant = team()
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")
    token = mcp_token(signer, scope="flow.read")
    args = {"get_session_trace": {"session_id": str(session.id)}, "get_visitor_memory": {"visitor_id": "v1"}}

    for tool in NEW_TOOLS:
        assert not call(token, tool, args.get(tool))["result"]["isError"], tool

    assert McpAuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_other_tenants_rows_never_show(signer: Signer) -> None:
    team()
    other = team("other", mine=False)
    plant_secrets(other)
    tenant_secrets.add_domain(other.id, "theirs.example.com")
    member_of(other, Membership.ROLE_MEMBER, sub="sub-theirs")  # 남의 팀원
    token = mcp_token(signer, scope="flow.read")

    assert ok(call(token, "list_secrets"))["secrets"] == []
    assert ok(call(token, "list_allowed_domains"))["domains"] == []
    assert ok(call(token, "list_invites"))["invites"] == []
    assert len(ok(call(token, "list_members"))["members"]) == 1
    assert ok(call(token, "get_settings"))["webhook"]["webhook_set"] is False
