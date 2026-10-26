"""사람 넘김 상담 시간(hitl_timezone·hitl_schedule·hitl_holidays) 저장 검증 — HTTP·MCP 공용(#223).

규칙은 ``apps.tenants.business_hours.hours_error``(순수 함수) 하나이고 HTTP PATCH와 MCP update_settings가 같이 쓴다.
저장된 나쁜 값 방어(is_open 이 예외 없이 「제한 없음」)도 여기서 본다.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from apps.tenants.models import Membership
from mcp_helpers import call, clock, flow_validator, make_tenant, mcp_token, member_of, signer, tool_text  # noqa: F401 — 픽스처
from resource_token_fakes import Signer

GOOD_DAY = {"enabled": True, "start": "09:00", "end": "18:00"}
BAD_VALUES = [
    ("hitl_timezone", "Mars/Base"),
    ("hitl_timezone", "../etc/passwd"),
    ("hitl_schedule", {"mon": [["09:00", "18:00"]]}),
    ("hitl_schedule", {"funday": GOOD_DAY}),
    ("hitl_schedule", {"mon": {"enabled": True, "start": "9am", "end": "18:00"}}),
    ("hitl_schedule", {"mon": {"enabled": True, "start": "25:00", "end": "26:00"}}),
    ("hitl_schedule", {"mon": {"enabled": True, "start": "18:00", "end": "09:00"}}),
    ("hitl_schedule", {"mon": {"enabled": True}}),
    ("hitl_schedule", {"mon": {"enabled": "yes", "start": "09:00", "end": "18:00"}}),
    ("hitl_schedule", {"mon": "09:00-18:00"}),
    ("hitl_holidays", ["12/25"]),
    ("hitl_holidays", ["2026-02-30"]),
    ("hitl_holidays", ["20261225"]),
    ("hitl_holidays", [20261225]),
]
GOOD_VALUES = [
    ("hitl_timezone", "Asia/Seoul"),
    ("hitl_timezone", ""),
    ("hitl_schedule", {}),
    ("hitl_schedule", {"mon": GOOD_DAY, "sun": {"enabled": False}}),
    ("hitl_schedule", {"mon": {"enabled": False, "start": "", "end": ""}}),  # 화면이 끈 요일을 보내는 모양
    ("hitl_holidays", []),
    ("hitl_holidays", ["2026-12-25", "2027-01-01"]),
]


def _patch(client, token, body):
    return client.patch("/api/tenant/config/", body, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", BAD_VALUES)
def test_http_rejects_malformed_hours_without_saving(client, tenant_admin_token, field, value) -> None:
    resp = _patch(client, tenant_admin_token, {field: value, "brand_name": "바뀌면 안 됨"})

    assert resp.status_code == 400, resp.content
    assert field in resp.json()["detail"]
    after = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}").json()
    assert after["brand_name"] != "바뀌면 안 됨"  # 한 칸이라도 틀리면 아무것도 안 바뀐다


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", GOOD_VALUES)
def test_http_accepts_well_formed_hours(client, tenant_admin_token, field, value) -> None:
    resp = _patch(client, tenant_admin_token, {field: value})

    assert resp.status_code == 200, resp.content
    assert resp.json()[field] == value


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", BAD_VALUES)
def test_mcp_rejects_malformed_hours_without_saving(signer: Signer, field, value) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), "update_settings", {field: value, "brand_name": "바뀌면 안 됨"})

    assert body["result"]["isError"], tool_text(body)
    assert "invalid_settings" in tool_text(body) and field in tool_text(body)
    tenant.config.refresh_from_db()
    assert tenant.config.brand_name != "바뀌면 안 됨"


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", GOOD_VALUES)
def test_mcp_accepts_well_formed_hours(signer: Signer, field, value) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)

    body = call(mcp_token(signer), "update_settings", {field: value})

    assert not body["result"].get("isError"), tool_text(body)


# ── 이미 저장된 나쁜 값: 예외 없이 「제한 없음」(opt-in 미설정과 같다) ─────────────────────────────


def _config(tz, schedule, holidays):
    return SimpleNamespace(hitl_timezone=tz, hitl_schedule=schedule, hitl_holidays=holidays)


CLOSED_WHEN_VALID = dt.datetime(2026, 6, 22, 20, 0, tzinfo=dt.timezone.utc)  # KST 화요일 05:00 — 올바른 설정이면 상담 시간 외


@pytest.mark.parametrize(
    "tz,schedule,holidays",
    [
        ("Mars/Base", {"mon": GOOD_DAY}, []),
        ("Asia/Seoul", {"mon": [["09:00", "18:00"]]}, []),
        ("Asia/Seoul", {"mon": {"enabled": True}}, []),
        ("Asia/Seoul", {"mon": {"enabled": True, "start": "9am", "end": "18:00"}}, []),
        ("Asia/Seoul", {"mon": GOOD_DAY}, [20260622]),
    ],
)
def test_stored_bad_value_does_not_raise_and_means_no_restriction(tz, schedule, holidays, caplog) -> None:
    from apps.tenants.business_hours import is_open

    with caplog.at_level("WARNING"):
        assert is_open(_config(tz, schedule, holidays), CLOSED_WHEN_VALID) is True

    assert "hitl" in caplog.text


def test_valid_config_still_gates() -> None:
    from apps.tenants.business_hours import is_open

    assert is_open(_config("Asia/Seoul", {"mon": GOOD_DAY}, []), CLOSED_WHEN_VALID) is False
