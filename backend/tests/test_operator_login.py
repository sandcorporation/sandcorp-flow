"""관리 화면 Operator 로그인 — sandcorp-auth 로그인 콜백 → Flow 세션(ADR-0028·ADR-0013, #21).

인가 서버(`/authorize`·`/token`)·JWKS·시계만 결정적 대역이다. 토큰은 실제 Ed25519로 서명하고,
Ninja 엔드포인트·검증기·세션(Refresh Family)·DB는 진짜다.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from apps.tenants import operator_login, resource_token
from operator_login_fakes import (
    AUTHORIZE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    TOKEN_URL,
    FakeAuthorizationServer,
)
from resource_token_fakes import (
    FLOW,
    ISSUER,
    MEMBER_SUB,
    OWNER,
    FakeJwksEndpoint,
    FrozenClock,
    Signer,
    make_validator,
    new_signer,
    public_jwk,
)

ADMIN_HOME = "/admin-ui/operator"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def signer(clock: FrozenClock) -> Signer:
    return new_signer(clock)


@pytest.fixture
def server(signer: Signer):
    fake = FakeAuthorizationServer(signer)
    operator_login.use_token_endpoint(fake)
    yield fake
    operator_login.use_token_endpoint(None)


@pytest.fixture(autouse=True)
def flow_client(settings, signer: Signer, clock: FrozenClock):
    settings.AUTH_CLIENT_ID = CLIENT_ID
    settings.AUTH_CLIENT_SECRET = CLIENT_SECRET
    settings.AUTH_REDIRECT_URI = REDIRECT_URI
    settings.AUTH_AUTHORIZE_URL = AUTHORIZE_URL
    settings.AUTH_TOKEN_URL = TOKEN_URL
    settings.AUTH_RESOURCE = FLOW
    settings.AUTH_LOGIN_REDIRECT = ADMIN_HOME
    endpoint = FakeJwksEndpoint([public_jwk(signer.private_key, signer.kid)])
    resource_token.use_validator(make_validator(endpoint, clock))
    yield
    resource_token.use_validator(None)


def start(client: Client) -> str:
    response = client.get("/api/operator/auth/authorize")
    assert response.status_code == 302
    return response["Location"]


def callback(client: Client, query: dict[str, str]):
    return client.get("/api/operator/auth/callback", query)


def rejected_with(response) -> str | None:
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.path == ADMIN_HOME
    return parse_qs(location.query).get("login_error", [None])[0]


def refresh_access(client: Client):
    return client.post("/api/operator/auth/refresh")


# ── 시작: /authorize로 보낸다 ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_authorize_redirects_to_sandcorp_auth_with_pkce_state_and_nonce() -> None:
    client = Client()
    response = client.get("/api/operator/auth/authorize")

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == AUTHORIZE_URL
    params = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["scope"] == "openid profile email login"
    assert params["resource"] == FLOW
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43  # base64url(SHA-256), 패딩 없음
    assert params["state"] and params["nonce"]
    assert CLIENT_SECRET not in response["Location"]

    attempt = response.cookies["op_login"]
    assert attempt["httponly"]
    assert attempt["samesite"].lower() == "lax"  # 인가 서버에서 돌아오는 교차 사이트 GET에 실린다
    assert attempt["path"] == "/api/operator/auth/callback"


@pytest.mark.django_db
def test_each_login_attempt_gets_fresh_state_and_verifier() -> None:
    first, second = start(Client()), start(Client())

    assert parse_qs(urlsplit(first).query)["state"] != parse_qs(urlsplit(second).query)["state"]
    assert (
        parse_qs(urlsplit(first).query)["code_challenge"]
        != parse_qs(urlsplit(second).query)["code_challenge"]
    )


@pytest.mark.django_db
def test_login_is_refused_until_the_client_is_registered(settings) -> None:
    settings.AUTH_CLIENT_SECRET = ""

    response = Client().get("/api/operator/auth/authorize")

    assert rejected_with(response) == "config"


# ── 콜백 전체 흐름: 대표 + admin → Flow 세션 ───────────────────────────────────


@pytest.mark.django_db
def test_owner_with_admin_gets_a_flow_session(server: FakeAuthorizationServer) -> None:
    from apps.tenants.models import Operator, RefreshToken, User

    client = Client()
    response = callback(client, server.approve(start(client)))

    assert rejected_with(response) is None  # 관리 화면으로 돌아간다
    refresh = response.cookies["op_refresh"]
    assert refresh.value and refresh["httponly"]
    assert refresh["samesite"].lower() == "strict"
    assert response.cookies["op_login"].value == ""  # 시도 쿠키는 지운다

    user = User.objects.get(iss=ISSUER, sub=OWNER)
    assert user.acct == "owner"
    assert (user.name, user.email, user.email_verified) == ("대표", "owner@sandcorp.co.kr", True)
    operator = Operator.objects.get(user=user)
    assert not operator.has_usable_password()
    # sandcorp-auth 토큰은 저장하지 않는다 — Flow 자기 Refresh Family 한 줄만 생긴다
    assert RefreshToken.objects.filter(operator=operator).count() == 1


@pytest.mark.django_db
def test_session_opens_the_operator_api(server: FakeAuthorizationServer) -> None:
    client = Client()
    callback(client, server.approve(start(client)))

    refreshed = refresh_access(client)
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]

    tenants = Client().get("/api/operator/tenants/", HTTP_AUTHORIZATION=f"Bearer {access}")
    assert tenants.status_code == 200


@pytest.mark.django_db
def test_logging_in_again_reuses_the_same_user_and_refreshes_the_profile(
    server: FakeAuthorizationServer,
) -> None:
    from apps.tenants.models import Operator, User

    first = Client()
    callback(first, server.approve(start(first)))
    second = Client()
    callback(
        second,
        server.approve(start(second), id_token_overrides={"name": "새 이름", "email": None}),
    )

    user = User.objects.get(iss=ISSUER, sub=OWNER)
    assert (user.name, user.email) == ("새 이름", None)
    assert User.objects.count() == 1
    assert Operator.objects.count() == 1


@pytest.mark.django_db
def test_logout_ends_the_flow_session(server: FakeAuthorizationServer) -> None:
    client = Client()
    callback(client, server.approve(start(client)))

    out = client.post("/api/operator/auth/logout")

    assert out.status_code == 200
    assert refresh_access(client).status_code == 401


# ── 거절: Operator 세션은 대표 + admin일 때만 ──────────────────────────────────


@pytest.mark.django_db
def test_owner_without_admin_scope_is_refused(server: FakeAuthorizationServer) -> None:
    from apps.tenants.models import Operator

    client = Client()
    response = callback(client, server.approve(start(client), flow_scope="login"))

    assert rejected_with(response) == "admin"
    assert "op_refresh" not in response.cookies
    assert not Operator.objects.exists()


@pytest.mark.django_db
def test_member_is_refused_even_though_the_token_is_valid(server: FakeAuthorizationServer) -> None:
    from apps.tenants.models import Operator, User

    client = Client()
    response = callback(client, server.approve_member(start(client)))

    assert rejected_with(response) == "member"
    assert "op_refresh" not in response.cookies
    assert not User.objects.filter(sub=MEMBER_SUB).exists()  # 일반 사용자 로그인은 #40
    assert not Operator.objects.exists()


@pytest.mark.django_db
def test_member_cannot_pretend_with_admin_scope(server: FakeAuthorizationServer) -> None:
    client = Client()
    query = server.approve(start(client), sub=MEMBER_SUB, acct="member", flow_scope="admin login")

    assert rejected_with(callback(client, query)) == "member"


@pytest.mark.django_db
def test_someone_else_without_acct_is_not_the_owner(server: FakeAuthorizationServer) -> None:
    client = Client()
    query = server.approve(start(client), sub="not-the-owner")

    assert rejected_with(callback(client, query)) == "token"


@pytest.mark.django_db
def test_token_without_login_scope_is_refused(server: FakeAuthorizationServer) -> None:
    client = Client()
    query = server.approve(start(client), flow_scope="admin")

    assert rejected_with(callback(client, query)) == "token"


@pytest.mark.django_db
def test_denied_consent_comes_back_as_a_refusal(server: FakeAuthorizationServer) -> None:
    client = Client()
    state = parse_qs(urlsplit(start(client)).query)["state"][0]

    response = callback(client, {"error": "access_denied", "state": state})

    assert rejected_with(response) == "denied"
    assert server.exchanges == 0


# ── state·PKCE·nonce ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_state_mismatch_is_refused_without_exchanging_the_code(
    server: FakeAuthorizationServer,
) -> None:
    client = Client()
    query = server.approve(start(client))

    response = callback(client, {**query, "state": "forged"})

    assert rejected_with(response) == "state"
    assert server.exchanges == 0


@pytest.mark.django_db
def test_callback_without_the_attempt_cookie_is_refused(server: FakeAuthorizationServer) -> None:
    query = server.approve(start(Client()))

    assert rejected_with(callback(Client(), query)) == "state"  # 다른 브라우저로 넘어온 콜백


@pytest.mark.django_db
def test_tampered_attempt_cookie_is_refused(server: FakeAuthorizationServer) -> None:
    client = Client()
    query = server.approve(start(client))
    client.cookies["op_login"] = client.cookies["op_login"].value + "x"

    assert rejected_with(callback(client, query)) == "state"


@pytest.mark.django_db
def test_expired_attempt_is_refused(server: FakeAuthorizationServer, monkeypatch) -> None:
    client = Client()
    query = server.approve(start(client))
    monkeypatch.setattr(operator_login, "ATTEMPT_MAX_AGE_SECONDS", -1)

    assert rejected_with(callback(client, query)) == "state"


@pytest.mark.django_db
def test_code_injected_into_another_attempt_fails_pkce(server: FakeAuthorizationServer) -> None:
    """공격자가 자기 로그인의 코드를 피해자의 콜백(피해자 state)에 끼워 넣는 경우."""
    attacker = Client()
    stolen = server.approve(start(attacker))
    victim = Client()
    victim_state = parse_qs(urlsplit(start(victim)).query)["state"][0]

    response = callback(victim, {"code": stolen["code"], "state": victim_state})

    assert rejected_with(response) == "exchange"
    assert "op_refresh" not in response.cookies


@pytest.mark.django_db
def test_code_cannot_be_used_twice(server: FakeAuthorizationServer) -> None:
    client = Client()
    url = start(client)
    attempt_cookie_before = client.cookies["op_login"].value
    query = server.approve(url)
    callback(client, query)
    client.cookies["op_login"] = attempt_cookie_before

    assert rejected_with(callback(client, query)) == "exchange"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": "replayed"},
        {"nonce": None},
        {"aud": "another-client"},
        {"iss": "https://evil.example"},
        {"sub": "someone-else"},
        {"exp": 1},
    ],
    ids=["nonce-mismatch", "nonce-missing", "aud", "iss", "sub-mismatch", "expired"],
)
def test_bad_id_token_is_refused(server: FakeAuthorizationServer, overrides) -> None:
    client = Client()
    query = server.approve(start(client), id_token_overrides=overrides)

    assert rejected_with(callback(client, query)) == "token"


@pytest.mark.django_db
def test_wrong_client_secret_fails_the_exchange(server: FakeAuthorizationServer, settings) -> None:
    client = Client()
    url = start(client)
    settings.AUTH_CLIENT_SECRET = "wrong"

    assert rejected_with(callback(client, server.approve(url))) == "exchange"


# ── 비밀번호 로그인은 없다 ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_password_login_endpoint_is_gone() -> None:
    from apps.tenants.models import Operator

    Operator.objects.create_user(username="admin", password="pw123456")

    response = Client().post(
        "/api/operator/auth/login",
        {"username": "admin", "password": "pw123456"},
        content_type="application/json",
    )

    assert response.status_code in (404, 405)


@pytest.mark.django_db
def test_password_era_operator_jwt_is_refused() -> None:
    from datetime import datetime, timedelta, timezone

    from django.conf import settings as django_settings
    from jose import jwt

    from apps.tenants.models import Operator

    op = Operator.objects.create_user(username="admin", password="pw123456")
    old = jwt.encode(
        {"sub": str(op.id), "type": "operator", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        django_settings.JWT_SIGNING_KEY,
        algorithm="HS256",
    )

    response = Client().get("/api/operator/tenants/", HTTP_AUTHORIZATION=f"Bearer {old}")

    assert response.status_code == 401


@pytest.mark.django_db
def test_session_token_for_an_operator_without_identity_is_refused() -> None:
    from apps.tenants.auth import create_operator_access_token
    from apps.tenants.models import Operator

    op = Operator.objects.create_user(username="legacy", password="pw123456")

    response = Client().get(
        "/api/operator/tenants/",
        HTTP_AUTHORIZATION=f"Bearer {create_operator_access_token(op)}",
    )

    assert response.status_code == 401


# ── e2e 시드(비밀번호 로그인 대신) ────────────────────────────────────────────


@pytest.mark.django_db
def test_e2e_seed_creates_an_owner_operator_whose_session_opens_the_api(tmp_path) -> None:
    from django.core.management import call_command

    from apps.tenants.models import Operator

    token_file = tmp_path / "shared" / "operator_token"
    call_command("seed_e2e_operator", "--sub", "e2e-owner", "--token-file", str(token_file))
    call_command("seed_e2e_operator", "--sub", "e2e-owner", "--token-file", str(token_file))

    assert Operator.objects.filter(user__sub="e2e-owner", user__acct="owner").count() == 1
    response = Client().get(
        "/api/operator/tenants/", HTTP_AUTHORIZATION=f"Bearer {token_file.read_text()}"
    )
    assert response.status_code == 200
