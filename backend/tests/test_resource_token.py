"""sandcorp-auth Resource Token 검증 규칙(B·T1~T10·A1~A3·S1·J1~J5·5절) — ADR-0028 「고객 토큰 검증」.

실제 Ed25519 키로 서명하고, JWKS는 결정적 대역(또는 로컬 HTTP 서버), 시계는 고정한다.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest

from apps.tenants.resource_token import (
    AccessToken,
    InsufficientScope,
    JwksCache,
    JwkSet,
    ResourceTokenValidator,
    TokenRejected,
    http_jwks_fetcher,
    protected_resource_metadata,
    site_origin,
    www_authenticate,
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


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def signer(clock: FrozenClock) -> Signer:
    return new_signer(clock)


@pytest.fixture
def endpoint(signer: Signer) -> FakeJwksEndpoint:
    return FakeJwksEndpoint([public_jwk(signer.private_key, signer.kid)])


@pytest.fixture
def validator(endpoint: FakeJwksEndpoint, clock: FrozenClock) -> ResourceTokenValidator:
    return make_validator(endpoint, clock)


def check(validator: ResourceTokenValidator, token: str, scope: str = "admin") -> AccessToken:
    return validator.validate(token, required_scope=scope)


# ── 통과 ──────────────────────────────────────────────────────────────────────


def test_owner_admin_token_passes(signer: Signer, validator: ResourceTokenValidator) -> None:
    token = check(validator, signer.token())

    assert (token.sub, token.acct) == (OWNER, "owner")
    assert token.client_id == "client-123"
    assert token.scopes == frozenset({"admin", "login"})
    assert token.jti == "jti-1"


def test_member_token_passes_the_account_rules(
    signer: Signer, validator: ResourceTokenValidator
) -> None:
    token = check(validator, signer.member_token(), scope="login")

    assert (token.sub, token.acct, token.scopes) == (MEMBER_SUB, "member", frozenset({"login"}))


# ── 헤더와 키(T1~T5) ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.b.c", "!!!.e30.sig"])
def test_malformed_token_is_rejected(validator: ResourceTokenValidator, token: str) -> None:
    with pytest.raises(TokenRejected):
        check(validator, token)


def test_alg_none_is_rejected(signer: Signer, validator: ResourceTokenValidator) -> None:
    _, payload, _ = signer.token().split(".")
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","kid":"key-a"}').rstrip(b"=").decode()

    with pytest.raises(TokenRejected):
        check(validator, f"{none_header}.{payload}.")


def test_hs256_signed_with_the_public_key_is_rejected(
    signer: Signer, endpoint: FakeJwksEndpoint, validator: ResourceTokenValidator
) -> None:
    forged = jwt.encode(
        signer.claims(), endpoint.keys[0]["x"], algorithm="HS256", headers={"kid": signer.kid}
    )

    with pytest.raises(TokenRejected):
        check(validator, forged)


def test_token_without_kid_is_rejected(signer: Signer, validator: ResourceTokenValidator) -> None:
    token = jwt.encode(signer.claims(), signer.private_key, algorithm="EdDSA")

    with pytest.raises(TokenRejected):
        check(validator, token)


def test_signature_by_another_key_with_the_same_kid_is_rejected(
    clock: FrozenClock, validator: ResourceTokenValidator
) -> None:
    impostor = new_signer(clock, kid="key-a")

    with pytest.raises(TokenRejected):
        check(validator, impostor.token())


# ── 클레임(T6~T10) ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("claim", ["iss", "sub", "aud", "client_id", "scope", "iat", "exp", "jti"])
def test_missing_required_claim_is_rejected(
    signer: Signer, validator: ResourceTokenValidator, claim: str
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(**{claim: None}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"iat": "1767225600"},
        {"exp": 1767226200.5},
        {"exp": True},
        {"aud": [FLOW]},
        {"scope": ["admin"]},
        {"client_id": 123},
    ],
)
def test_claim_with_the_wrong_type_is_rejected(
    signer: Signer, validator: ResourceTokenValidator, overrides: dict[str, object]
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(**overrides))


@pytest.mark.parametrize("iss", [ISSUER + "/", "https://evil.example", "https://AUTH.sandcorp.co.kr"])
def test_other_issuer_is_rejected(signer: Signer, validator: ResourceTokenValidator, iss: str) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(iss=iss))


@pytest.mark.parametrize(
    "aud",
    [FLOW + "/", "https://FLOW.sandcorp.co.kr/api", "https://news.sandcorp.co.kr/mcp", [FLOW]],
)
def test_other_audience_is_rejected(
    signer: Signer, validator: ResourceTokenValidator, aud: object
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(aud=aud))


def test_expiry_allows_30_seconds_of_leeway_then_rejects(
    signer: Signer, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    token = signer.token()

    clock.advance(600 + 30)
    check(validator, token)

    clock.advance(1)
    with pytest.raises(TokenRejected):
        check(validator, token)


def test_iat_in_the_future_is_not_compared(
    signer: Signer, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    now = int(clock.now().timestamp())

    check(validator, signer.token(iat=now + 3600, nbf=now + 3600))


# ── 계정 종류(A1~A3, T11 대신) ────────────────────────────────────────────────


def test_token_without_acct_must_have_the_owner_sub(
    signer: Signer, validator: ResourceTokenValidator
) -> None:
    for sub in ["someone-else", MEMBER_SUB, OWNER.upper(), f" {OWNER}"]:
        with pytest.raises(TokenRejected):
            check(validator, signer.token(sub=sub))


def test_empty_owner_sub_setting_rejects_every_owner_token(
    signer: Signer, endpoint: FakeJwksEndpoint, clock: FrozenClock
) -> None:
    validator = make_validator(endpoint, clock, owner_sub="")

    with pytest.raises(TokenRejected):
        check(validator, signer.token(sub=""))


@pytest.mark.parametrize("acct", ["owner", "Member", "MEMBER", "admin", "", 1, ["member"], None])
def test_acct_other_than_member_is_rejected(
    signer: Signer, validator: ResourceTokenValidator, acct: object
) -> None:
    claims = signer.claims(sub=MEMBER_SUB, scope="login")
    claims["acct"] = acct  # None도 「있는데 null」로 싣는다

    with pytest.raises(TokenRejected):
        check(validator, signer.sign(claims), scope="login")


@pytest.mark.parametrize(
    "sub",
    [
        OWNER,
        "m_short",
        "m_01jb2z3y4x5w6v7t8s9r0q1p2n",  # 대소문자 구분
        "x" + MEMBER_SUB,
        MEMBER_SUB + "X",
        "m_81JB2Z3Y4X5W6V7T8S9R0Q1P2N",  # 첫 글자는 0~7
        "m_01JB2Z3Y4X5W6V7T8S9R0Q1P2I",  # Crockford base32에 I 없음
    ],
)
def test_member_token_needs_a_member_sub(
    signer: Signer, validator: ResourceTokenValidator, sub: str
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.member_token(sub=sub), scope="login")


# ── 스코프(S1) ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", ["login", "administrator", "admins", "Admin", "", "  "])
def test_missing_scope_is_forbidden_not_unauthorized(
    signer: Signer, validator: ResourceTokenValidator, scope: str
) -> None:
    with pytest.raises(InsufficientScope) as raised:
        check(validator, signer.token(scope=scope), scope="admin")
    assert raised.value.required_scope == "admin"


def test_scope_is_split_on_single_spaces_dropping_empty_pieces(
    signer: Signer, validator: ResourceTokenValidator
) -> None:
    token = check(validator, signer.token(scope="  login   admin "))

    assert token.scopes == frozenset({"login", "admin"})


def test_member_token_without_the_scope_is_insufficient(
    signer: Signer, validator: ResourceTokenValidator
) -> None:
    with pytest.raises(InsufficientScope):
        check(validator, signer.member_token(), scope="admin")


# ── JWKS 캐시(J1~J5) ──────────────────────────────────────────────────────────


def test_unknown_kid_refetches_jwks_once_then_passes(
    signer: Signer, endpoint: FakeJwksEndpoint, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    check(validator, signer.token())
    assert endpoint.fetches == 1

    rotated = new_signer(clock, kid="key-b")
    endpoint.keys.append(public_jwk(rotated.private_key, rotated.kid))

    check(validator, rotated.token())
    assert endpoint.fetches == 2


def test_unknown_kid_refetch_is_limited_to_once_per_60_seconds(
    signer: Signer, endpoint: FakeJwksEndpoint, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    check(validator, signer.token())
    stranger = new_signer(clock, kid="key-unknown")

    for _ in range(3):
        with pytest.raises(TokenRejected):
            check(validator, stranger.token())
    assert endpoint.fetches == 2

    clock.advance(59)
    with pytest.raises(TokenRejected):
        check(validator, stranger.token())
    assert endpoint.fetches == 2

    clock.advance(1)
    with pytest.raises(TokenRejected):
        check(validator, stranger.token())
    assert endpoint.fetches == 3


def test_jwks_is_refetched_after_the_10_minute_ttl(
    signer: Signer, endpoint: FakeJwksEndpoint, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    check(validator, signer.token())
    clock.advance(599)
    check(validator, signer.token())
    assert endpoint.fetches == 1

    clock.advance(1)
    check(validator, signer.token())
    assert endpoint.fetches == 2


def test_key_dropped_from_jwks_is_dropped_from_the_cache(
    signer: Signer, endpoint: FakeJwksEndpoint, validator: ResourceTokenValidator, clock: FrozenClock
) -> None:
    check(validator, signer.token())
    endpoint.keys.clear()
    clock.advance(600)

    with pytest.raises(TokenRejected):
        check(validator, signer.token())


def test_only_ed25519_signing_keys_are_used(signer: Signer, clock: FrozenClock) -> None:
    good = public_jwk(signer.private_key, signer.kid)
    for bad in [{"alg": "RS256"}, {"use": "enc"}, {"crv": "X25519"}, {"kty": "EC"}]:
        endpoint = FakeJwksEndpoint([{**good, **bad}])  # type: ignore[list-item]  # 일부러 틀린 JWK
        with pytest.raises(TokenRejected):
            check(make_validator(endpoint, clock), signer.token())

    without_optional = {k: v for k, v in good.items() if k not in ("alg", "use")}
    endpoint = FakeJwksEndpoint([without_optional])  # type: ignore[list-item]  # alg·use는 선택
    check(make_validator(endpoint, clock), signer.token())


def test_failed_jwks_fetch_keeps_the_cached_keys_and_waits_60_seconds(
    signer: Signer, endpoint: FakeJwksEndpoint, clock: FrozenClock
) -> None:
    calls = 0

    def flaky() -> JwkSet:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("auth server down")
        return endpoint()

    validator = ResourceTokenValidator(
        jwks=JwksCache(flaky, clock), issuer=ISSUER, audience=FLOW, owner_sub=OWNER, clock=clock
    )
    check(validator, signer.token())
    clock.advance(600)

    check(validator, signer.token())  # 조회 실패 — 캐시로 통과
    assert calls == 2
    clock.advance(59)
    check(validator, signer.token())
    assert calls == 2  # 60초 동안 다시 시도하지 않는다
    clock.advance(1)
    check(validator, signer.token())
    assert calls == 3


# ── 실제 HTTP 페처(로컬 서버) ─────────────────────────────────────────────────


class _JwksServer:
    def __init__(self) -> None:
        self.status = 200
        self.body = b'{"keys": []}'
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/.well-known/jwks.json"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def jwks_server():
    server = _JwksServer()
    yield server
    server.close()


def test_http_fetcher_reads_the_jwks(jwks_server: _JwksServer, signer: Signer, clock: FrozenClock) -> None:
    jwks_server.body = json.dumps({"keys": [public_jwk(signer.private_key, signer.kid)]}).encode()
    validator = ResourceTokenValidator(
        jwks=JwksCache(http_jwks_fetcher(jwks_server.url), clock),
        issuer=ISSUER,
        audience=FLOW,
        owner_sub=OWNER,
        clock=clock,
    )

    assert check(validator, signer.token()).sub == OWNER


@pytest.mark.parametrize(("status", "body"), [(500, b'{"keys": []}'), (200, b"not json"), (200, b"[]")])
def test_http_fetcher_raises_on_bad_responses(
    jwks_server: _JwksServer, status: int, body: bytes
) -> None:
    jwks_server.status, jwks_server.body = status, body

    with pytest.raises(Exception):
        http_jwks_fetcher(jwks_server.url)()


# ── 실패 응답 헤더(5절)·메타데이터(6절) ───────────────────────────────────────


def test_www_authenticate_header_values() -> None:
    origin = site_origin(FLOW)
    meta = "https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource"

    assert origin == "https://flow.sandcorp.co.kr"
    assert www_authenticate(origin) == f'Bearer resource_metadata="{meta}"'
    assert www_authenticate(origin, error="invalid_token") == (
        f'Bearer resource_metadata="{meta}", error="invalid_token"'
    )
    assert www_authenticate(origin, error="insufficient_scope", scope="admin") == (
        'Bearer error="insufficient_scope", scope="admin"'
    )


def test_protected_resource_metadata_shape() -> None:
    assert protected_resource_metadata(resource=FLOW, issuer=ISSUER) == {
        "resource": "https://flow.sandcorp.co.kr/api",
        "authorization_servers": ["https://auth.sandcorp.co.kr"],
        "scopes_supported": ["admin", "login"],
        "bearer_methods_supported": ["header"],
    }
