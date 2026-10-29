"""「이 흐름으로 가입」 — 체험 사본을 내 테넌트로 옮기기(#42, ADR-0030).

소유권 증명은 둘 다: 체험 세션 쿠키(``sf_try``, 서버엔 해시) + 로그인한 고객의 Bearer(멤버 세션). 옮길 곳은 그
Bearer의 테넌트이고 Admin(``flow.edit``)이어야 한다. 새 가입자는 기존 온보딩(``/api/auth/member/tenants``)으로
테넌트를 만든 뒤 그 세션으로 옮긴다. 옮기면 사본이 새 흐름(초안, 배포 안 함)으로 들어가고 체험 테넌트·세션은 지워진다.

sandcorp-auth만 토큰 엔드포인트 대역(``FlowAuthServer``). DB·쿠키·Ninja 엔드포인트·서비스는 실제다.
"""
from __future__ import annotations

import datetime as dt
import json
import threading

import pytest
from django.test import Client
from django.utils import timezone

from authkit_fakes import FlowAuthServer
from authkit_fakes import MEMBER_SUB
from test_member_login import landing, login, new_tenant
from test_operator_login import clock, flow_client, server, signer  # noqa: F401 — 픽스처 재사용
from tests.utils import make_membership, member_token

BASE = "/api/try"
ORIGIN = "http://testserver"
CLAIM_LANDING = "/admin-ui/try/claim"


@pytest.fixture(autouse=True)
def _landings(settings):
    settings.AUTH_MEMBER_REDIRECT = "/admin-ui/tenant"
    settings.AUTH_TRY_CLAIM_REDIRECT = CLAIM_LANDING


@pytest.fixture(autouse=True)
def _clear_onboarding_ratelimit():
    from apps.chat.sse import get_redis_client

    r = get_redis_client()
    for key in r.scan_iter("ratelimit:onboarding:*"):
        r.delete(key)


def _try(client: Client, template: str = "shop_support", ip: str = "203.0.113.42") -> dict:
    r = client.post(f"{BASE}/sessions/", json.dumps({"template": template}), content_type="application/json",
                    HTTP_ORIGIN=ORIGIN, REMOTE_ADDR=ip)
    assert r.status_code == 201, r.content
    return r.json()


def _claim(client: Client, token: str | None, origin: str | None = ORIGIN):
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    return client.post(f"{BASE}/claim/", "{}", content_type="application/json", headers=headers)


def _admin(name: str = "Acme"):
    tenant = new_tenant(name)
    return tenant, member_token(make_membership(tenant, "admin", sub=MEMBER_SUB))


def _flows(tenant_id) -> list:
    from apps.flows.models import Flow

    return list(Flow.objects.filter(tenant_id=tenant_id).select_related("draft").order_by("created_at"))


def _try_gone(started: dict) -> bool:
    from apps.tenants.models import Tenant, TrySession

    return not TrySession.objects.filter(flow_id=started["flow_id"]).exists() and not Tenant.objects.filter(
        flows__id=started["flow_id"]
    ).exists()


# ── 새 가입자: 로그인 → 온보딩 → 옮기기 ─────────────────────────────────────

@pytest.mark.django_db
def test_new_signup_logs_in_makes_a_tenant_and_gets_the_copy_as_a_new_draft_flow(server) -> None:
    from apps.flows.defaults import DEFAULT_FLOW_NAME
    from apps.tenants.models import Tenant

    browser = Client()
    started = _try(browser)
    edited = browser.get(f"{BASE}/flows/{started['flow_id']}/").json()["draft"]["graph"]
    edited["nodes"][0]["name"] = "내가 고친 시작"
    assert browser.put(f"{BASE}/flows/{started['flow_id']}/draft/", json.dumps({"graph": edited}),
                       content_type="application/json", HTTP_ORIGIN=ORIGIN).status_code == 200

    # 「이 흐름으로 가입」 → 고객 로그인(옮기기 표시) → 콜백은 언제나 표(ticket)를 주고 옮기기 화면으로
    path, params = landing(login(browser, server, start_url="/api/auth/authorize/member?then=try_claim"))
    assert path == CLAIM_LANDING and params == {}
    assert browser.get("/api/auth/member/tenants").json()["tenants"] == []

    made = browser.post("/api/auth/member/tenants", {"name": "새 가게", "slug": "new-shop"},
                        content_type="application/json")
    assert made.status_code == 201, made.content
    r = _claim(browser, made.json()["access_token"])

    assert r.status_code == 201, r.content
    body = r.json()
    tenant = Tenant.objects.get(slug="new-shop")
    assert body["tenant_id"] == str(tenant.id)
    flows = _flows(tenant.id)
    assert [f.name for f in flows] == [DEFAULT_FLOW_NAME, "쇼핑몰 배송·반품 상담"]  # 기본 흐름은 그대로, 사본은 옆에
    claimed = flows[1]
    assert body["flow_id"] == str(claimed.id)
    assert claimed.draft.graph == edited  # 노드 id·고친 내용 그대로
    assert claimed.deployed_version_id is None and not claimed.is_public  # 초안만, 배포 안 함
    assert flows[0].is_public  # 위젯은 계속 기본 흐름
    assert _try_gone(started)
    assert browser.cookies["sf_try"].value == ""  # 체험 쿠키도 지웠다
    assert tenant.trial_turns_remaining == 200  # 새 테넌트 Trial Quota는 기본 그대로


@pytest.mark.django_db
def test_claim_login_hands_a_ticket_even_with_one_membership(server) -> None:
    """옮기기 로그인은 테넌트 하나뿐이어도 바로 세션을 열지 않는다 — 「새 테넌트 / 기존에 추가」를 고르게."""
    tenant = new_tenant("Solo")
    make_membership(tenant, "admin", sub=MEMBER_SUB)
    browser = Client()

    path, params = landing(login(browser, server, start_url="/api/auth/authorize/member?then=try_claim"))

    assert path == CLAIM_LANDING and params == {}
    assert "member_refresh" not in browser.cookies
    tenants = browser.get("/api/auth/member/tenants").json()["tenants"]
    assert [(t["tenant_name"], t["role"]) for t in tenants] == [("Solo", "admin")]


@pytest.mark.django_db
def test_unknown_then_is_ignored_and_the_normal_landing_happens(server) -> None:
    path, params = landing(login(Client(), server, start_url="/api/auth/authorize/member?then=https://evil.example"))
    assert path == "/admin-ui/tenant" and params == {"onboarding": "1"}


# ── 기존 Admin 테넌트에 추가 ────────────────────────────────────────────────

@pytest.mark.django_db
def test_existing_admin_tenant_gets_the_copy_next_to_its_flows_with_a_free_name() -> None:
    from apps.flows import services

    tenant, token = _admin()
    services.create_flow(tenant.id, "쇼핑몰 배송·반품 상담")  # 같은 이름이 이미 있다
    browser = Client()
    started = _try(browser)

    r = _claim(browser, token)

    assert r.status_code == 201, r.content
    names = [f.name for f in _flows(tenant.id)]
    assert names == ["쇼핑몰 배송·반품 상담", "쇼핑몰 배송·반품 상담 (2)"]
    assert _try_gone(started)


@pytest.mark.django_db
def test_member_only_tenant_is_refused_and_the_try_session_stays() -> None:
    tenant = new_tenant("Viewer Co")
    token = member_token(make_membership(tenant, "member", sub=MEMBER_SUB))
    browser = Client()
    started = _try(browser)

    r = _claim(browser, token)

    assert r.status_code == 403 and r.json()["code"] == "try_claim_forbidden"
    assert _flows(tenant.id) == []
    assert not _try_gone(started)
    assert browser.get(f"{BASE}/session/").status_code == 200


# ── 소유권: 쿠키 + 로그인 둘 다 ─────────────────────────────────────────────

@pytest.mark.django_db
def test_without_login_or_cookie_or_from_another_origin_nothing_moves() -> None:
    tenant, token = _admin()
    browser = Client()
    started = _try(browser)

    no_login = _claim(browser, None)
    no_cookie = _claim(Client(), token)
    forged = Client()
    forged.cookies["sf_try"] = "made-up"
    forged_cookie = _claim(forged, token)
    bad_origin = _claim(browser, token, origin="https://evil.example")

    assert (no_login.status_code, no_login.json()["code"]) == (401, "try_login_required")
    assert (no_cookie.status_code, no_cookie.json()["code"]) == (401, "try_no_session")
    assert forged_cookie.json()["code"] == "try_no_session"
    assert (bad_origin.status_code, bad_origin.json()["code"]) == (403, "try_bad_origin")
    assert _flows(tenant.id) == [] and not _try_gone(started)


@pytest.mark.django_db
def test_a_cookie_claims_only_its_own_session() -> None:
    tenant, token = _admin()
    mine, theirs = Client(), Client()
    my_start = _try(mine, "shop_support", ip="203.0.113.50")
    their_start = _try(theirs, "academy_reservation", ip="203.0.113.51")

    assert _claim(mine, token).status_code == 201

    assert [f.name for f in _flows(tenant.id)] == ["쇼핑몰 배송·반품 상담"]
    assert _try_gone(my_start) and not _try_gone(their_start)
    assert theirs.get(f"{BASE}/session/").status_code == 200


@pytest.mark.django_db
def test_expired_session_cannot_be_claimed() -> None:
    from apps.tenants.models import Tenant

    tenant, token = _admin()
    browser = Client()
    started = _try(browser)
    Tenant.objects.filter(flows__id=started["flow_id"]).update(try_expires_at=timezone.now() - dt.timedelta(seconds=1))

    r = _claim(browser, token)

    assert (r.status_code, r.json()["code"]) == (401, "try_no_session")
    assert _flows(tenant.id) == []


@pytest.mark.django_db
def test_claiming_twice_moves_once() -> None:
    tenant, token = _admin()
    browser = Client()
    _try(browser)
    cookie = browser.cookies["sf_try"].value

    assert _claim(browser, token).status_code == 201
    replay = Client()
    replay.cookies["sf_try"] = cookie  # 지워진 쿠키를 되살려 다시 보내도
    again = _claim(replay, token)

    assert (again.status_code, again.json()["code"]) == (401, "try_no_session")
    assert len(_flows(tenant.id)) == 1


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_claims_of_one_session_move_it_once() -> None:
    tenant_a, token_a = _admin("Alpha")
    tenant_b = new_tenant("Beta")
    from apps.tenants.models import Membership, User

    token_b = member_token(
        Membership.objects.create(user=User.objects.get(sub=MEMBER_SUB), tenant=tenant_b, role="admin")
    )
    starter = Client()
    _try(starter)
    cookie = starter.cookies["sf_try"].value
    codes: list[int] = []
    lock = threading.Lock()
    gate = threading.Barrier(2)

    def worker(token: str) -> None:
        from django.db import connection

        try:
            c = Client()
            c.cookies["sf_try"] = cookie
            gate.wait()
            code = _claim(c, token).status_code
            with lock:
                codes.append(code)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(t,)) for t in (token_a, token_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [201, 401]
    assert len(_flows(tenant_a.id)) + len(_flows(tenant_b.id)) == 1


# ── 무엇을 옮기나: 지식 노드·가짜 API는 그대로, 도서관 문서는 안 옮김 ────────

@pytest.mark.django_db
def test_knowledge_nodes_and_fake_api_urls_move_as_they_are_but_library_documents_do_not() -> None:
    from apps.flows.demo_api import HOST
    from apps.rag.models import Document
    from apps.tenants import try_tenant

    try_tenant.ensure_library()
    tenant, token = _admin()
    browser = Client()
    started = _try(browser, "shop_support")
    original = browser.get(f"{BASE}/flows/{started['flow_id']}/").json()["draft"]["graph"]

    assert _claim(browser, token).status_code == 201

    graph = _flows(tenant.id)[0].draft.graph
    kinds = {n["id"]: n for n in graph["nodes"]}
    assert kinds["policy"]["kind"] == "knowledge_search" and kinds["policy"]["config"] == {"top_k": 4}
    assert kinds["order"]["config"]["url"].startswith(f"https://{HOST}/")
    assert graph == original
    assert not Document.objects.filter(tenant_id=tenant.id).exists()
    assert try_tenant.knowledge_scope(tenant.id).store_tenant_id == str(tenant.id)  # 이제 자기 지식만 본다
