"""가짜 API(#131, ADR-0029 보충) — 예약 호스트를 HTTP 도구가 프로세스 안에서 처리.

- 예약 호스트 **정확 일치**(https·기본 포트·소문자·끝점 없음)일 때만 프로세스 안 핸들러. 그 밖은 지금처럼 safe_http.
- 프로세스 안 응답도 같은 ``Limits``(크기·시간)·``save``·실패 코드 규칙을 거친다.
- 네트워크 대역(``safe_http.NETWORK``의 FakeDns·route·로컬 서버)으로 「조회 0·연결 0」을 박는다.
- LLM은 conftest의 결정적 Fake.
"""
from __future__ import annotations

import json
import secrets as pysecrets
import uuid

import pytest
from asgiref.sync import sync_to_async as adb

from apps.core import safe_http
from tests.test_flow_http_tool import Net, _flow, _http_entry, _path, _setup, _turn, net, tool_pki  # noqa: F401

DEMO = "demo-api.flow.sandcorp.co.kr"
BASE = f"https://{DEMO}"


def _no_network(n: Net) -> None:
    assert n.dns.calls == [] and n.route == [] and n.hits.items == []


def test_reserved_host_is_one_constant():
    from apps.flows import demo_api

    assert demo_api.HOST == DEMO


# ── 주문 조회 ────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_order_lookup_runs_in_process_saves_and_goes_next(tenant_with_key, net):
    tenant, _ = tenant_with_key
    flow = _flow({
        "url": f"{BASE}/orders/{{{{state.user_message}}}}",
        "save": {"status": "$.status", "label": "$.status_label", "tracking": "$.tracking_no"},
    }, after={"branches": [{"name": "shipping", "when": "fields.status == 'shipping'"}]})

    result, _ = await _turn(tenant, flow, "10021")

    assert _path(result) == ["s", "h", "c", "ok"]
    assert result["fields"] == {"status": "shipping", "label": "배송 중", "tracking": "SC0000010021"}
    entry = _http_entry(result)
    assert entry["record"]["status"] == 200 and entry["record"]["error"] is None
    assert entry["record"]["request"] == {"method": "GET", "url": f"{BASE}/orders/10021"}
    _no_network(net)  # 허용 목록에 아무것도 없어도(암묵 포함) 네트워크 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("order_no", "found", "status", "label"), [
    ("10021", True, "shipping", "배송 중"),
    ("10024", True, "delivered", "배송 완료"),
    ("10027", True, "preparing", "상품 준비 중"),
    ("10020", False, "not_found", "주문을 찾을 수 없음"),
    ("주문123", False, "not_found", "주문을 찾을 수 없음"),
])
async def test_order_number_rule_decides_the_status(tenant_with_key, net, order_no, found, status, label):
    tenant, _ = tenant_with_key
    flow = _flow({"url": f"{BASE}/orders/{{{{state.user_message}}}}",
                  "save": {"found": "$.found", "status": "$.status", "label": "$.status_label"}})

    result, _ = await _turn(tenant, flow, order_no)

    assert _path(result) == ["s", "h", "ok"]
    assert result["fields"] == {"found": found, "status": status, "label": label}
    _no_network(net)


# ── 예약·문의 접수 ────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_reservation_confirms_a_deterministic_number(tenant_with_key, net):
    tenant, _ = tenant_with_key
    flow = _flow({
        "method": "POST", "url": f"{BASE}/reservations",
        "body_template": '{"date": "2026-11-03", "level": "{{state.user_message}}", "name": "김샌드"}',
        "save": {"reserved": "$.reserved", "no": "$.reservation_no", "level": "$.level", "time": "$.time"},
    })

    first, _ = await _turn(tenant, flow, "초급")
    second, _ = await _turn(tenant, flow, "초급")

    assert _path(first) == ["s", "h", "ok"]
    assert first["fields"]["reserved"] is True and first["fields"]["level"] == "beginner"
    assert first["fields"]["time"] == "15:00"
    assert first["fields"]["no"].startswith("LT-20261103-") and len(first["fields"]["no"]) == len("LT-20261103-ABCD")
    assert second["fields"] == first["fields"]  # 결정적·무상태
    _no_network(net)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("body", [
    '{"date": "2026-11-03", "level": "{{state.user_message}}"}',  # 모르는 레벨
    '{"date": "11월 3일", "level": "beginner"}',  # 날짜 모양
    'not json {{state.user_message}}',
])
async def test_bad_reservation_takes_the_error_branch_as_http_400(tenant_with_key, net, body):
    tenant, _ = tenant_with_key
    flow = _flow({"method": "POST", "url": f"{BASE}/reservations", "body_template": body,
                  "save": {"no": "$.reservation_no"}})

    result, _ = await _turn(tenant, flow, "최상급")

    assert _path(result) == ["s", "h", "failed"]
    record = _http_entry(result)["record"]
    assert (record["error"], record["status"]) == ("http_error", 400)
    assert result["fields"] == {"no": None}
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_removed_inquiries_path_is_not_found(tenant_with_key, net):
    """#164: POST /inquiries는 지웠다 — 모르는 경로와 같이 404 http_error(쿼리·본문이 달라도)."""
    tenant, _ = tenant_with_key

    plain, _ = await _turn(tenant, _flow({"method": "POST", "url": f"{BASE}/inquiries",
                                          "body_template": '{"message": "견적이 궁금해요"}'}))
    simulated, _ = await _turn(tenant, _flow({"method": "POST", "url": f"{BASE}/inquiries?simulate=error",
                                              "body_template": '{"message": "x"}'}))

    for result in (plain, simulated):
        record = _http_entry(result)["record"]
        assert _path(result) == ["s", "h", "failed"]
        assert (record["error"], record["status"]) == ("http_error", 404)
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_unknown_path_and_wrong_method_are_http_errors(tenant_with_key, net):
    tenant, _ = tenant_with_key

    missing, _ = await _turn(tenant, _flow({"url": f"{BASE}/nope"}))
    wrong, _ = await _turn(tenant, _flow({"method": "DELETE", "url": f"{BASE}/orders/10021"}))

    assert (_http_entry(missing)["record"]["error"], _http_entry(missing)["record"]["status"]) == ("http_error", 404)
    assert (_http_entry(wrong)["record"]["error"], _http_entry(wrong)["record"]["status"]) == ("http_error", 405)
    _no_network(net)


# ── 실패 흉내: error 갈래 시연 ─────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("url", "message", "code", "status"), [
    (f"{BASE}/orders/{{{{state.user_message}}}}", "9500", "http_error", 500),
    (f"{BASE}/orders/{{{{state.user_message}}}}", "9408", "timeout", None),
    (f"{BASE}/orders/10021?simulate=error", "", "http_error", 500),
    (f"{BASE}/orders/10021?simulate=timeout", "", "timeout", None),
    (f"{BASE}/reservations?simulate=error", "", "http_error", 500),
])
async def test_failure_simulation_takes_the_error_branch(tenant_with_key, net, url, message, code, status):
    tenant, _ = tenant_with_key
    method = "POST" if "reservations" in url else "GET"
    flow = _flow({"method": method, "url": url, "body_template": '{"message": "x"}' if method == "POST" else None,
                  "save": {"status": "$.status"}})

    result, _ = await _turn(tenant, flow, message or "안녕")

    assert _path(result) == ["s", "h", "failed"]
    record = _http_entry(result)["record"]
    assert (record["error"], record["status"]) == (code, status)
    assert result["fields"] == {"status": None}
    _no_network(net)


@pytest.mark.django_db(transaction=True)
async def test_in_process_replies_obey_the_same_limits(tenant_with_key, net, monkeypatch):
    """지연 흉내는 실제로 자지 않는다 — 흉내 지연이 ``LIMITS.total_timeout``을 넘을 때만 timeout. 크기도 같은 한도."""
    from apps.flows import http_tool

    tenant, _ = tenant_with_key
    flow = _flow({"url": f"{BASE}/orders/9408", "save": {"status": "$.status"}})

    monkeypatch.setattr(http_tool, "LIMITS", safe_http.Limits(total_timeout=60.0))
    patient, _ = await _turn(tenant, flow)
    monkeypatch.setattr(http_tool, "LIMITS", safe_http.Limits(max_body_bytes=16))
    tiny, _ = await _turn(tenant, _flow({"url": f"{BASE}/orders/10021"}))

    assert _path(patient) == ["s", "h", "ok"] and patient["fields"] == {"status": "shipping"}
    assert _http_entry(tiny)["record"]["error"] == "too_large"
    _no_network(net)


# ── 비밀: 실어도 응답·판단 기록에 원문 없음 ─────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_secrets_sent_to_the_fake_api_never_come_back(tenant_with_key, net):
    from apps.flows import services
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    key = "sk-" + pysecrets.token_hex(12)
    await adb(_setup)(tenant.id, domains=(), secrets={"demo_key": key})
    graph = _flow({
        "method": "POST", "url": f"{BASE}/reservations",
        "headers": {"Authorization": "Bearer {{secret.demo_key}}", "X-Api-Key": "{{secret.demo_key}}"},
        "body_template": '{"name": "{{secret.demo_key}}", "message": "{{secret.demo_key}}"}',
        "save": {"no": "$.reservation_no", "all": "$"},
    })
    flow = await adb(services.create_flow)(tenant.id, "demo", graph)

    result = await services.run_test_turn(tenant.id, flow.id, "안녕")

    assert result.ran, result
    stored = await adb(lambda: list(DecisionTrace.objects.filter(tenant_id=tenant.id).values_list("entries", flat=True)))()
    for blob in (json.dumps(result.trace, ensure_ascii=False), json.dumps(stored, ensure_ascii=False)):
        assert key not in blob
    _no_network(net)


# ── 비슷한 호스트는 절대 프로세스 안으로 가지 않는다 ────────────────────────────

VARIANTS = [
    (f"https://{DEMO}.evil.com/orders/10021", "host_not_allowed"),
    (f"https://x.{DEMO}/orders/10021", "host_not_allowed"),
    (f"http://{DEMO}/orders/10021", "not_https"),
    (f"https://{DEMO}:8443/orders/10021", "non_default_port"),
    (f"https://{DEMO}:443/orders/10021", "host_not_allowed"),
    (f"https://{DEMO.upper()}/orders/10021", "host_not_allowed"),
    (f"https://{DEMO}./orders/10021", "host_not_allowed"),
    ("https://demo-аpi.flow.sandcorp.co.kr/orders/10021", "host_not_allowed"),  # 키릴 а(IDN)
    (f"https://u@{DEMO}/orders/10021", "userinfo"),
    ("https://flow.sandcorp.co.kr/orders/10021", "host_not_allowed"),
]


async def _call(tenant_id: uuid.UUID, url: str):
    from apps.flows import http_tool
    from apps.flows.schema import HttpToolConfig

    config = HttpToolConfig.model_construct(method="GET", url=url, headers={}, body_template=None,
                                            save={"status": "$.status"})  # http·userinfo는 저장 검증이 먼저 막는다
    return await http_tool.call(tenant_id, config, http_tool.Scope("", "", None, {}))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("url", "code"), VARIANTS)
async def test_lookalike_hosts_take_the_safe_http_path(tenant_with_key, net, url, code):
    tenant, _ = tenant_with_key

    result = await _call(tenant.id, url)

    assert (result.error, result.saved) == (code, {"status": None})
    _no_network(net)  # 허용 목록 밖이라 해석 전에 막힌다


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("pattern", "url", "resolved"), [
    (f"*.{DEMO}", f"https://x.{DEMO}/orders/10021", f"x.{DEMO}"),
    (DEMO, f"https://{DEMO.upper()}/orders/10021", DEMO),
    (DEMO, f"https://{DEMO}./orders/10021", DEMO),
    (DEMO, f"https://{DEMO}:443/orders/10021", DEMO),
])
async def test_allowed_variants_still_go_to_the_network_not_in_process(tenant_with_key, net, pattern, url, resolved):
    """허용 목록에 넣어 둔 테넌트에서도 변형은 safe_http로 — 이름을 실제로 해석하려 든다(대역에 없으니 dns_failed)."""
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id, domains=(pattern,))

    result = await _call(tenant.id, url)

    assert result.error == "dns_failed"
    assert net.dns.calls == [resolved]
