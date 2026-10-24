"""Tenant Secret·허용 도메인(#33, ADR-0029 「비밀」) — 공격 시험 먼저.

비밀 값은 쓰기 전용이다: API 응답·판단 기록·예외 메시지·로그·MCP 응답 어디에도 원문(과 원문의 부분 문자열)이
나오지 않는다. 흐름 JSON에는 ``{{secret.이름}}`` 참조만 두고, 치환은 요청을 만들 때 서버에서만 한다.
외부 경계는 MCP의 JWKS·시계만 대역이다. DB·암호화·로그는 실제 객체다.
"""
from __future__ import annotations

import json
import logging
import secrets as pysecrets

import pytest

from apps.tenants import permissions as P
from apps.tenants.models import Membership, Tenant
from mcp_helpers import call, clock, flow_validator, make_tenant, mcp_token, member_of, signer, tool_text  # noqa: F401 — 픽스처

SECRETS = "/api/tenant/secrets/"
DOMAINS = "/api/tenant/allowed-domains/"
VALUE = "zq" + pysecrets.token_hex(14)
"""무작위 값 — 가림 목록은 프로세스 단위라 고정 문자열(`sk-live…`)을 쓰면 다른 시험의 로그·응답까지 가린다."""
ROTATED = "zr" + pysecrets.token_hex(10)


def _hdr(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _put(client, name: str, value: str, token: str):
    return client.put(f"{SECRETS}{name}", {"value": value}, content_type="application/json", **_hdr(token))


def _http_flow(header_value: str) -> dict:
    return {
        "nodes": [
            {"id": "start", "kind": "start", "code": "N-01"},
            {"id": "h", "kind": "http_tool", "code": "N-02", "config": {
                "method": "GET", "url": "https://api.example.com/orders",
                "headers": {"Authorization": header_value},
            }},
        ],
        "edges": [{"from": "start", "to": "h"}],
    }


# ── 권한 비트 ───────────────────────────────────────────────────────────────

def test_secret_and_domain_management_are_admin_only_bits():
    member, admin = Membership(role=P.ROLE_MEMBER), Membership(role=P.ROLE_ADMIN)
    for bit in (P.SECRETS_MANAGE, P.DOMAINS_MANAGE):
        assert bit in P.ADMIN_ONLY
        assert P.has_permission(admin, bit) and not P.has_permission(member, bit)


# ── 가림(순수) ──────────────────────────────────────────────────────────────

def test_redact_masks_revealed_value_and_its_substrings():
    from apps.tenants.tenant_secrets import MASK, redact, register_revealed

    value = "pw-" + pysecrets.token_hex(12)
    register_revealed(value)

    assert redact(f"key={value}; ok") == f"key={MASK}; ok"
    assert value not in redact(f"[{value}]")
    partial = value[5:15]  # 원문의 10자 부분 문자열(예: 잘린 오류 메시지)
    out = redact(f"앞부분 {partial} 뒷부분")
    assert partial not in out and MASK in out and out.startswith("앞부분 ") and out.endswith(" 뒷부분")
    assert redact("평범한 문장 그대로") == "평범한 문장 그대로"


# ── API: 비밀은 쓰기 전용 ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_admin_writes_secret_and_reads_back_only_its_name(client, tenant_with_key, tenant_admin_token):
    from apps.tenants.models import TenantSecret
    from apps.tenants.tenant_secrets import render_secrets

    tenant, _ = tenant_with_key
    r = _put(client, "shop_key", VALUE, tenant_admin_token)
    assert r.status_code == 200, r.content
    assert VALUE not in r.content.decode() and r.json()["name"] == "shop_key"

    listed = client.get(SECRETS, **_hdr(tenant_admin_token))
    assert listed.status_code == 200 and [s["name"] for s in listed.json()] == ["shop_key"]
    assert VALUE not in listed.content.decode()

    row = TenantSecret.objects.get(tenant=tenant, name="shop_key")
    assert VALUE not in row.ciphertext  # 암호문으로만 저장
    assert render_secrets(tenant.id, "Bearer {{secret.shop_key}}") == f"Bearer {VALUE}"


@pytest.mark.django_db
def test_admin_replaces_and_deletes_secret(client, tenant_with_key, tenant_admin_token):
    from apps.tenants.tenant_secrets import SecretNotFound, render_secrets

    tenant, _ = tenant_with_key
    _put(client, "shop_key", VALUE, tenant_admin_token)
    assert _put(client, "shop_key", ROTATED, tenant_admin_token).status_code == 200
    assert render_secrets(tenant.id, "{{ secret.shop_key }}") == ROTATED
    assert len(client.get(SECRETS, **_hdr(tenant_admin_token)).json()) == 1

    assert client.delete(f"{SECRETS}shop_key", **_hdr(tenant_admin_token)).status_code == 204
    assert client.delete(f"{SECRETS}shop_key", **_hdr(tenant_admin_token)).status_code == 404
    with pytest.raises(SecretNotFound):
        render_secrets(tenant.id, "{{secret.shop_key}}")


@pytest.mark.django_db
def test_member_cannot_write_or_delete_secrets_but_sees_names(client, tenant_with_key, tenant_admin_token,
                                                              tenant_member_token):
    _put(client, "shop_key", VALUE, tenant_admin_token)

    assert _put(client, "shop_key", "zm" + pysecrets.token_hex(8), tenant_member_token).status_code == 403
    assert _put(client, "new_key", "zn" + pysecrets.token_hex(8), tenant_member_token).status_code == 403
    assert client.delete(f"{SECRETS}shop_key", **_hdr(tenant_member_token)).status_code == 403
    listed = client.get(SECRETS, **_hdr(tenant_member_token))
    assert listed.status_code == 200 and [s["name"] for s in listed.json()] == ["shop_key"]


@pytest.mark.django_db
@pytest.mark.parametrize("name, value", [
    ("bad-name", VALUE),             # 이름 규칙 위반
    ("1starts_with_digit", VALUE),
    ("shop_key", "short"),           # 너무 짧은 값
    ("shop_key", VALUE * 300),       # 너무 긴 값
])
def test_invalid_secret_is_rejected_without_echoing_the_value(client, tenant_admin_token, name, value):
    r = _put(client, name, value, tenant_admin_token)
    assert r.status_code == 422
    assert value not in r.content.decode() and VALUE[:12] not in r.content.decode()


@pytest.mark.django_db
def test_other_tenants_secret_is_invisible(client, tenant_with_key, tenant_admin_token):
    from apps.tenants.tenant_secrets import SecretNotFound, render_secrets, set_secret

    tenant, _ = tenant_with_key
    other = Tenant.objects.create_with_key(name="Other Co", raw_key=pysecrets.token_urlsafe(16))
    set_secret(other.id, "their_key", VALUE)

    assert client.get(SECRETS, **_hdr(tenant_admin_token)).json() == []
    assert client.delete(f"{SECRETS}their_key", **_hdr(tenant_admin_token)).status_code == 404
    with pytest.raises(SecretNotFound) as exc:
        render_secrets(tenant.id, "Bearer {{secret.their_key}}")
    assert "their_key" in str(exc.value) and VALUE not in str(exc.value)


# ── 치환·검증 ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_unknown_secret_reference_is_a_flow_validation_error(tenant_with_key):
    from apps.flows import services
    from apps.tenants.tenant_secrets import set_secret

    tenant, _ = tenant_with_key
    flow = services.create_flow(tenant.id, "주문 조회", _http_flow("Bearer {{secret.shop_key}}"))

    draft = services.flow_detail(tenant.id, flow.id).draft
    assert [e.code for e in draft.errors] == ["unknown_secret"] and "shop_key" in draft.errors[0].message
    with pytest.raises(services.FlowInvalid) as exc:
        services.save_version(tenant.id, flow.id)
    assert [e.code for e in exc.value.errors] == ["unknown_secret"]

    set_secret(tenant.id, "shop_key", VALUE)
    assert services.flow_detail(tenant.id, flow.id).draft.errors == []
    assert services.save_version(tenant.id, flow.id).created


# ── 가림: 로그·예외·판단 기록 ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_revealed_secret_never_reaches_logs(tenant_with_key, caplog):
    from apps.tenants.tenant_secrets import render_secrets, set_secret

    tenant, _ = tenant_with_key
    set_secret(tenant.id, "shop_key", VALUE)
    header = render_secrets(tenant.id, "Bearer {{secret.shop_key}}")
    log = logging.getLogger("apps.flows.http_tool")

    with caplog.at_level(logging.INFO):
        log.info("요청 헤더 %s", header)
        log.warning(f"잘린 응답: ...{VALUE[4:20]}...")
        try:
            raise RuntimeError(f"upstream 401 for key {VALUE}")
        except RuntimeError:
            log.exception("HTTP 도구 실패")

    assert VALUE not in caplog.text and VALUE[4:20] not in caplog.text
    assert all(VALUE not in r.getMessage() for r in caplog.records)
    assert "HTTP 도구 실패" in caplog.text and "[비밀]" in caplog.text


@pytest.mark.django_db
def test_decision_trace_masks_revealed_secret(tenant_with_key):
    from apps.chat.models import ChatSession
    from apps.flows.models import DecisionTrace
    from apps.flows.trace import save_decision_trace
    from apps.tenants.tenant_secrets import render_secrets, set_secret

    tenant, _ = tenant_with_key
    set_secret(tenant.id, "shop_key", VALUE)
    header = render_secrets(tenant.id, "Bearer {{secret.shop_key}}")
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")
    entry = {"node_id": "h", "kind": "http_tool", "record": {"request": {"Authorization": header},
                                                            "error": f"401: bad key {VALUE[:-3]}"},
             "input_tokens": 0, "output_tokens": 0, "cost_usd": None}

    save_decision_trace(session, [entry], 3)  # type: ignore[list-item]

    dumped = json.dumps(DecisionTrace.objects.get(session=session).entries, ensure_ascii=False)
    assert VALUE not in dumped and VALUE[:-3] not in dumped and "[비밀]" in dumped


# ── MCP로 새는 길 ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_mcp_shows_only_secret_references(signer):
    from apps.chat.models import ChatSession
    from apps.flows import services
    from apps.flows.trace import save_decision_trace
    from apps.tenants.tenant_secrets import render_secrets, set_secret

    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_ADMIN)
    set_secret(tenant.id, "shop_key", VALUE)
    flow = services.create_flow(tenant.id, "주문 조회", _http_flow("Bearer {{secret.shop_key}}"))
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v1")
    header = render_secrets(tenant.id, "Bearer {{secret.shop_key}}")
    save_decision_trace(session, [{"node_id": "h", "kind": "http_tool", "record": {"request": header},  # type: ignore[list-item]
                                   "input_tokens": 0, "output_tokens": 0, "cost_usd": None}], 1)
    token = mcp_token(signer, scope="flow.read")

    flow_text = tool_text(call(token, "get_flow", {"flow_id": str(flow.id)}))
    assert "{{secret.shop_key}}" in flow_text and VALUE not in flow_text
    for tool, args in [("get_session", {"session_id": str(session.id)}), ("list_flows", {}),
                       ("list_sessions", {}), ("list_tenants", {})]:
        assert VALUE not in tool_text(call(token, tool, args)), tool


# ── 허용 도메인 ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_admin_manages_allowed_domains_with_normalized_patterns(client, tenant_with_key, tenant_admin_token):
    from apps.tenants.tenant_secrets import allow_list_for

    tenant, _ = tenant_with_key
    hdr = _hdr(tenant_admin_token)
    r = client.post(DOMAINS, {"pattern": " API.Example.com. "}, content_type="application/json", **hdr)
    assert r.status_code == 201, r.content
    assert r.json()["pattern"] == "api.example.com"
    assert client.post(DOMAINS, {"pattern": "*.shop.example.org"}, content_type="application/json",
                       **hdr).status_code == 201
    dup = client.post(DOMAINS, {"pattern": "api.example.com"}, content_type="application/json", **hdr)
    assert dup.status_code == 409

    listed = client.get(DOMAINS, **hdr).json()
    assert sorted(d["pattern"] for d in listed) == ["*.shop.example.org", "api.example.com"]
    policy = allow_list_for(tenant.id)
    assert policy.allows("api.example.com") and policy.allows("a.shop.example.org")
    assert not policy.allows("evil.example.com") and not policy.allows("shop.example.org")

    target = next(d for d in listed if d["pattern"] == "api.example.com")
    assert client.delete(f"{DOMAINS}{target['id']}", **hdr).status_code == 204
    assert [d["pattern"] for d in client.get(DOMAINS, **hdr).json()] == ["*.shop.example.org"]


@pytest.mark.django_db
@pytest.mark.parametrize("pattern", [
    "https://api.example.com", "api.example.com/path", "api.example.com:8443", "user@api.example.com",
    "1.2.3.4", "[::1]", "localhost", "*.com", "a.*.example.com", "*", "**.example.com", "",
])
def test_bad_domain_patterns_are_rejected(client, tenant_admin_token, pattern):
    r = client.post(DOMAINS, {"pattern": pattern}, content_type="application/json", **_hdr(tenant_admin_token))
    assert r.status_code == 422, (pattern, r.content)


@pytest.mark.django_db
def test_member_cannot_change_domains_and_other_tenant_rows_are_404(client, tenant_admin_token, tenant_member_token):
    from apps.tenants.models import TenantAllowedDomain

    assert client.post(DOMAINS, {"pattern": "api.example.com"}, content_type="application/json",
                       **_hdr(tenant_member_token)).status_code == 403
    other = Tenant.objects.create_with_key(name="Other Co", raw_key=pysecrets.token_urlsafe(16))
    theirs = TenantAllowedDomain.objects.create(tenant=other, pattern="their.example.com")

    assert client.delete(f"{DOMAINS}{theirs.id}", **_hdr(tenant_admin_token)).status_code == 404
    assert client.get(DOMAINS, **_hdr(tenant_admin_token)).json() == []
    mine = client.post(DOMAINS, {"pattern": "api.example.com"}, content_type="application/json",
                       **_hdr(tenant_admin_token)).json()
    assert client.delete(f"{DOMAINS}{mine['id']}", **_hdr(tenant_member_token)).status_code == 403
