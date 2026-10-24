"""sandcorp-auth 액세스 토큰(Resource Token) 검증기 — ADR-0028 「고객 토큰 검증」.

규칙 원문은 sandcorp-auth `docs/resource-server-token-validation.md`이고, 이 파일은 그 문서의
참조 구현(`backend/tests/reference_validator.py`의 `validate_account_access_token`)을 규칙 그대로
옮긴 것이다. Flow는 일반 사용자도 받는 사이트라서 T11 대신 A1~A3(10절)을 쓴다.

- 검증 순서: B1·B2(Bearer 헤더) → T1~T10 → A1~A3 → S1.
- JWKS 캐시: J1~J5(600초 TTL, 모르는 kid 재조회 60초에 1번, 조회 실패 시 기존 캐시 유지·60초 쉼).
- 실패 응답 헤더: 5절(`www_authenticate`). 메타데이터: 6절(`protected_resource_metadata`).

외부 경계(JWKS 조회, 시계)는 주입한다. 운영은 `http_jwks_fetcher`와 `SystemClock`.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal, NotRequired, Protocol, TypedDict
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

ALGORITHM = "EdDSA"
JWKS_TTL_SECONDS = 600
JWKS_MIN_REFETCH_SECONDS = 60
JWKS_TIMEOUT_SECONDS = 5
EXP_LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat", "jti", "client_id", "scope"]
ACCOUNT_KIND_CLAIM = "acct"
MEMBER_SUB = re.compile(r"m_[0-7][0-9A-HJKMNP-TV-Z]{25}")
SCOPES_SUPPORTED = ["admin", "login"]
METADATA_PATH = "/.well-known/oauth-protected-resource"


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Jwk(TypedDict):
    kty: str
    crv: str
    x: str
    kid: str
    alg: NotRequired[str]
    use: NotRequired[str]


class JwkSet(TypedDict):
    keys: list[Jwk]


class TokenRejected(Exception):
    """401 `error="invalid_token"`. 메시지(거부 이유)는 로그에만 남기고 응답에 싣지 않는다."""


class InsufficientScope(Exception):
    """403 `error="insufficient_scope"`."""

    def __init__(self, required_scope: str) -> None:
        super().__init__(required_scope)
        self.required_scope = required_scope


@dataclass(frozen=True)
class AccessToken:
    sub: str
    client_id: str
    scopes: frozenset[str]
    jti: str
    iat: int
    exp: int
    acct: Literal["owner", "member"] = "owner"


class JwksCache:
    """`kid → 공개 키` 캐시(J1~J5). 프로세스마다 하나."""

    def __init__(self, fetch_jwks: Callable[[], JwkSet], clock: Clock) -> None:
        self._fetch = fetch_jwks
        self._clock = clock
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._unknown_kid_refetch_at: float | None = None

    def key_for(self, kid: str) -> Ed25519PublicKey | None:
        now = self._clock.now().timestamp()
        stale = self._fetched_at is None or now - self._fetched_at >= JWKS_TTL_SECONDS
        if stale and not _within(self._failed_at, now):  # J1
            self._refresh(now)
        elif kid not in self._keys and not _within(self._unknown_kid_refetch_at, now):  # J2
            self._unknown_kid_refetch_at = now
            self._refresh(now)
        return self._keys.get(kid)

    def _refresh(self, now: float) -> None:
        try:
            jwk_set = self._fetch()
        except Exception:  # J4: 네트워크·타임아웃·2xx 아님·JSON 깨짐 — 기존 키를 그대로 쓴다
            logger.warning("JWKS 조회 실패 — 기존 캐시 유지, 60초 동안 재시도 안 함", exc_info=True)
            self._failed_at = now
            return
        keys: dict[str, Ed25519PublicKey] = {}
        for jwk in jwk_set.get("keys", []):  # J3
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                continue
            if jwk.get("alg", ALGORITHM) != ALGORITHM or jwk.get("use", "sig") != "sig":
                continue
            key = jwt.PyJWK(dict(jwk), algorithm=ALGORITHM).key
            if isinstance(key, Ed25519PublicKey):
                keys[jwk["kid"]] = key
        self._keys = keys
        self._fetched_at = now
        self._failed_at = None


def _within(moment: float | None, now: float) -> bool:
    return moment is not None and now - moment < JWKS_MIN_REFETCH_SECONDS


def http_jwks_fetcher(url: str) -> Callable[[], JwkSet]:
    """`GET <url>`(타임아웃 5초). 2xx가 아니거나 JSON이 깨졌으면 예외 — JwksCache가 J4로 받는다."""

    def fetch() -> JwkSet:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=JWKS_TIMEOUT_SECONDS) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                raise OSError(f"JWKS status {response.status}")
            body: object = json.loads(response.read())
        if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
            raise ValueError("JWKS is not a key set")
        return JwkSet(keys=body["keys"])

    return fetch


@dataclass(frozen=True)
class ResourceTokenValidator:
    """Flow 리소스(`audience`)용 검증기. `validate`가 T1~T10 → A1~A3 → S1을 적용한다."""

    jwks: JwksCache
    issuer: str
    audience: str
    owner_sub: str
    clock: Clock

    def validate(self, token: str, *, required_scope: str) -> AccessToken:
        verified, claims = self._verify(token)
        if ACCOUNT_KIND_CLAIM not in claims:  # A1: acct 없음 = 대표 토큰
            if not self.owner_sub or verified.sub != self.owner_sub:
                raise TokenRejected("not the owner")
            return _with_scope(verified, required_scope)
        if claims[ACCOUNT_KIND_CLAIM] != "member":  # A2
            raise TokenRejected("unknown account kind")
        if MEMBER_SUB.fullmatch(verified.sub) is None:  # A3
            raise TokenRejected("not a member sub")
        return _with_scope(replace(verified, acct="member"), required_scope)

    def _verify(self, token: str) -> tuple[AccessToken, dict[str, object]]:
        """T1~T10: 우리 발급자가 이 리소스에 준, 만료되지 않은 토큰인가. 스코프는 아직."""
        try:
            header = jwt.get_unverified_header(token)  # T1
        except jwt.InvalidTokenError as exc:
            raise TokenRejected("malformed token") from exc
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str):  # T2·T3
            raise TokenRejected("alg must be EdDSA and kid must be present")
        key = self.jwks.key_for(kid)  # T4
        if key is None:
            raise TokenRejected("unknown kid")
        try:
            claims: dict[str, object] = jwt.decode(  # T5·T6
                token,
                key,
                algorithms=[ALGORITHM],
                options={
                    "require": REQUIRED_CLAIMS,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise TokenRejected("bad signature or missing claim") from exc

        sub, client_id, jti, scope = (claims[n] for n in ("sub", "client_id", "jti", "scope"))
        iat, exp = claims["iat"], claims["exp"]
        if not (  # T7 (bool은 int의 하위형이라 따로 막는다)
            isinstance(sub, str)
            and isinstance(client_id, str)
            and isinstance(jti, str)
            and isinstance(scope, str)
            and isinstance(iat, int)
            and not isinstance(iat, bool)
            and isinstance(exp, int)
            and not isinstance(exp, bool)
        ):
            raise TokenRejected("claim has the wrong type")
        if claims["iss"] != self.issuer:  # T8
            raise TokenRejected("wrong issuer")
        if claims["aud"] != self.audience:  # T9: 문자열 완전 일치만(배열·끝 `/` 거부)
            raise TokenRejected("wrong audience")
        if self.clock.now().timestamp() > exp + EXP_LEEWAY_SECONDS:  # T10
            raise TokenRejected("expired")

        scopes = frozenset(scope.split(" ")) - {""}
        verified = AccessToken(
            sub=sub, client_id=client_id, scopes=scopes, jti=jti, iat=iat, exp=exp
        )
        return verified, claims


def _with_scope(verified: AccessToken, required_scope: str) -> AccessToken:
    if required_scope not in verified.scopes:  # S1: 원소 비교(administrator ≠ admin)
        raise InsufficientScope(required_scope)
    return verified


def site_origin(resource: str) -> str:
    """`https://flow.sandcorp.co.kr/api` → `https://flow.sandcorp.co.kr`."""
    parts = urlsplit(resource)
    return f"{parts.scheme}://{parts.netloc}"


def www_authenticate(
    origin: str,
    *,
    error: Literal["invalid_token", "insufficient_scope"] | None = None,
    scope: str | None = None,
    metadata_path: str = METADATA_PATH,
) -> str:
    """5절 `WWW-Authenticate` 값: 401 토큰 없음/`invalid_token`, 403 `insufficient_scope`.

    `metadata_path`는 리소스별 메타데이터 경로(MCP는 `/.well-known/oauth-protected-resource/mcp`)."""
    if error == "insufficient_scope":
        return f'Bearer error="insufficient_scope", scope="{scope}"'
    value = f'Bearer resource_metadata="{origin}{metadata_path}"'
    if error == "invalid_token":
        value += ', error="invalid_token"'
    return value


class ProtectedResourceMetadata(TypedDict):
    resource: str
    authorization_servers: list[str]
    scopes_supported: list[str]
    bearer_methods_supported: list[str]


def protected_resource_metadata(
    *, resource: str, issuer: str, scopes: list[str] = SCOPES_SUPPORTED
) -> ProtectedResourceMetadata:
    """6절(RFC 9728) `GET /.well-known/oauth-protected-resource[/mcp]` 본문."""
    return {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": list(scopes),
        "bearer_methods_supported": ["header"],
    }


# ── 프로세스 하나에 검증기 하나(J1) ─────────────────────────────────────────────

_validator: ResourceTokenValidator | None = None


def get_validator() -> ResourceTokenValidator:
    """설정(AUTH_*)으로 만든 프로세스 검증기. 처음 부를 때 만든다."""
    global _validator
    if _validator is None:
        from django.conf import settings

        clock = SystemClock()
        _validator = ResourceTokenValidator(
            jwks=JwksCache(http_jwks_fetcher(settings.AUTH_JWKS_URL), clock),
            issuer=settings.AUTH_ISSUER,
            audience=settings.AUTH_RESOURCE,
            owner_sub=settings.AUTH_OWNER_SUB,
            clock=clock,
        )
    return _validator


def get_mcp_validator() -> ResourceTokenValidator:
    """MCP 리소스(`AUTH_MCP_RESOURCE`, #74 결정 2)용 검증기. API 검증기와 JWKS 캐시·발급자·대표를 함께 쓰고
    audience만 다르다 — 그래서 `use_validator`로 바꾼 시험 검증기를 그대로 따른다."""
    from django.conf import settings

    return replace(get_validator(), audience=settings.AUTH_MCP_RESOURCE)


def use_validator(validator: ResourceTokenValidator | None) -> None:
    """프로세스 검증기를 바꾼다(시험이 결정적 JWKS·고정 시계를 넣을 때). None이면 설정으로 다시 만든다."""
    global _validator
    _validator = validator
