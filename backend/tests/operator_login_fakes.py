"""관리 화면 로그인 시험 도구: sandcorp-auth `/authorize`·`/token`의 결정적 대역.

토큰은 실제 Ed25519 키(`resource_token_fakes.Signer`)로 서명한다. 대역이 하는 일은 인가 서버가 하는 일
그대로다 — 등록된 클라이언트·리다이렉트 URI 확인, 코드에 PKCE challenge·nonce를 묶어 두기,
`/token`에서 client_secret과 `code_verifier`를 확인해 액세스 토큰·ID 토큰 발급. 검증은 Flow 코드가 한다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from apps.tenants.operator_login import (
    ClientConfig,
    TokenExchangeFailed,
    TokenResponse,
    code_challenge,
)
from resource_token_fakes import FLOW, ISSUER, MEMBER_SUB, OWNER, Signer

CLIENT_ID = "flow-admin"
CLIENT_SECRET = "flow-admin-secret"
REDIRECT_URI = "https://flow.sandcorp.co.kr/api/operator/auth/callback"
AUTHORIZE_URL = f"{ISSUER}/authorize"
TOKEN_URL = f"{ISSUER}/token"


@dataclass
class Grant:
    challenge: str
    nonce: str
    redirect_uri: str
    sub: str
    acct: str | None
    flow_scope: str
    id_token_overrides: dict[str, object]


@dataclass
class FakeAuthorizationServer:
    signer: Signer
    codes: dict[str, Grant] = field(default_factory=dict)
    exchanges: int = 0

    def approve(
        self,
        authorize_url: str,
        *,
        sub: str = OWNER,
        acct: str | None = None,
        flow_scope: str = "admin login",
        id_token_overrides: dict[str, object] | None = None,
    ) -> dict[str, str]:
        """사용자가 로그인·동의를 마친 것처럼 코드를 발급하고 콜백 쿼리(code, state)를 돌려준다."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert authorize_url.startswith(AUTHORIZE_URL + "?")
        assert params["response_type"] == "code"
        assert params["client_id"] == CLIENT_ID
        assert params["redirect_uri"] == REDIRECT_URI
        assert params["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.codes[code] = Grant(
            challenge=params["code_challenge"],
            nonce=params["nonce"],
            redirect_uri=params["redirect_uri"],
            sub=sub,
            acct=acct,
            flow_scope=flow_scope,
            id_token_overrides=id_token_overrides or {},
        )
        return {"code": code, "state": params["state"]}

    def approve_member(self, authorize_url: str) -> dict[str, str]:
        return self.approve(authorize_url, sub=MEMBER_SUB, acct="member", flow_scope="login")

    def exchange(self, config: ClientConfig, *, code: str, code_verifier: str) -> TokenResponse:
        self.exchanges += 1
        if (config.client_id, config.client_secret) != (CLIENT_ID, CLIENT_SECRET):
            raise TokenExchangeFailed("invalid_client")
        grant = self.codes.pop(code, None)  # 코드는 한 번만
        if grant is None or grant.redirect_uri != config.redirect_uri:
            raise TokenExchangeFailed("invalid_grant")
        if code_challenge(code_verifier) != grant.challenge:
            raise TokenExchangeFailed("invalid_grant: PKCE")

        account = {"acct": grant.acct} if grant.acct else {}
        access = self.signer.token(
            sub=grant.sub, aud=FLOW, client_id=CLIENT_ID, scope=grant.flow_scope, **account
        )
        now = int(self.signer.clock.now().timestamp())
        id_claims: dict[str, object] = {
            "iss": ISSUER,
            "sub": grant.sub,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 600,
            "auth_time": now,
            "nonce": grant.nonce,
            "name": "대표",
            "email": "owner@sandcorp.co.kr",
            "email_verified": True,
            **account,
        }
        for name, value in grant.id_token_overrides.items():
            if value is None:
                id_claims.pop(name, None)
            else:
                id_claims[name] = value
        return {
            "access_token": access,
            "token_type": "Bearer",
            "id_token": self.signer.sign(id_claims),
            "scope": f"openid profile email {grant.flow_scope}",
        }
