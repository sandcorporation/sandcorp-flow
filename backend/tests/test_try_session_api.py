"""Try Session 공개 API·세션 쿠키·한도(Try Session 2/2, #135, ADR-0030).

인증 없는 공개 API로 템플릿을 고르고 「고쳐 보기」로 체험 테넌트를 만든다. 브라우저는 추측 불가 세션 ID를
HttpOnly 쿠키로 들고, 서버는 그 해시만 둔다. 사본 읽기·초안 저장·시험 대화(SSE)는 편집기 API와 같은 모양이고
배포는 거부한다. 한도: 세션당 20턴(체험 테넌트의 ``trial_turns_remaining`` + ``admit_turn``), IP당 하루 3세션,
전체 하루 비용 $5(정리에 지워지지 않는 날짜별 누적, Asia/Seoul 날짜).

LLM만 conftest의 결정적 Fake(사용량 gpt-4o-mini 120/30 보고). DB·Redis·HTTP 수신부는 실제다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from tests.test_flow_api import sse_events

BASE = "/api/try"
ORIGIN = "http://testserver"
COOKIE = "sf_try"
PLATFORM_KEY = "sk-platform-test"


@pytest.fixture
def prod_platform(settings):
    """운영처럼: dev 폴백 끔 + 플랫폼 OpenAI 키 있음(턴을 센다)."""
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = PLATFORM_KEY
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    settings.TRUSTED_PROXY_COUNT = 1
    return settings


def _start(client: Client, template: str = "sandcorp_home", ip: str = "203.0.113.7", **extra: str):
    headers = {"HTTP_ORIGIN": ORIGIN, "REMOTE_ADDR": ip, **extra}
    return client.post(f"{BASE}/sessions/", json.dumps({"template": template}), content_type="application/json", **headers)


def _session(ip: str = "203.0.113.7", template: str = "sandcorp_home") -> tuple[Client, dict]:
    c = Client()
    r = _start(c, template, ip)
    assert r.status_code == 201, r.content
    return c, r.json()


def _turn(c: Client, flow_id: str, message: str = "안녕하세요", origin: str | None = ORIGIN):
    extra = {"HTTP_ORIGIN": origin} if origin else {}
    return c.post(
        f"{BASE}/flows/{flow_id}/test/", json.dumps({"message": message}), content_type="application/json", **extra
    )


ANSWER_ONLY = {
    "nodes": [
        {"id": "start", "kind": "start", "code": "N-01", "name": "시작", "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "answer", "kind": "ai_answer", "code": "N-02", "name": "답", "position": {"x": 200, "y": 0}, "config": {}},
    ],
    "edges": [{"from": "start", "to": "answer"}],
}


def _answer_only(c: Client, flow_id: str) -> None:
    """LLM 답 노드 하나짜리로 고친다(Fake가 사용량 gpt-4o-mini 120/30을 보고하는 경로)."""
    r = c.put(
        f"{BASE}/flows/{flow_id}/draft/", json.dumps({"graph": ANSWER_ONLY}), content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )
    assert r.status_code == 200 and r.json()["valid"], r.content


def _set_turns(flow_id: str, n: int) -> None:
    from apps.flows.models import Flow
    from apps.tenants.models import Tenant

    Tenant.objects.filter(pk=Flow.objects.get(pk=flow_id).tenant_id).update(trial_turns_remaining=n)


# ── 템플릿 목록·세션 만들기·쿠키 ─────────────────────────────────────────────

@pytest.mark.django_db
def test_templates_are_public_and_list_every_example(client):
    r = client.get(f"{BASE}/templates/")

    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert ids == ["sandcorp_home", "shop_support", "academy_reservation"]
    assert all(t["name"] and isinstance(t["documents"], list) for t in r.json())


@pytest.mark.django_db
def test_template_detail_is_public_and_gives_the_graph_without_a_session(client):
    """「흐름 보기」(읽기 전용)는 세션·쿠키 없이 예제의 흐름 그래프를 읽는다(#37). 세션 한도를 쓰지 않는다."""
    from apps.tenants.models import TrySession

    r = client.get(f"{BASE}/templates/shop_support/")

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "shop_support" and body["name"]
    assert body["graph"]["nodes"] and "edges" in body["graph"]
    assert isinstance(body["documents"], list)
    assert TrySession.objects.count() == 0 and "sf_try" not in r.cookies


@pytest.mark.django_db
def test_template_detail_unknown_is_404_with_code(client):
    r = client.get(f"{BASE}/templates/nope/")

    assert r.status_code == 404
    assert r.json()["code"] == "unknown_template"


@pytest.mark.django_db
def test_start_sets_an_httponly_strict_cookie_and_stores_only_its_hash(client):
    from apps.tenants.models import Tenant, TrySession

    r = _start(client, "shop_support")

    assert r.status_code == 201
    body = r.json()
    assert body["template"] == "shop_support" and body["turns_remaining"] == 20
    morsel = r.cookies[COOKIE]
    raw = morsel.value
    assert len(raw) >= 40
    assert morsel["httponly"] and morsel["samesite"] == "Strict" and morsel["path"] == f"{BASE}/"
    assert int(morsel["max-age"]) == 24 * 3600
    session = TrySession.objects.get()
    assert session.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in json.dumps(list(TrySession.objects.values()), default=str)
    tenant = Tenant.objects.get(pk=session.tenant_id)
    assert tenant.kind == "try" and str(session.flow_id) == body["flow_id"]


@pytest.mark.django_db
def test_unknown_template_is_404_and_spends_no_ip_quota(client, settings):
    settings.TRY_SESSIONS_PER_IP_PER_DAY = 1

    assert _start(client, "nope").status_code == 404
    assert _start(client, "nope").json()["code"] == "unknown_template"
    assert _start(Client(), "shop_support").status_code == 201


@pytest.mark.django_db
def test_session_reads_back_its_own_copy_with_the_editor_shape():
    c, s = _session()

    me = c.get(f"{BASE}/session/")
    assert me.status_code == 200 and me.json()["flow_id"] == s["flow_id"]
    detail = c.get(f"{BASE}/flows/{s['flow_id']}/")
    assert detail.status_code == 200
    body = detail.json()
    assert set(body) >= {"flow", "draft", "versions"} and body["flow"]["id"] == s["flow_id"]
    assert body["draft"]["graph"]["nodes"]
    assert c.get(f"{BASE}/node-schema/").status_code == 200


# ── 접근 거부: 쿠키 없음·위조·다른 세션 ──────────────────────────────────────

@pytest.mark.django_db
def test_missing_or_forged_cookie_is_refused():
    _, s = _session()

    anon = Client()
    r = anon.get(f"{BASE}/flows/{s['flow_id']}/")
    assert r.status_code == 401 and r.json()["code"] == "try_no_session"
    assert anon.get(f"{BASE}/session/").status_code == 401

    forged = Client()
    forged.cookies[COOKIE] = "x" * 43
    assert forged.get(f"{BASE}/flows/{s['flow_id']}/").status_code == 401
    r = forged.put(
        f"{BASE}/flows/{s['flow_id']}/draft/", json.dumps({"graph": {}}), content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )
    assert r.status_code == 401


@pytest.mark.django_db
def test_another_sessions_copy_is_not_reachable():
    a, sa = _session(ip="198.51.100.1")
    b, sb = _session(ip="198.51.100.2")

    assert a.get(f"{BASE}/flows/{sb['flow_id']}/").status_code == 404
    r = a.put(
        f"{BASE}/flows/{sb['flow_id']}/draft/", json.dumps({"graph": {"nodes": []}}),
        content_type="application/json", HTTP_ORIGIN=ORIGIN,
    )
    assert r.status_code == 404
    assert _turn(a, sb["flow_id"]).status_code == 404
    # 편집기(운영자) API는 쿠키로 닿지 않는다
    assert a.get(f"/api/tenant/flows/{sa['flow_id']}/").status_code == 401


@pytest.mark.django_db
def test_expired_session_is_gone_after_prune():
    from apps.tenants import try_tenant

    c, s = _session()
    try_tenant.prune_expired(now=timezone.now() + dt.timedelta(hours=25))

    r = c.get(f"{BASE}/flows/{s['flow_id']}/")
    assert r.status_code == 401 and r.json()["code"] == "try_no_session"


# ── 초안 저장·배포 거부·Origin ───────────────────────────────────────────────

@pytest.mark.django_db
def test_draft_save_and_patch_use_the_editor_paths():
    c, s = _session()
    graph = c.get(f"{BASE}/flows/{s['flow_id']}/").json()["draft"]["graph"]
    graph["nodes"][0]["name"] = "고친 시작"

    r = c.put(
        f"{BASE}/flows/{s['flow_id']}/draft/", json.dumps({"graph": graph}), content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )
    assert r.status_code == 200 and r.json()["graph"]["nodes"][0]["name"] == "고친 시작"
    r = c.patch(
        f"{BASE}/flows/{s['flow_id']}/draft/", json.dumps({"ops": [{"op": "delete_node", "id": "없는노드"}]}),
        content_type="application/json", HTTP_ORIGIN=ORIGIN,
    )
    assert r.status_code == 422
    assert c.get(f"{BASE}/flows/{s['flow_id']}/").json()["draft"]["graph"]["nodes"][0]["name"] == "고친 시작"


@pytest.mark.django_db
def test_deploy_is_refused():
    c, s = _session()

    r = c.post(f"{BASE}/flows/{s['flow_id']}/deploy/", "{}", content_type="application/json", HTTP_ORIGIN=ORIGIN)
    assert r.status_code == 403 and r.json()["code"] == "try_restricted"


@pytest.mark.django_db
def test_writes_with_a_wrong_or_missing_origin_are_refused(client):
    r = _start(client, HTTP_ORIGIN="https://evil.example")
    assert r.status_code == 403 and r.json()["code"] == "try_bad_origin"
    c, s = _session()
    for origin in ("https://evil.example", None):
        extra = {"HTTP_ORIGIN": origin} if origin else {}
        r = c.put(
            f"{BASE}/flows/{s['flow_id']}/draft/", json.dumps({"graph": {}}), content_type="application/json", **extra
        )
        assert r.status_code == 403 and r.json()["code"] == "try_bad_origin"
        r = _turn(c, s["flow_id"], origin=origin)
        assert r.status_code == 403 and r.json()["code"] == "try_bad_origin"


@pytest.mark.django_db
def test_configured_origin_is_allowed(settings):
    settings.TRY_ALLOWED_ORIGINS = ["https://sandcorp.co.kr"]
    c = Client()
    r = c.post(
        f"{BASE}/sessions/", json.dumps({"template": "shop_support"}), content_type="application/json",
        HTTP_ORIGIN="https://sandcorp.co.kr",
    )
    assert r.status_code == 201


# ── 세션당 20턴 ──────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
def test_twentieth_turn_runs_and_twenty_first_is_refused(prod_platform, fake_chat_llm):
    c, s = _session()
    assert s["turns_remaining"] == 20
    _set_turns(s["flow_id"], 1)  # 19턴을 쓴 뒤

    events = sse_events(_turn(c, s["flow_id"]))
    done = dict(events)["done"]
    assert done["ran"] is True and "reply" in dict(events)
    assert c.get(f"{BASE}/session/").json()["turns_remaining"] == 0

    fake_chat_llm.override = _no_llm
    done = dict(sse_events(_turn(c, s["flow_id"])))["done"]
    assert done["ran"] is False and done["code"] == "try_turns_exhausted"


def _no_llm(messages):
    raise AssertionError("LLM을 부르면 안 된다")


# ── IP당 하루 3세션 ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_fourth_session_from_one_ip_is_refused_but_other_ips_are_not():
    for _ in range(3):
        _session(ip="192.0.2.10")

    r = _start(Client(), ip="192.0.2.10")
    assert r.status_code == 429 and r.json()["code"] == "try_ip_limit"
    assert _start(Client(), ip="192.0.2.11").status_code == 201


@pytest.mark.django_db
def test_forged_forwarded_for_does_not_bypass_the_ip_limit(prod_platform):
    # 프록시 1단: XFF의 맨 끝(우리 프록시가 덧붙인 값)만 믿는다 — 앞쪽은 클라이언트가 마음대로 쓴다.
    for n in range(3):
        assert _start(Client(), HTTP_X_FORWARDED_FOR=f"10.0.0.{n}, 192.0.2.50").status_code == 201

    r = _start(Client(), HTTP_X_FORWARDED_FOR="10.9.9.9, 192.0.2.50")
    assert r.status_code == 429


@pytest.mark.django_db
def test_ip_limit_resets_on_the_next_seoul_day():
    from apps.tenants import try_session

    seoul_2359 = dt.datetime(2026, 10, 25, 14, 59, tzinfo=dt.timezone.utc)  # 23:59 KST
    for _ in range(3):
        assert try_session.admit_ip("192.0.2.77", now=seoul_2359)
    assert not try_session.admit_ip("192.0.2.77", now=seoul_2359)
    assert try_session.admit_ip("192.0.2.77", now=seoul_2359 + dt.timedelta(minutes=2))  # 00:01 KST


@pytest.mark.django_db(transaction=True)
def test_concurrent_session_starts_never_exceed_the_ip_limit():
    from apps.tenants.models import TrySession

    codes: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        from django.db import connection

        try:
            code = _start(Client(), "shop_support", ip="192.0.2.99").status_code
            with lock:
                codes.append(code)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [201, 201, 201] + [429] * 5
    assert TrySession.objects.count() == 3


# ── 전체 하루 비용 $5 ────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
def test_turn_just_under_the_budget_runs_and_adds_its_cost_then_the_next_is_refused(
    prod_platform, fake_chat_llm, settings
):
    from apps.tenants import try_session
    from apps.usage.pricing import cost_usd

    settings.TRY_DAILY_BUDGET_USD = Decimal("5")
    c, s = _session()
    _answer_only(c, s["flow_id"])
    try_session.add_spend(Decimal("4.999999"))

    done = dict(sse_events(_turn(c, s["flow_id"])))["done"]
    assert done["ran"] is True
    one_turn = cost_usd("gpt-4o-mini", 120, 30)
    assert one_turn and try_session.spent_today() >= Decimal("4.999999") + one_turn  # 한 턴만큼 넘을 수 있다

    fake_chat_llm.override = _no_llm
    remaining_before = c.get(f"{BASE}/session/").json()["turns_remaining"]
    done = dict(sse_events(_turn(c, s["flow_id"])))["done"]
    assert done["ran"] is False and done["code"] == "try_daily_budget"
    assert "내일" in done["note"]
    assert c.get(f"{BASE}/session/").json()["turns_remaining"] == remaining_before  # 턴도 쓰지 않는다
    # 새 세션도 시험 대화는 막힌다(만들기는 된다 — 편집은 비용이 없다)
    c2, s2 = _session(ip="198.51.100.200")
    assert dict(sse_events(_turn(c2, s2["flow_id"])))["done"]["code"] == "try_daily_budget"


@pytest.mark.django_db(transaction=True)
def test_spend_survives_the_try_tenant_prune(prod_platform, fake_chat_llm):
    from apps.tenants import try_session, try_tenant

    c, s = _session()
    _answer_only(c, s["flow_id"])
    assert dict(sse_events(_turn(c, s["flow_id"])))["done"]["ran"] is True
    spent = try_session.spent_today()
    assert spent > 0

    try_tenant.prune_expired(now=timezone.now() + dt.timedelta(hours=25))
    assert try_session.spent_today() == spent


@pytest.mark.django_db
def test_spend_day_follows_seoul_time():
    from apps.tenants import try_session

    seoul_2359 = dt.datetime(2026, 10, 25, 14, 59, tzinfo=dt.timezone.utc)
    try_session.add_spend(Decimal("3"), now=seoul_2359)

    assert try_session.spent_today(now=seoul_2359) == Decimal("3")
    assert try_session.spent_today(now=seoul_2359 + dt.timedelta(minutes=2)) == Decimal("0")


@pytest.mark.django_db(transaction=True)
def test_concurrent_spend_adds_are_not_lost():
    from apps.tenants import try_session

    def worker() -> None:
        from django.db import connection

        try:
            try_session.add_spend(Decimal("0.01"))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert try_session.spent_today() == Decimal("0.10")
