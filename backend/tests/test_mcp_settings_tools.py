"""MCP 설정 쓰기·흐름 복원·토글 도구(#212, 우산 #207).

- ``update_settings``: 권한 비트 없음(Member도), 허용 필드만(``extra="forbid"``), HTTP ``PATCH /config/``와 같은 검증, 차이 응답.
- ``restore_flow``(flow.write)·``set_deploy_approval``·``set_public_flow_view``(flow.admin): 2단계 — 토큰 없이는 부작용 0.
"""

from __future__ import annotations

import json

import pytest

from apps.flows import services
from apps.mcp.models import McpAuditEvent
from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — 픽스처
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    post,
    rpc,
    signer,
    tool_text,
)
from authkit_fakes import Signer

ADMIN_SCOPES = "flow.read flow.write flow.admin"


def ok(body: dict) -> dict:
    assert not body["result"].get("isError"), tool_text(body)
    return body["result"]["structuredContent"]


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def tenant_as(role: str, slug: str = "acme"):
    tenant = make_tenant(slug)
    member_of(tenant, role)
    return tenant


def config_of(tenant):
    tenant.config.refresh_from_db()
    return tenant.config


# ── update_settings ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_member_can_update_settings_and_gets_a_diff_of_changed_fields_only(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    tenant.config.brand_name = "옛 이름"
    tenant.config.welcome_message = "안녕하세요"
    tenant.config.save()

    out = ok(call(mcp_token(signer), "update_settings", {
        "brand_name": "새 이름", "welcome_message": "안녕하세요", "require_identity_verification": True,
    }))

    assert out == {"changed": {
        "brand_name": {"before": "옛 이름", "after": "새 이름"},
        "require_identity_verification": {"before": False, "after": True},
    }}
    config = config_of(tenant)
    assert config.brand_name == "새 이름" and config.require_identity_verification is True


@pytest.mark.django_db
def test_unspecified_fields_stay_and_identical_call_changes_nothing(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    token = mcp_token(signer)
    ok(call(token, "update_settings", {"system_prompt": "친절하게", "hitl_schedule": {"mon": {"enabled": True, "start": "09:00", "end": "18:00"}}, "hitl_holidays": ["2026-12-25"]}))

    again = ok(call(token, "update_settings", {"system_prompt": "친절하게", "hitl_schedule": {"mon": {"enabled": True, "start": "09:00", "end": "18:00"}}}))

    assert again == {"changed": {}}
    assert call(token, "update_settings", {})["result"]["isError"] is False
    config = config_of(tenant)
    assert config.system_prompt == "친절하게" and config.hitl_holidays == ["2026-12-25"]


@pytest.mark.django_db
def test_empty_string_clears_a_text_field_and_reports_before_after(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    tenant.config.welcome_message = "안녕하세요"
    tenant.config.save()

    out = ok(call(mcp_token(signer), "update_settings", {"welcome_message": ""}))

    assert out == {"changed": {"welcome_message": {"before": "안녕하세요", "after": ""}}}
    assert config_of(tenant).welcome_message == ""


@pytest.mark.django_db
def test_omitted_or_null_text_field_is_left_alone(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    tenant.config.welcome_message = "안녕하세요"
    tenant.config.save()
    token = mcp_token(signer)

    assert ok(call(token, "update_settings", {"brand_name": "x"}))["changed"].keys() == {"brand_name"}
    assert ok(call(token, "update_settings", {"welcome_message": None})) == {"changed": {}}
    assert config_of(tenant).welcome_message == "안녕하세요"


@pytest.mark.django_db
def test_update_settings_description_tells_how_to_clear_a_text_field(signer: Signer) -> None:
    tenant_as(Membership.ROLE_ADMIN)
    tools = post(rpc("tools/list"), mcp_token(signer)).json()["result"]["tools"]
    description = next(t["description"] for t in tools if t["name"] == "update_settings")
    assert '빈 문자열 ""' in description and "인자를 빼면 그대로" in description


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["llm_api_key", "webhook_url", "slug", "llm_base_url", "deploy_requires_approval", "public_flow_view", "model_id", "tenant_key"])
def test_fields_outside_the_allow_list_are_invalid_arguments(signer: Signer, field: str) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    before = (tenant.slug, tenant.config.webhook_url, tenant.config.llm_api_key, tenant.config.deploy_requires_approval)

    assert "invalid_arguments" in err(call(mcp_token(signer), "update_settings", {field: "x", "brand_name": "바뀌면 안 됨"}))

    config = config_of(tenant)
    assert (tenant.slug, config.webhook_url, config.llm_api_key, config.deploy_requires_approval) == before
    assert config.brand_name != "바뀌면 안 됨"  # 한 칸이라도 틀리면 아무것도 안 바뀐다


@pytest.mark.django_db
def test_topic_scope_rule_matches_the_http_patch(signer: Signer) -> None:
    """범위 설명 없이 주제범위를 켜는 검증 — HTTP와 같은 규칙·같은 문구(서비스 함수 하나)."""
    tenant = tenant_as(Membership.ROLE_ADMIN)

    mcp_message = err(call(mcp_token(signer), "update_settings", {"topic_scope_enabled": True}))

    assert "주제범위 제어를 켜려면 응대 범위 설명을 입력해야 합니다." in mcp_message
    assert config_of(tenant).topic_scope_enabled is False
    ok(call(mcp_token(signer), "update_settings", {"topic_scope_enabled": True, "scope_description": "요금 문의만"}))
    assert config_of(tenant).topic_scope_enabled is True


@pytest.mark.django_db
def test_settings_survive_a_get_settings_round_trip(signer: Signer) -> None:
    tenant_as(Membership.ROLE_MEMBER)
    token = mcp_token(signer)
    ok(call(token, "update_settings", {"hitl_timezone": "Asia/Seoul", "scope_refusal_message": "범위 밖이에요", "require_identity_verification": True}))

    view = ok(call(token, "get_settings"))

    assert view["hitl_timezone"] == "Asia/Seoul" and view["require_identity_verification"] is True
    assert view["scope_refusal_message"] == "범위 밖이에요"


@pytest.mark.django_db
def test_update_settings_needs_flow_write_and_is_audited(signer: Signer) -> None:
    tenant_as(Membership.ROLE_MEMBER)

    response = post(rpc("tools/call", {"name": "update_settings", "arguments": {"brand_name": "x"}}), mcp_token(signer, scope="flow.read"))
    assert response.status_code == 403

    ok(call(mcp_token(signer), "update_settings", {"brand_name": "감사"}))
    event = McpAuditEvent.objects.filter(tool="update_settings", result_code="ok").get()
    assert event.arguments["brand_name"] == "감사" and event.confirm_stage is None


# ── restore_flow ─────────────────────────────────────────────────────────────


def archived_flow(tenant, name: str = "옛 상담"):
    flow = services.create_flow(tenant.id, name)
    services.archive_flow(tenant.id, flow.id)
    return services.Flow.objects.get(pk=flow.pk) if hasattr(services, "Flow") else flow


@pytest.mark.django_db
def test_restore_flow_is_two_step_and_brings_the_flow_back(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    flow = archived_flow(tenant)
    token = mcp_token(signer)

    first = ok(call(token, "restore_flow", {"flow_id": str(flow.id)}))

    assert first["confirm_required"] is True
    assert first["preview"]["name"] == "옛 상담" and first["preview"]["archived_at"]
    assert services.Flow.objects.get(pk=flow.pk).archived_at is not None  # 부작용 0

    out = ok(call(token, "restore_flow", {"flow_id": str(flow.id), "confirm_token": first["confirm_token"]}))

    assert out["restored"] is True and out["id"] == str(flow.id) and out["name"] == "옛 상담"
    assert services.Flow.objects.get(pk=flow.pk).archived_at is None
    stages = list(McpAuditEvent.objects.filter(tool="restore_flow").order_by("id").values_list("confirm_stage", flat=True))
    assert stages == ["preview", "confirm"]


@pytest.mark.django_db
def test_restore_flow_unknown_flow_and_name_clash_and_member(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    token = mcp_token(signer)
    assert "flow_not_found" in err(call(token, "restore_flow", {"flow_id": "00000000-0000-0000-0000-000000000000"}))

    flow = archived_flow(tenant, "중복")
    services.create_flow(tenant.id, "중복")
    preview = ok(call(token, "restore_flow", {"flow_id": str(flow.id)}))
    assert "duplicate_name" in err(call(token, "restore_flow", {"flow_id": str(flow.id), "confirm_token": preview["confirm_token"]}))


@pytest.mark.django_db
def test_member_cannot_restore(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)
    flow = archived_flow(tenant)

    assert "admin_required" in err(call(mcp_token(signer), "restore_flow", {"flow_id": str(flow.id)}))
    assert services.Flow.objects.get(pk=flow.pk).archived_at is not None


# ── 토글 두 개 ───────────────────────────────────────────────────────────────

TOGGLES = [
    ("set_deploy_approval", "deploy_requires_approval"),
    ("set_public_flow_view", "public_flow_view"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("tool", "field"), TOGGLES)
def test_toggle_is_two_step_and_sets_the_value(signer: Signer, tool: str, field: str) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    token = mcp_token(signer, scope=ADMIN_SCOPES)
    assert getattr(config_of(tenant), field) is False

    first = ok(call(token, tool, {"enabled": True}))

    assert first["confirm_required"] is True
    assert first["preview"]["current"] is False and first["preview"]["new"] is True
    assert first["preview"]["effect"] and "no_change" not in first["preview"]
    assert getattr(config_of(tenant), field) is False  # 토큰 없이는 부작용 0

    out = ok(call(token, tool, {"enabled": True, "confirm_token": first["confirm_token"]}))

    assert out == {"changed": {field: {"before": False, "after": True}}}
    assert getattr(config_of(tenant), field) is True

    again = ok(call(token, tool, {"enabled": True}))
    assert again["preview"]["no_change"] is True and again["preview"]["current"] is True


@pytest.mark.django_db
def test_preview_effect_lines_name_the_consequence(signer: Signer) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)
    token = mcp_token(signer, scope=ADMIN_SCOPES)

    public = ok(call(token, "set_public_flow_view", {"enabled": True}))["preview"]["effect"]
    assert "익명" in public and "/flows/acme" in public

    tenant.config.deploy_requires_approval = True
    tenant.config.save()
    approval = ok(call(token, "set_deploy_approval", {"enabled": False}))["preview"]["effect"]
    assert "Admin 배포가 곧바로 반영" in approval


@pytest.mark.django_db
@pytest.mark.parametrize(("tool", "field"), TOGGLES)
def test_member_cannot_toggle(signer: Signer, tool: str, field: str) -> None:
    tenant = tenant_as(Membership.ROLE_MEMBER)

    assert "admin_required" in err(call(mcp_token(signer, scope=ADMIN_SCOPES), tool, {"enabled": True}))
    assert getattr(config_of(tenant), field) is False


@pytest.mark.django_db
@pytest.mark.parametrize(("tool", "field"), TOGGLES)
def test_flow_write_only_token_is_403_asking_for_flow_admin(signer: Signer, tool: str, field: str) -> None:
    tenant = tenant_as(Membership.ROLE_ADMIN)

    response = post(rpc("tools/call", {"name": tool, "arguments": {"enabled": True}}), mcp_token(signer, scope="flow.read flow.write"))

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="flow.admin"'
    assert getattr(config_of(tenant), field) is False


@pytest.mark.django_db
def test_toggle_requires_a_boolean(signer: Signer) -> None:
    tenant_as(Membership.ROLE_ADMIN)

    assert "invalid_arguments" in err(call(mcp_token(signer, scope=ADMIN_SCOPES), "set_public_flow_view", {}))
    assert "invalid_arguments" in err(call(mcp_token(signer, scope=ADMIN_SCOPES), "set_public_flow_view", {"enabled": "maybe"}))


@pytest.mark.django_db
def test_toggle_audit_records_both_stages_without_the_token(signer: Signer) -> None:
    tenant_as(Membership.ROLE_ADMIN)
    token = mcp_token(signer, scope=ADMIN_SCOPES)
    first = ok(call(token, "set_deploy_approval", {"enabled": True}))
    ok(call(token, "set_deploy_approval", {"enabled": True, "confirm_token": first["confirm_token"]}))

    events = list(McpAuditEvent.objects.filter(tool="set_deploy_approval").order_by("id"))

    assert [e.confirm_stage for e in events] == ["preview", "confirm"]
    assert all(e.result_code == "ok" for e in events)
    assert first["confirm_token"] not in json.dumps([e.arguments for e in events])
