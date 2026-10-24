"""관리 화면 로그인 — sandcorp-auth 기밀 클라이언트(ADR-0028, #21 대표·#40 고객).

규칙: sandcorp-auth `docs/resource-server-token-validation.md` 7절(관리 화면 로그인)·10절(ID 토큰).

- `start_login()`            : state·PKCE(S256)·nonce를 만들고 `/authorize` 주소를 돌려준다.
- `complete_login(...)`      : 콜백 쿼리를 검증하고 코드를 교환해, 대표(A1 + `admin`)면 그 `Operator`를,
                               일반 사용자(`acct="member"`)면 `MemberLogin`(사용자 행 + 초대 토큰)을 돌려준다.
                               아니면 `LoginRejected(reason)`. 멤버십·테넌트 세션은 `members.py`가 정한다.
- `owner_operator(...)`      : 대표 `User(iss, sub)`와 거기 이은 `Operator`를 찾거나 만든다.

sandcorp-auth의 액세스·리프레시 토큰은 저장하지 않는다. 확인만 하고 버린 뒤 Flow 자기 세션(ADR-0013)을 연다.
토큰 엔드포인트는 외부 경계라 `use_token_endpoint(...)`로 바꿀 수 있다(시험 대역).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NotRequired, Protocol, TypedDict

import jwt
from django.conf import settings
from django.core import signing
from django.db import transaction

from apps.tenants.resource_token import (
    ALGORITHM,
    EXP_LEEWAY_SECONDS,
    AccessToken,
    InsufficientScope,
    ResourceTokenValidator,
    TokenRejected,
    get_validator,
)

if TYPE_CHECKING:
    from apps.tenants.models import Operator, User

logger = logging.getLogger(__name__)

LOGIN_SCOPE = "login"
OPERATOR_SCOPE = "admin"
# 진입마다 요청 스코프가 다르다(ADR-0028). 대표(Operator)는 `admin`을 함께 요청하고, 고객(member)은
# 요청하지 않는다 — member가 `admin`을 요청하면 sandcorp-auth가 `invalid_scope`로 돌려보낸다.
OPERATOR_REQUESTED_SCOPE = "openid profile email login admin"
MEMBER_REQUESTED_SCOPE = "openid profile email login"
LoginEntry = Literal["operator", "member"]
REQUESTED_SCOPE: dict[LoginEntry, str] = {
    "operator": OPERATOR_REQUESTED_SCOPE,
    "member": MEMBER_REQUESTED_SCOPE,
}
ATTEMPT_MAX_AGE_SECONDS = 600
TOKEN_TIMEOUT_SECONDS = 10
_ATTEMPT_SALT = "flow.operator-login.attempt"

RejectReason = Literal["denied", "state", "exchange", "token", "admin", "member", "owner", "config"]


class LoginRejected(Exception):
    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason)
        self.reason: RejectReason = reason


@dataclass(frozen=True)
class LoginAttempt:
    """한 번의 로그인 시도. 브라우저에는 서명된 httpOnly 쿠키로만 맡긴다."""

    state: str
    code_verifier: str
    nonce: str
    # 팀원 초대 링크로 시작한 로그인이면 그 원문 토큰(#40). 콜백에서 멤버십을 만든다.
    invite: str | None = None
    # 어느 화면에서 시작했나 — 요청 스코프와 콜백이 받을 계정 종류가 여기서 정해진다.
    entry: LoginEntry = "operator"

    def dumps(self) -> str:
        payload: dict[str, str] = {
            "s": self.state, "v": self.code_verifier, "n": self.nonce, "e": self.entry,
        }
        if self.invite:
            payload["i"] = self.invite
        return signing.dumps(payload, salt=_ATTEMPT_SALT)

    @classmethod
    def loads(cls, value: str | None) -> LoginAttempt | None:
        if not value:
            return None
        try:
            payload = signing.loads(value, salt=_ATTEMPT_SALT, max_age=ATTEMPT_MAX_AGE_SECONDS)
        except signing.BadSignature:  # 만료(SignatureExpired)도 여기로 온다
            return None
        if not isinstance(payload, dict):
            return None
        state, verifier, nonce = payload.get("s"), payload.get("v"), payload.get("n")
        if not (isinstance(state, str) and isinstance(verifier, str) and isinstance(nonce, str)):
            return None
        invite = payload.get("i")
        entry: LoginEntry = "member" if payload.get("e") == "member" else "operator"
        return cls(
            entry=entry,
            state=state,
            code_verifier=verifier,
            nonce=nonce,
            invite=invite if isinstance(invite, str) and invite else None,
        )


@dataclass(frozen=True)
class ClientConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    authorize_url: str
    token_url: str
    resource: str

    @classmethod
    def from_settings(cls) -> ClientConfig:
        return cls(
            client_id=settings.AUTH_CLIENT_ID,
            client_secret=settings.AUTH_CLIENT_SECRET,
            redirect_uri=settings.AUTH_REDIRECT_URI,
            authorize_url=settings.AUTH_AUTHORIZE_URL,
            token_url=settings.AUTH_TOKEN_URL,
            resource=settings.AUTH_RESOURCE,
        )

    @property
    def complete(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


# ── 토큰 엔드포인트(외부 경계) ─────────────────────────────────────────────────


class TokenResponse(TypedDict):
    access_token: str
    token_type: str
    id_token: NotRequired[str]
    scope: NotRequired[str]


class TokenExchangeFailed(Exception):
    pass


class TokenEndpoint(Protocol):
    def exchange(
        self, config: ClientConfig, *, code: str, code_verifier: str
    ) -> TokenResponse: ...


class HttpTokenEndpoint:
    """`POST /token` — `client_secret_basic`, `authorization_code` + PKCE `code_verifier`."""

    def exchange(self, config: ClientConfig, *, code: str, code_verifier: str) -> TokenResponse:
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
                "code_verifier": code_verifier,
            }
        ).encode()
        basic = base64.b64encode(
            f"{_form_quote(config.client_id)}:{_form_quote(config.client_secret)}".encode()
        ).decode()
        request = urllib.request.Request(
            config.token_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TOKEN_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise TokenExchangeFailed(str(exc)) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise TokenExchangeFailed("no access_token")
        return payload  # type: ignore[return-value]  # JSON 응답 — access_token만 위에서 좁혔다


def _form_quote(value: str) -> str:
    """RFC 6749 2.3.1: Basic 인증의 id·secret은 form-urlencoded로 감싼다."""
    return urllib.parse.quote_plus(value)


_token_endpoint: TokenEndpoint | None = None


def get_token_endpoint() -> TokenEndpoint:
    return _token_endpoint or HttpTokenEndpoint()


def use_token_endpoint(endpoint: TokenEndpoint | None) -> None:
    """시험이 결정적 인가 서버 대역을 넣을 때. None이면 HTTP로 돌아간다."""
    global _token_endpoint
    _token_endpoint = endpoint


# ── 로그인 흐름 ────────────────────────────────────────────────────────────────


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_login(
    config: ClientConfig | None = None,
    invite: str | None = None,
    entry: LoginEntry = "operator",
) -> tuple[str, LoginAttempt]:
    """`entry="operator"`는 관리 화면 대표 로그인, `"member"`는 고객 로그인(초대 링크 포함)."""
    if invite:
        entry = "member"  # 초대는 언제나 고객 진입이다
    config = config or ClientConfig.from_settings()
    if not config.complete:
        raise LoginRejected("config")
    attempt = LoginAttempt(
        state=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),  # 86자 — RFC 7636 43~128자
        nonce=secrets.token_urlsafe(32),
        invite=invite or None,
        entry=entry,
    )
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": REQUESTED_SCOPE[attempt.entry],
            "resource": config.resource,
            "state": attempt.state,
            "nonce": attempt.nonce,
            "code_challenge": code_challenge(attempt.code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{config.authorize_url}?{query}", attempt


class IdentityClaims(TypedDict):
    sub: str
    name: NotRequired[str]
    email: NotRequired[str]
    email_verified: NotRequired[bool]


@dataclass(frozen=True)
class MemberLogin:
    """검증을 통과한 일반 사용자. `invite`는 초대 링크로 시작한 로그인의 원문 토큰."""

    user: User
    invite: str | None


def complete_login(
    query: Mapping[str, str],
    attempt: LoginAttempt | None,
    config: ClientConfig | None = None,
) -> Operator | MemberLogin:
    """콜백을 끝까지 검증한다. 대표(A1 통과) + `admin`이면 그 Operator, 일반 사용자면 MemberLogin,
    아니면 LoginRejected."""
    config = config or ClientConfig.from_settings()
    if not config.complete:
        raise LoginRejected("config")
    if query.get("error"):
        logger.info("Operator 로그인: 인가 서버가 거절함 (%s)", query.get("error"))
        raise LoginRejected("denied")
    state = query.get("state", "")
    if attempt is None or not hmac.compare_digest(state.encode(), attempt.state.encode()):
        logger.info("Operator 로그인: state 불일치 또는 시도 쿠키 없음")
        raise LoginRejected("state")
    code = query.get("code")
    if not code:
        raise LoginRejected("exchange")

    try:
        tokens = get_token_endpoint().exchange(
            config, code=code, code_verifier=attempt.code_verifier
        )
    except TokenExchangeFailed as exc:
        logger.info("Operator 로그인: 코드 교환 실패 (%s)", exc)
        raise LoginRejected("exchange") from exc

    validator = get_validator()
    try:
        access = validator.validate(tokens["access_token"], required_scope=LOGIN_SCOPE)
    except (TokenRejected, InsufficientScope) as exc:
        logger.info("Operator 로그인: 액세스 토큰 거부 (%r)", exc)
        raise LoginRejected("token") from exc
    if access.client_id != config.client_id:
        logger.info("Operator 로그인: 다른 클라이언트의 토큰")
        raise LoginRejected("token")
    identity = _verify_id_token(tokens.get("id_token"), validator, config, attempt.nonce, access)

    # ADR-0028: Operator는 `acct` 없음(A1) + `admin`뿐이다. 진입과 계정 종류가 엇갈리면 거절한다.
    if attempt.entry == "member":
        if access.acct != "member":
            raise LoginRejected("owner")  # 대표는 고객 화면으로 들어오지 않는다
        from apps.tenants.members import member_user

        return MemberLogin(
            user=member_user(validator.issuer, access.sub, identity), invite=attempt.invite
        )
    if access.acct == "member":
        raise LoginRejected("member")  # 고객은 관리 화면(Operator)으로 들어오지 않는다
    if OPERATOR_SCOPE not in access.scopes:
        raise LoginRejected("admin")
    return owner_operator(validator.issuer, access.sub, identity)


def _verify_id_token(
    id_token: str | None,
    validator: ResourceTokenValidator,
    config: ClientConfig,
    nonce: str,
    access: AccessToken,
) -> IdentityClaims:
    """10절: EdDSA·같은 JWKS, `iss`·`aud`(=client_id)·`exp`·`nonce`, 그리고 액세스 토큰과 같은 `sub`."""
    if not id_token:
        raise LoginRejected("token")
    try:
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str):
            raise LoginRejected("token")
        key = validator.jwks.key_for(kid)
        if key is None:
            raise LoginRejected("token")
        claims: dict[str, object] = jwt.decode(
            id_token,
            key,
            algorithms=[ALGORITHM],
            audience=config.client_id,
            issuer=validator.issuer,
            options={"require": ["iss", "sub", "aud", "exp", "iat"], "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        logger.info("Operator 로그인: ID 토큰 거부 (%s)", exc)
        raise LoginRejected("token") from exc
    exp = claims["exp"]
    if not isinstance(exp, int) or validator.clock.now().timestamp() > exp + EXP_LEEWAY_SECONDS:
        raise LoginRejected("token")
    claim_nonce = claims.get("nonce")
    if not isinstance(claim_nonce, str) or not hmac.compare_digest(
        claim_nonce.encode(), nonce.encode()
    ):
        logger.info("Operator 로그인: nonce 불일치")
        raise LoginRejected("token")
    if claims["sub"] != access.sub:
        raise LoginRejected("token")

    identity = IdentityClaims(sub=access.sub)
    name, email, verified = claims.get("name"), claims.get("email"), claims.get("email_verified")
    if isinstance(name, str):
        identity["name"] = name
    if isinstance(email, str):
        identity["email"] = email
    if isinstance(verified, bool):
        identity["email_verified"] = verified
    return identity


def owner_operator(iss: str, sub: str, identity: IdentityClaims | None = None) -> Operator:
    """대표 `User(iss, sub)`를 찾거나 만들고(프로필 캐시 갱신), 거기 이은 Operator를 돌려준다."""
    from apps.tenants.models import Operator, User

    with transaction.atomic():
        user, _ = User.objects.get_or_create(
            iss=iss, sub=sub, defaults={"acct": User.Acct.OWNER}
        )
        if identity is not None:
            user.name = identity.get("name")
            user.email = identity.get("email")
            user.email_verified = identity.get("email_verified")
            user.save(update_fields=["name", "email", "email_verified", "updated_at"])
        operator = Operator.objects.filter(user=user).first()
        if operator is None:
            operator, op_created = Operator.objects.get_or_create(username=sub)
            if op_created:
                operator.set_unusable_password()
            operator.user = user
            operator.save()
    return operator
