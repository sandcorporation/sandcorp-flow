"""sandcorp-authkit 시험 대역(`sandcorp_authkit.testing`)을 Flow 시험 기본값에 맞춘다(#282).

대역 자체(JWKS 엔드포인트·Ed25519 서명 키·고정 시계·가짜 인가 서버)는 패키지 것이다. 여기에는 Flow 시험이
쓰던 기본값(토큰은 대표의 Flow API `admin` 토큰, 인가 서버는 `flow-admin` 클라이언트·대표 프로필)과
어댑터 seam(`authkit.clock`·`authkit.jwks_cache`·`authkit.token_transport`) 바꾸기만 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest
from django.conf import settings

from apps.tenants import authkit
from sandcorp_authkit.testing import (
    ISSUER,
    MEMBER_SUB,
    OWNER_SUB,
    FakeJwks,
    FakeTokenEndpoint,
    FixedClock,
    access_token_claims,
)

__all__ = ["ISSUER", "MEMBER_SUB", "OWNER", "FLOW", "FixedClock", "FakeJwks", "Signer", "FlowAuthServer"]

OWNER = OWNER_SUB
FLOW = "https://flow.sandcorp.co.kr/api"
CLIENT_ID = "flow-admin"
CLIENT_SECRET = "flow-admin-secret"
REDIRECT_URI = "https://flow.sandcorp.co.kr/api/auth/callback"
AUTHORIZE_URL = f"{ISSUER}/authorize"
TOKEN_URL = f"{ISSUER}/token"


@dataclass
class Signer:
    """`FakeJwks`(서명 키)와 시계를 묶는다. 기본은 대표의 Flow API 토큰(aud=FLOW, `admin login`,
    client_id `client-123`, jti `jti-1`). 값이 None이면 그 claim을 뺀다."""

    jwks: FakeJwks
    clock: FixedClock

    def claims(self, **overrides: object) -> dict[str, object]:
        base = access_token_claims(self.clock, audience=FLOW, client_id="client-123", jti="jti-1")
        merged = {**base, **overrides}
        return {name: value for name, value in merged.items() if value is not None}

    def sign(self, claims: Mapping[str, object], headers: Mapping[str, object] | None = None) -> str:
        return self.jwks.key.sign(claims, headers=headers)

    def token(self, **overrides: object) -> str:
        return self.sign(self.claims(**overrides))

    def member_token(self, **overrides: object) -> str:
        return self.token(**{"sub": MEMBER_SUB, "acct": "member", "scope": "login", **overrides})


def use_fake_jwks(monkeypatch: pytest.MonkeyPatch, jwks: FakeJwks, clock: FixedClock) -> None:
    """어댑터의 시계·JWKS 캐시를 대역으로(진짜 `JwksCache`, 가짜 엔드포인트). 대표 sub는 `OWNER`."""
    monkeypatch.setattr(settings, "AUTH_OWNER_SUB", OWNER)
    cache = jwks.cache(clock)
    monkeypatch.setattr(authkit, "clock", clock)
    monkeypatch.setattr(authkit, "jwks_cache", lambda: cache)


class FlowAuthServer(FakeTokenEndpoint):
    """`FakeTokenEndpoint`에 Flow 시험 기본값을 얹는다: 토큰 스코프 `flow_scope`(기본 `admin login`, 응답
    scope는 `openid profile email` + 그것), ID 토큰 프로필(대표·owner@sandcorp.co.kr·확인됨)."""

    def approve(  # type: ignore[override]  # flow 시험의 인자 이름(flow_scope)을 지킨다
        self,
        authorize_url: str,
        *,
        sub: str = OWNER,
        acct: str | None = None,
        flow_scope: str = "admin login",
        id_token_overrides: Mapping[str, object] | None = None,
    ) -> dict[str, str]:
        profile = {"name": "대표", "email": "owner@sandcorp.co.kr", "email_verified": True}
        return super().approve(
            authorize_url,
            sub=sub,
            acct=acct,
            scope=f"openid profile email {flow_scope}",
            id_token_overrides={**profile, **(id_token_overrides or {})},
        )

    def approve_member(self, authorize_url: str, *, sub: str = MEMBER_SUB, scope: str | None = None) -> dict[str, str]:
        return self.approve(authorize_url, sub=sub, acct="member", flow_scope=scope or "login")

    @property
    def exchanges(self) -> int:
        return len(self.requests)


def flow_auth_server(monkeypatch: pytest.MonkeyPatch, jwks: FakeJwks, clock: FixedClock) -> FlowAuthServer:
    server = FlowAuthServer(jwks, clock, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    monkeypatch.setattr(authkit, "token_transport", lambda: server)
    return server
