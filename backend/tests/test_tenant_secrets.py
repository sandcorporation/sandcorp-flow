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


# ── 가림(파생값 등록) ── 저장된 비밀이 없는 테넌트라도 redact는 그 테넌트 비밀 표를 본다(#118) → django_db ──

@pytest.mark.django_db
def test_redact_masks_revealed_value_and_its_substrings():
    import uuid

    from apps.tenants.tenant_secrets import MASK, redact, register_revealed

    tid = uuid.uuid4()
    value = "pw-" + pysecrets.token_hex(12)
    register_revealed(tid, value)

    assert redact(tid, f"key={value}; ok") == f"key={MASK}; ok"
    assert value not in redact(tid, f"[{value}]")
    assert redact(str(tid), value) == MASK  # 문자열 tenant_id도 같은 테넌트
    partial = value[5:15]  # 원문의 10자 부분 문자열(예: 잘린 오류 메시지)
    out = redact(tid, f"앞부분 {partial} 뒷부분")
    assert partial not in out and MASK in out and out.startswith("앞부분 ") and out.endswith(" 뒷부분")
    assert redact(tid, "평범한 문장 그대로") == "평범한 문장 그대로"


@pytest.mark.django_db
def test_redact_is_scoped_to_the_tenant_but_log_redaction_is_process_wide():
    import uuid

    from apps.tenants.tenant_secrets import MASK, redact, redact_log, register_revealed

    a, b = uuid.uuid4(), uuid.uuid4()
    value = "pw-" + pysecrets.token_hex(12)
    register_revealed(a, value)

    assert redact(a, value[3:15]) == MASK
    assert redact(b, value[3:15]) == value[3:15]  # 남의 비밀 조각은 이 테넌트 출력에 아무 흔적도 남기지 않는다
    assert redact(uuid.uuid4(), value) == value
    assert redact_log(f"x {value[3:15]} y") == f"x {MASK} y"


@pytest.mark.django_db
def test_redaction_lists_are_bounded_least_recently_used_first(monkeypatch):
    import uuid

    from apps.tenants import tenant_secrets as ts

    monkeypatch.setattr(ts, "_log_mask", ts._Mask(3))
    monkeypatch.setattr(ts, "MAX_TENANT_VALUES", 2)
    tid = uuid.uuid4()
    old, kept, *rest = ["pw-" + pysecrets.token_hex(12) for _ in range(4)]
    for v in (old, kept):
        ts.register_revealed(tid, v)
    ts.register_revealed(tid, kept)  # 다시 치환하면 최근 것이 된다
    for v in rest:
        ts.register_revealed(tid, v)

    assert ts.redact_log(old) == old and ts.redact_log(kept) == ts.MASK
    assert all(ts.redact_log(v) == ts.MASK for v in rest)
    assert ts.redact(tid, old) == old and ts.redact(tid, kept) == kept  # 테넌트 상한 2: 마지막 둘만
    assert all(ts.redact(tid, v) == ts.MASK for v in rest)


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


# ── 가림 범위: 테넌트마다(#115) ─────────────────────────────────────────────
# 테넌트가 읽는 출력(판단 기록·시험 대화 trace·MCP 응답)의 가림은 그 테넌트 비밀로만 한다. 다른 테넌트 비밀 조각이
# 이 테넌트 출력에서 가려지면, 임의 문자열을 넣어 보고 어디가 [비밀]로 바뀌는지로 그 비밀을 한 글자씩 알아낼 수 있다.

def _fresh() -> str:
    return "zx" + pysecrets.token_hex(14)


def _reveal(tenant, value: str) -> None:
    from apps.tenants.tenant_secrets import render_secrets, set_secret

    set_secret(tenant.id, "shop_key", value)
    render_secrets(tenant.id, "Bearer {{secret.shop_key}}")


@pytest.mark.django_db(transaction=True)
async def test_other_tenants_secret_does_not_mask_this_tenants_test_turn(tenant_with_key, fake_chat_llm, caplog):
    """A의 비밀 조각을 B가 시험 대화에 넣어도 B의 trace·판단 기록엔 원문 그대로 — 가림 오라클이 없다."""
    from asgiref.sync import sync_to_async as adb

    from apps.flows import services
    from apps.flows.models import DecisionTrace
    from test_flow_services import make_flow, other_tenant

    a, _ = tenant_with_key
    b = await adb(other_tenant)()
    secret = _fresh()
    await adb(_reveal)(a, secret)
    probe = f"혹시 {secret[2:14]} 맞나요"
    flow_b = await adb(make_flow)(b)

    result = await services.run_test_turn(b.id, flow_b.id, probe)

    assert result.ran, result
    dumped = json.dumps(result.trace, ensure_ascii=False)
    assert probe in dumped and "[비밀]" not in dumped
    stored = await adb(lambda: json.dumps([r.entries for r in DecisionTrace.objects.filter(tenant_id=b.id)],
                                          ensure_ascii=False))()
    assert probe in stored and "[비밀]" not in stored

    # A 자신의 시험 대화에서는 여전히 가려지고, 로그(운영자만 봄)에서도 가려진다.
    flow_a = await adb(make_flow)(a)
    mine = await services.run_test_turn(a.id, flow_a.id, probe)
    assert secret[2:14] not in json.dumps(mine.trace, ensure_ascii=False)
    with caplog.at_level(logging.INFO):
        logging.getLogger("apps.flows.http_tool").info("B가 보낸 말 %s", probe)
    assert secret[2:14] not in caplog.text and "[비밀]" in caplog.text


@pytest.mark.django_db
def test_other_tenants_secret_does_not_mask_this_tenants_mcp_test_turn(signer, fake_chat_llm):
    """MCP test_turn도 같은 원칙 — B의 응답 trace에 A 비밀 조각이 그대로, A의 응답에선 가려짐."""
    from test_flow_services import make_flow

    a, b = make_tenant("acme-a"), make_tenant("acme-b")
    member_of(a, Membership.ROLE_ADMIN)
    member_of(b, Membership.ROLE_ADMIN)
    secret = _fresh()
    _reveal(a, secret)
    probe = f"주문번호 {secret[5:17]} 확인"
    token = mcp_token(signer)

    def turn(tenant) -> str:
        flow = make_flow(tenant)
        body = call(token, "test_turn", {"tenant": tenant.slug, "flow_id": str(flow.id), "message": probe})
        return tool_text(body)

    theirs, mine = turn(b), turn(a)
    assert secret[5:17] in theirs and "[비밀]" not in theirs
    assert secret[5:17] not in mine


# ── 가림은 DB에 저장된 그 테넌트 비밀 전체로(#118) ───────────────────────────
# 운영은 api(uvicorn 워커 2)와 worker-chat이 서로 다른 프로세스다. 비밀을 저장한 프로세스만 그 값을 알면 시험 대화를
# 돌리는 프로세스의 판단 기록·SSE trace에 원문이 그대로 나간다. 「다른 프로세스가 저장」은 이 프로세스의 가림 함수를
# 거치지 않고 ORM으로 바로 넣어 흉내 낸다.

def _store_elsewhere(tenant_id, name: str, value: str):
    """다른 프로세스가 저장한 비밀 — 이 프로세스의 ``set_secret``·``render_secrets``를 거치지 않는다."""
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantSecret

    secret, _ = TenantSecret.objects.update_or_create(
        tenant_id=tenant_id, name=name, defaults={"ciphertext": encrypt_secret(value)})
    return secret


def _later(monkeypatch, seconds: float) -> None:
    """캐시 시계를 ``seconds``만큼 앞으로 — 다른 프로세스의 변경을 TTL로 따라잡는지 본다."""
    import time

    from apps.tenants import tenant_secrets as ts

    base = time.monotonic()
    monkeypatch.setattr(ts, "_clock", lambda: base + seconds, raising=False)


@pytest.mark.django_db(transaction=True)
def test_secret_stored_by_another_process_is_masked_in_test_conversation(client, tenant_with_key, tenant_admin_token,
                                                                          fake_chat_llm):
    """비밀을 모르는 프로세스에서 돌린 시험 대화 — SSE trace와 저장 판단 기록 모두 그 테넌트 비밀을 가린다."""
    from test_flow_api import jpost, new_flow, sse_events

    tenant, _ = tenant_with_key
    secret = _fresh()
    _store_elsewhere(tenant.id, "shop_key", secret)
    fid = new_flow(client, tenant_admin_token)

    r = jpost(client, f"/api/tenant/flows/{fid}/test/", {"message": f"키는 {secret} 이고 앞은 {secret[:12]}"},
              tenant_admin_token)

    events = sse_events(r)
    traced = json.dumps([d for n, d in events if n == "trace"], ensure_ascii=False)
    assert secret[:12] not in traced and secret[6:18] not in traced and "[비밀]" in traced
    session_id = dict(events)["done"]["session_id"]
    stored = client.get(f"/api/tenant/sessions/{session_id}/decision-traces", **_hdr(tenant_admin_token)).json()
    dumped = json.dumps(stored, ensure_ascii=False)
    assert secret[:12] not in dumped and "[비밀]" in dumped


@pytest.mark.django_db(transaction=True)
async def test_deleted_secret_is_no_longer_masked_and_replaced_value_is(tenant_with_key, fake_chat_llm):
    """같은 프로세스의 저장·삭제는 곧바로 반영 — 지운 값은 더 가리지 않고, 바꾼 값은 새 값을 가린다."""
    from asgiref.sync import sync_to_async as adb

    from apps.flows import services
    from apps.tenants.tenant_secrets import delete_secret, set_secret
    from test_flow_services import make_flow

    tenant, _ = tenant_with_key
    flow = await adb(make_flow)(tenant)
    old, new = _fresh(), _fresh()

    async def traced(text: str) -> str:
        result = await services.run_test_turn(tenant.id, flow.id, text)
        assert result.ran, result
        return json.dumps(result.trace, ensure_ascii=False)

    await adb(set_secret)(tenant.id, "shop_key", old)
    assert old[3:15] not in await traced(f"이거 {old[3:15]}")

    await adb(set_secret)(tenant.id, "shop_key", new)
    assert new[3:15] not in await traced(f"이거 {new[3:15]}")

    await adb(delete_secret)(tenant.id, "shop_key")
    after = await traced(f"이거 {new[3:15]}")
    assert new[3:15] in after and "[비밀]" not in after


@pytest.mark.django_db
def test_another_processes_change_and_delete_converge_after_ttl(tenant_with_key, monkeypatch):
    """다른 프로세스의 교체·삭제는 캐시 TTL이 지나면 따라잡는다(표의 개수·최근 수정 시각으로 바뀜을 안다)."""
    from apps.tenants import tenant_secrets as ts
    from apps.tenants.models import TenantSecret

    tenant, _ = tenant_with_key
    old, new = _fresh(), _fresh()
    _store_elsewhere(tenant.id, "shop_key", old)
    assert ts.redact(tenant.id, old) == "[비밀]"

    _store_elsewhere(tenant.id, "shop_key", new)
    _later(monkeypatch, ts.STORED_TTL_SECONDS + 1)
    assert ts.redact(tenant.id, new) == "[비밀]" and ts.redact(tenant.id, old) == old

    TenantSecret.objects.filter(tenant_id=tenant.id).delete()
    _later(monkeypatch, 2 * ts.STORED_TTL_SECONDS + 2)
    assert ts.redact(tenant.id, new) == new


@pytest.mark.django_db
def test_render_picks_up_another_processes_new_value_without_waiting(tenant_with_key):
    """치환이 캐시가 모르는 값을 복호화하면(다른 프로세스가 방금 바꿈) TTL을 기다리지 않고 그 값을 가린다."""
    from apps.tenants import tenant_secrets as ts

    tenant, _ = tenant_with_key
    old, new = _fresh(), _fresh()
    _store_elsewhere(tenant.id, "shop_key", old)
    assert ts.redact(tenant.id, old) == "[비밀]"
    _store_elsewhere(tenant.id, "shop_key", new)

    ts.render_secrets(tenant.id, "Bearer {{secret.shop_key}}")

    assert ts.redact(tenant.id, f"응답 {new}") == "응답 [비밀]"


@pytest.mark.django_db
def test_tenant_without_secrets_costs_no_query_within_ttl(tenant_with_key, monkeypatch, django_assert_num_queries):
    """비밀 없는 테넌트는 빈 집합을 캐시 — TTL 안에선 조회 0번, 지나면 개수 확인 한 번(복호화 없음)."""
    from apps.tenants import tenant_secrets as ts

    tenant, _ = tenant_with_key
    ts.redact(tenant.id, "아무 말")
    with django_assert_num_queries(0):
        assert ts.redact(tenant.id, "아무 말 abcdefgh") == "아무 말 abcdefgh"
    _later(monkeypatch, ts.STORED_TTL_SECONDS + 1)
    with django_assert_num_queries(1):
        ts.redact(tenant.id, "또 다른 말")
