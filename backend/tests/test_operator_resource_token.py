"""Operator API를 sandcorp-auth Resource Token으로 여는 시나리오(ADR-0028, #20).

Operator = A1 통과(`acct` 없음, sub == AUTH_OWNER_SUB) + `admin` 스코프. 일반 사용자 토큰은
검증을 통과해도 403. 옛 Operator JWT 경로는 그대로 열린다(#21에서 걷어냈다).
JWKS·시계만 결정적 대역이고, 나머지(검증기·Ninja·DB)는 진짜다.
"""

import pytest
from django.test import Client

from apps.tenants import resource_token
from resource_token_fakes import (
    MEMBER_SUB,
    OWNER,
    FakeJwksEndpoint,
    FrozenClock,
    Signer,
    make_validator,
    new_signer,
    public_jwk,
)

META = "https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource"
NO_TOKEN = f'Bearer resource_metadata="{META}"'
INVALID = f'Bearer resource_metadata="{META}", error="invalid_token"'
NEED_ADMIN = 'Bearer error="insufficient_scope", scope="admin"'


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def signer(clock: FrozenClock) -> Signer:
    return new_signer(clock)


@pytest.fixture
def endpoint(signer: Signer) -> FakeJwksEndpoint:
    return FakeJwksEndpoint([public_jwk(signer.private_key, signer.kid)])


@pytest.fixture(autouse=True)
def flow_validator(endpoint: FakeJwksEndpoint, clock: FrozenClock):
    resource_token.use_validator(make_validator(endpoint, clock))
    yield
    resource_token.use_validator(None)


def get_tenants(token: str | None = None, *, header: str | None = None):
    client = Client()
    if header is not None:
        client.defaults["HTTP_AUTHORIZATION"] = header
    elif token is not None:
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.get("/api/operator/tenants/")


# ── 대표(Owner) + admin ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_owner_admin_token_opens_the_operator_api(signer: Signer) -> None:
    from apps.tenants.models import Operator

    response = get_tenants(signer.token())

    assert response.status_code == 200
    operator = Operator.objects.get(username=OWNER)
    assert not operator.has_usable_password()  # 비밀번호 로그인으로는 들어올 수 없다


@pytest.mark.django_db
def test_owner_can_create_a_tenant_with_a_resource_token(signer: Signer) -> None:
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"bearer {signer.token()}"  # 스킴은 대소문자 무관(B1)

    created = client.post(
        "/api/operator/tenants/", {"name": "Acme"}, content_type="application/json"
    )
    listed = client.get("/api/operator/tenants/")

    assert created.status_code == 201
    assert [t["name"] for t in listed.json()] == ["Acme"]


@pytest.mark.django_db
def test_owner_token_opens_operator_usage(signer: Signer) -> None:
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {signer.token()}"

    assert client.get("/api/operator/usage/").status_code == 200


@pytest.mark.django_db
def test_owner_token_without_admin_scope_is_403(signer: Signer) -> None:
    for scope in ["login", "administrator"]:
        response = get_tenants(signer.token(scope=scope))

        assert response.status_code == 403
        assert response["WWW-Authenticate"] == NEED_ADMIN


# ── 일반 사용자(member) ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_member_token_is_valid_but_not_an_operator(signer: Signer) -> None:
    from apps.tenants.models import Operator

    for scope in ["login", "admin login"]:  # admin을 들고 와도(auth가 주지 않지만) 403
        response = get_tenants(signer.member_token(scope=scope))

        assert response.status_code == 403
        assert response["WWW-Authenticate"] == NEED_ADMIN
    assert not Operator.objects.filter(username=MEMBER_SUB).exists()


@pytest.mark.django_db
def test_member_cannot_create_tenants(signer: Signer) -> None:
    from apps.tenants.models import Tenant

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {signer.member_token(scope='admin login')}"

    response = client.post("/api/operator/tenants/", {"name": "x"}, content_type="application/json")

    assert response.status_code == 403
    assert not Tenant.objects.exists()


# ── 틀린 토큰·토큰 없음(5절 헤더) ─────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": "someone-else"},
        {"acct": "owner"},
        {"sub": MEMBER_SUB},  # acct 없는 member sub = 대표 아님(A1)
        {"aud": "https://flow.sandcorp.co.kr/api/"},
        {"iss": "https://auth.sandcorp.co.kr/"},
    ],
)
def test_wrong_token_is_401_invalid_token(signer: Signer, overrides: dict[str, object]) -> None:
    response = get_tenants(signer.token(**overrides))

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == INVALID
    assert response.json() == {"detail": "Unauthorized"}  # 어느 규칙인지 싣지 않는다


@pytest.mark.django_db
def test_expired_token_is_401_after_the_30_second_leeway(signer: Signer, clock: FrozenClock) -> None:
    token = signer.token()
    clock.advance(630)
    assert get_tenants(token).status_code == 200

    clock.advance(1)
    response = get_tenants(token)
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == INVALID


@pytest.mark.django_db
def test_garbage_token_is_401_invalid_token() -> None:
    response = get_tenants("garbage")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == INVALID


@pytest.mark.django_db
@pytest.mark.parametrize("header", [None, "Basic dXNlcjpwYXNz", "Token abc"])
def test_missing_bearer_token_is_401_without_error(header: str | None, signer: Signer) -> None:
    client = Client()
    if header:
        client.defaults["HTTP_AUTHORIZATION"] = header

    response = client.get("/api/operator/tenants/")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == NO_TOKEN


@pytest.mark.django_db
def test_token_in_query_string_is_ignored(signer: Signer) -> None:
    response = Client().get(f"/api/operator/tenants/?access_token={signer.token()}")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == NO_TOKEN


# ── 옛 Operator JWT 경로는 그대로 ─────────────────────────────────────────────


@pytest.mark.django_db
def test_legacy_operator_jwt_still_opens_the_operator_api(auth_operator: Client) -> None:
    assert auth_operator.get("/api/operator/tenants/").status_code == 200


@pytest.mark.django_db
def test_legacy_token_does_not_fetch_jwks(auth_operator: Client, endpoint: FakeJwksEndpoint) -> None:
    auth_operator.get("/api/operator/tenants/")

    assert endpoint.fetches == 0


# ── 보호 리소스 메타데이터(6절) ───────────────────────────────────────────────


def test_protected_resource_metadata_endpoint() -> None:
    response = Client().get("/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.json() == {
        "resource": "https://flow.sandcorp.co.kr/api",
        "authorization_servers": ["https://auth.sandcorp.co.kr"],
        "scopes_supported": ["admin", "login"],
        "bearer_methods_supported": ["header"],
    }


def test_default_settings_build_the_flow_validator(settings) -> None:
    resource_token.use_validator(None)
    settings.AUTH_OWNER_SUB = OWNER

    validator = resource_token.get_validator()

    assert (validator.issuer, validator.audience, validator.owner_sub) == (
        "https://auth.sandcorp.co.kr",
        "https://flow.sandcorp.co.kr/api",
        OWNER,
    )
    assert settings.AUTH_JWKS_URL == "https://auth.sandcorp.co.kr/.well-known/jwks.json"
