"""sandcorp-auth Resource Token 검증 규칙(B·T1~T10·A1~A3·S1·J1~J5·5절) — ADR-0028 「고객 토큰 검증」.

검증은 sandcorp-authkit(#282)이 하고, Flow의 몫은 어댑터(`apps.tenants.authkit`)가 넘기는 설정 — 발급자·리소스·
계정 규칙(`owner_or_member`)이다. 이 시험은 그 설정으로 규칙이 그대로 지켜지는지 본다(옮기기 전 시험 그대로).
실제 Ed25519 키로 서명하고, JWKS는 결정적 대역(또는 로컬 HTTP 서버), 시계는 고정한다.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest

from apps.tenants import authkit
from authkit_fakes import FLOW, ISSUER, MEMBER_SUB, OWNER, FakeJwks, FixedClock, Signer, use_fake_jwks
from sandcorp_authkit.django import BearerConfig
from sandcorp_authkit.resource import protected_resource_metadata, www_authenticate
from sandcorp_authkit.testing import SigningKey, public_jwk
from sandcorp_authkit.tokens import (
    AccessToken,
    InsufficientScope,
    JwksCache,
    TokenRejected,
    http_jwks_fetcher,
    owner_or_member,
    validate_access_token,
)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def endpoint() -> FakeJwks:
    return FakeJwks(SigningKey(kid="key-a"))


@pytest.fixture
def signer(endpoint: FakeJwks, clock: FixedClock) -> Signer:
    return Signer(endpoint, clock)


@pytest.fixture
def validator(monkeypatch: pytest.MonkeyPatch, endpoint: FakeJwks, clock: FixedClock) -> BearerConfig:
    """운영과 같은 어댑터 설정(API 리소스) — JWKS·시계만 대역."""
    use_fake_jwks(monkeypatch, endpoint, clock)
    return authkit.api_bearer_config()


def new_signer(clock: FixedClock, kid: str) -> Signer:
    return Signer(FakeJwks(SigningKey(kid=kid)), clock)


def make_validator(jwks: JwksCache, clock: FixedClock, *, owner_sub: str = OWNER) -> BearerConfig:
    return BearerConfig(
        issuer=ISSUER,
        audience=FLOW,
        resource_metadata_url=authkit.api_metadata_url(),
        jwks=jwks,
        clock=clock,
        extra_claims_hook=owner_or_member(owner_sub),
    )


def published(*jwks: dict[str, str]) -> FakeJwks:
    endpoint = FakeJwks()
    endpoint.document = {"keys": list(jwks)}
    return endpoint


def check(validator: BearerConfig, token: str, scope: str = "admin") -> AccessToken:
    return validate_access_token(
        token,
        issuer=validator.issuer,
        audience=validator.audience,
        required_scope=scope,
        jwks=validator.jwks,
        clock=validator.clock,
        extra_claims_hook=validator.extra_claims_hook,
    )


# ── 통과 ──────────────────────────────────────────────────────────────────────


def test_owner_admin_token_passes(signer: Signer, validator: BearerConfig) -> None:
    token = check(validator, signer.token())

    assert (token.sub, token.acct) == (OWNER, None)
    assert token.client_id == "client-123"
    assert token.scopes == frozenset({"admin", "login"})
    assert token.jti == "jti-1"


def test_member_token_passes_the_account_rules(
    signer: Signer, validator: BearerConfig
) -> None:
    token = check(validator, signer.member_token(), scope="login")

    assert (token.sub, token.acct, token.scopes) == (MEMBER_SUB, "member", frozenset({"login"}))


# ── 헤더와 키(T1~T5) ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.b.c", "!!!.e30.sig"])
def test_malformed_token_is_rejected(validator: BearerConfig, token: str) -> None:
    with pytest.raises(TokenRejected):
        check(validator, token)


def test_alg_none_is_rejected(signer: Signer, validator: BearerConfig) -> None:
    _, payload, _ = signer.token().split(".")
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","kid":"key-a"}').rstrip(b"=").decode()

    with pytest.raises(TokenRejected):
        check(validator, f"{none_header}.{payload}.")


def test_hs256_signed_with_the_public_key_is_rejected(
    signer: Signer, endpoint: FakeJwks, validator: BearerConfig
) -> None:
    forged = jwt.encode(
        signer.claims(), endpoint.key.jwk()["x"], algorithm="HS256", headers={"kid": signer.jwks.key.kid}
    )

    with pytest.raises(TokenRejected):
        check(validator, forged)


def test_token_without_kid_is_rejected(signer: Signer, validator: BearerConfig) -> None:
    token = jwt.encode(signer.claims(), signer.jwks.key.private, algorithm="EdDSA")

    with pytest.raises(TokenRejected):
        check(validator, token)


def test_signature_by_another_key_with_the_same_kid_is_rejected(
    clock: FixedClock, validator: BearerConfig
) -> None:
    impostor = new_signer(clock, kid="key-a")

    with pytest.raises(TokenRejected):
        check(validator, impostor.token())


# ── 클레임(T6~T10) ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("claim", ["iss", "sub", "aud", "client_id", "scope", "iat", "exp", "jti"])
def test_missing_required_claim_is_rejected(
    signer: Signer, validator: BearerConfig, claim: str
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
    signer: Signer, validator: BearerConfig, overrides: dict[str, object]
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(**overrides))


@pytest.mark.parametrize("iss", [ISSUER + "/", "https://evil.example", "https://AUTH.sandcorp.co.kr"])
def test_other_issuer_is_rejected(signer: Signer, validator: BearerConfig, iss: str) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(iss=iss))


@pytest.mark.parametrize(
    "aud",
    [FLOW + "/", "https://FLOW.sandcorp.co.kr/api", "https://news.sandcorp.co.kr/mcp", [FLOW]],
)
def test_other_audience_is_rejected(
    signer: Signer, validator: BearerConfig, aud: object
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.token(aud=aud))


def test_expiry_allows_30_seconds_of_leeway_then_rejects(
    signer: Signer, validator: BearerConfig, clock: FixedClock
) -> None:
    token = signer.token()

    clock.advance(600 + 30)
    check(validator, token)

    clock.advance(1)
    with pytest.raises(TokenRejected):
        check(validator, token)


def test_iat_in_the_future_is_not_compared(
    signer: Signer, validator: BearerConfig, clock: FixedClock
) -> None:
    now = int(clock.now().timestamp())

    check(validator, signer.token(iat=now + 3600, nbf=now + 3600))


# ── 계정 종류(A1~A3, T11 대신) ────────────────────────────────────────────────


def test_token_without_acct_must_have_the_owner_sub(
    signer: Signer, validator: BearerConfig
) -> None:
    for sub in ["someone-else", MEMBER_SUB, OWNER.upper(), f" {OWNER}"]:
        with pytest.raises(TokenRejected):
            check(validator, signer.token(sub=sub))


def test_empty_owner_sub_setting_rejects_every_owner_token(
    signer: Signer, endpoint: FakeJwks, clock: FixedClock
) -> None:
    validator = make_validator(endpoint.cache(clock), clock, owner_sub="")

    with pytest.raises(TokenRejected):
        check(validator, signer.token(sub=""))


@pytest.mark.parametrize("acct", ["owner", "Member", "MEMBER", "admin", "", 1, ["member"], None])
def test_acct_other_than_member_is_rejected(
    signer: Signer, validator: BearerConfig, acct: object
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
    signer: Signer, validator: BearerConfig, sub: str
) -> None:
    with pytest.raises(TokenRejected):
        check(validator, signer.member_token(sub=sub), scope="login")


# ── 스코프(S1) ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", ["login", "administrator", "admins", "Admin", "", "  "])
def test_missing_scope_is_forbidden_not_unauthorized(
    signer: Signer, validator: BearerConfig, scope: str
) -> None:
    with pytest.raises(InsufficientScope) as raised:
        check(validator, signer.token(scope=scope), scope="admin")
    assert raised.value.required_scope == "admin"


def test_scope_is_split_on_single_spaces_dropping_empty_pieces(
    signer: Signer, validator: BearerConfig
) -> None:
    token = check(validator, signer.token(scope="  login   admin "))

    assert token.scopes == frozenset({"login", "admin"})


def test_member_token_without_the_scope_is_insufficient(
    signer: Signer, validator: BearerConfig
) -> None:
    with pytest.raises(InsufficientScope):
        check(validator, signer.member_token(), scope="admin")


# ── JWKS 캐시(J1~J5) ──────────────────────────────────────────────────────────


def test_unknown_kid_refetches_jwks_once_then_passes(
    signer: Signer, endpoint: FakeJwks, validator: BearerConfig, clock: FixedClock
) -> None:
    check(validator, signer.token())
    assert endpoint.fetches == 1

    rotated = new_signer(clock, kid="key-b")
    endpoint.serve(*endpoint.keys, rotated.jwks.key)

    check(validator, rotated.token())
    assert endpoint.fetches == 2


def test_unknown_kid_refetch_is_limited_to_once_per_60_seconds(
    signer: Signer, endpoint: FakeJwks, validator: BearerConfig, clock: FixedClock
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
    signer: Signer, endpoint: FakeJwks, validator: BearerConfig, clock: FixedClock
) -> None:
    check(validator, signer.token())
    clock.advance(599)
    check(validator, signer.token())
    assert endpoint.fetches == 1

    clock.advance(1)
    check(validator, signer.token())
    assert endpoint.fetches == 2


def test_key_dropped_from_jwks_is_dropped_from_the_cache(
    signer: Signer, endpoint: FakeJwks, validator: BearerConfig, clock: FixedClock
) -> None:
    check(validator, signer.token())
    endpoint.serve()
    clock.advance(600)

    with pytest.raises(TokenRejected):
        check(validator, signer.token())


def test_only_ed25519_signing_keys_are_used(signer: Signer, clock: FixedClock) -> None:
    good = public_jwk(signer.jwks.key.private, signer.jwks.key.kid)
    for bad in [{"alg": "RS256"}, {"use": "enc"}, {"crv": "X25519"}, {"kty": "EC"}]:
        with pytest.raises(TokenRejected):  # 일부러 틀린 JWK
            check(make_validator(published({**good, **bad}).cache(clock), clock), signer.token())

    without_optional = {k: v for k, v in good.items() if k not in ("alg", "use")}
    check(make_validator(published(without_optional).cache(clock), clock), signer.token())  # alg·use는 선택


def test_failed_jwks_fetch_keeps_the_cached_keys_and_waits_60_seconds(
    signer: Signer, endpoint: FakeJwks, clock: FixedClock
) -> None:
    calls = 0

    def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("auth server down")
        return endpoint()

    validator = make_validator(JwksCache(flaky, clock), clock)
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
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def jwks_server():
    server = _JwksServer()
    yield server
    server.close()


def test_http_fetcher_reads_the_jwks(jwks_server: _JwksServer, signer: Signer, clock: FixedClock) -> None:
    jwks_server.body = json.dumps({"keys": [public_jwk(signer.jwks.key.private, signer.jwks.key.kid)]}).encode()
    validator = make_validator(JwksCache(http_jwks_fetcher(jwks_server.url), clock), clock)

    assert check(validator, signer.token()).sub == OWNER


@pytest.mark.parametrize(("status", "body"), [(500, b'{"keys": []}'), (200, b"not json"), (200, b"[]")])
def test_http_fetcher_bad_responses_are_failed_fetches(
    jwks_server: _JwksServer, signer: Signer, clock: FixedClock, status: int, body: bytes
) -> None:
    """2xx 아님·JSON 깨짐·키 집합 아님은 조회 실패(J4) — 키가 없으니 토큰은 거부된다."""
    jwks_server.status, jwks_server.body = status, body
    validator = make_validator(JwksCache(http_jwks_fetcher(jwks_server.url), clock), clock)

    with pytest.raises(TokenRejected):
        check(validator, signer.token())


# ── 실패 응답 헤더(5절)·메타데이터(6절) ───────────────────────────────────────


def test_www_authenticate_header_values() -> None:
    meta = authkit.api_metadata_url()

    assert meta == "https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource"
    assert www_authenticate(meta) == f'Bearer resource_metadata="{meta}"'
    assert www_authenticate(meta, error="invalid_token") == (
        f'Bearer resource_metadata="{meta}", error="invalid_token"'
    )
    assert www_authenticate(meta, error="insufficient_scope", scope="admin") == (
        'Bearer error="insufficient_scope", scope="admin"'
    )


def test_protected_resource_metadata_shape() -> None:
    assert protected_resource_metadata(FLOW, ISSUER, authkit.API_SCOPES) == {
        "resource": "https://flow.sandcorp.co.kr/api",
        "authorization_servers": ["https://auth.sandcorp.co.kr"],
        "scopes_supported": ["admin", "login"],
        "bearer_methods_supported": ["header"],
    }
