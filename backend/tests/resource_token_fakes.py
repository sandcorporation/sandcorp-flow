"""Resource Token 시험 도구: 실제 Ed25519 키로 서명하는 발급자 대역, 결정적 JWKS 엔드포인트, 고정 시계.

외부 경계(auth.sandcorp.co.kr의 JWKS, 시계)만 대역으로 바꾼다. 검증기는 진짜다.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from apps.tenants.resource_token import Jwk, JwksCache, JwkSet, ResourceTokenValidator

ISSUER = "https://auth.sandcorp.co.kr"
FLOW = "https://flow.sandcorp.co.kr/api"
OWNER = "farrar142"
MEMBER_SUB = "m_01JB2Z3Y4X5W6V7T8S9R0Q1P2N"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FrozenClock:
    """시험이 말할 때만 움직이는 시계."""

    def __init__(self, start: datetime = START) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def public_jwk(private_key: Ed25519PrivateKey, kid: str) -> Jwk:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "alg": "EdDSA", "use": "sig"}


@dataclass
class FakeJwksEndpoint:
    """시험이 `keys`에 넣은 키를 내주고 조회 횟수를 센다."""

    keys: list[Jwk] = field(default_factory=list)
    fetches: int = 0

    def __call__(self) -> JwkSet:
        self.fetches += 1
        return {"keys": list(self.keys)}


@dataclass
class Signer:
    private_key: Ed25519PrivateKey
    kid: str
    clock: FrozenClock

    def claims(self, **overrides: object) -> dict[str, object]:
        """기본은 대표의 Flow `admin` 토큰. 값이 None이면 그 클레임을 뺀다."""
        now = int(self.clock.now().timestamp())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "sub": OWNER,
            "aud": FLOW,
            "client_id": "client-123",
            "scope": "admin login",
            "iat": now,
            "exp": now + 600,
            "jti": "jti-1",
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return claims

    def sign(self, claims: dict[str, object]) -> str:
        return jwt.encode(claims, self.private_key, algorithm="EdDSA", headers={"kid": self.kid})

    def token(self, **overrides: object) -> str:
        return self.sign(self.claims(**overrides))

    def member_token(self, **overrides: object) -> str:
        return self.token(**{"sub": MEMBER_SUB, "acct": "member", "scope": "login", **overrides})


def new_signer(clock: FrozenClock, kid: str = "key-a") -> Signer:
    return Signer(Ed25519PrivateKey.generate(), kid, clock)


def make_validator(
    endpoint: FakeJwksEndpoint, clock: FrozenClock, *, owner_sub: str = OWNER
) -> ResourceTokenValidator:
    return ResourceTokenValidator(
        jwks=JwksCache(endpoint, clock),
        issuer=ISSUER,
        audience=FLOW,
        owner_sub=owner_sub,
        clock=clock,
    )
