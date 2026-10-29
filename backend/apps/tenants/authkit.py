"""sandcorp-auth 인증 어댑터 — 이 저장소에서 인증 설정(`AUTH_*`)을 읽는 유일한 곳(#282, ADR-0028).

인증 코드(토큰 검증·RFC 9728·bearer·운영자 로그인)는 패키지 `sandcorp-authkit`(sandcorp-auth #54)에 있다.
여기서는 settings를 함수 인자(`BearerConfig`·`LoginConfig`)로 바꾸고 Flow 고유 규칙만 더한다.

- 계정 규칙: 대표(`acct` 없음, sub == `AUTH_OWNER_SUB`) 또는 일반 사용자(`acct="member"`) — `owner_or_member`.
- 리소스 둘: API(aud=`AUTH_RESOURCE`, 메타데이터는 경로 없는 `/.well-known/oauth-protected-resource`)와
  MCP(aud=`AUTH_MCP_RESOURCE`, RFC 9728 경로 `metadata_path` = `/.well-known/oauth-protected-resource/mcp`).
- 운영자 API 거부 본문은 기존 꼴 `{"detail": …}`(`operator_denial`). MCP는 패키지 본문 `{"error": …}` 그대로.
- 로그인 시도는 서명된 httpOnly 쿠키(`CookieAttemptStore`) — Flow에는 Django 세션이 없다.

시험이 바꾸는 자리(seam): `clock`, `jwks_cache`, `token_transport`.
"""

from __future__ import annotations

from functools import cache
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.http import HttpRequest, HttpResponse, JsonResponse

from sandcorp_authkit.django import BearerConfig, BearerError, deny
from sandcorp_authkit.login import LoginAttempt, LoginConfig, LoginResult, TokenTransport, complete_login
from sandcorp_authkit.resource import metadata_url
from sandcorp_authkit.tokens import (
    ClaimsHook,
    Clock,
    JwksCache,
    SystemClock,
    http_jwks_fetcher,
    owner_or_member,
)

clock: Clock = SystemClock()

OPERATOR_SCOPE = "admin"
API_SCOPES = ["admin", "login"]
# 진입마다 요청 스코프가 다르다(ADR-0028). 대표(Operator)는 `admin`을 함께 요청하고, 고객(member)은
# 요청하지 않는다 — member가 `admin`을 요청하면 sandcorp-auth가 `invalid_scope`로 돌려보낸다.
OPERATOR_REQUESTED_SCOPE = "openid profile email login admin"
MEMBER_REQUESTED_SCOPE = "openid profile email login"


@cache
def _jwks_for(url: str) -> JwksCache:
    return JwksCache(http_jwks_fetcher(url), clock)


def jwks_cache() -> JwksCache:
    """프로세스마다 하나(J1). API·MCP·로그인이 함께 쓴다."""
    return _jwks_for(settings.AUTH_JWKS_URL)


def account_rule() -> ClaimsHook:
    return owner_or_member(settings.AUTH_OWNER_SUB)


def _origin(resource: str) -> str:
    parts = urlsplit(resource)
    return f"{parts.scheme}://{parts.netloc}"


def api_metadata_url() -> str:
    """운영자 API의 `resource_metadata` — 지금처럼 경로 없는 메타데이터(`/.well-known/oauth-protected-resource`)."""
    return metadata_url(_origin(settings.AUTH_RESOURCE))


def _bearer_config(audience: str, resource_metadata_url: str) -> BearerConfig:
    return BearerConfig(
        issuer=settings.AUTH_ISSUER,
        audience=audience,
        resource_metadata_url=resource_metadata_url,
        jwks=jwks_cache(),
        clock=clock,
        extra_claims_hook=account_rule(),
    )


def api_bearer_config() -> BearerConfig:
    return _bearer_config(settings.AUTH_RESOURCE, api_metadata_url())


def mcp_bearer_config() -> BearerConfig:
    return _bearer_config(settings.AUTH_MCP_RESOURCE, metadata_url(settings.AUTH_MCP_RESOURCE))


_OPERATOR_DETAIL = {401: "Unauthorized", 403: "Forbidden", 503: "Service Unavailable"}


def operator_denial(request: HttpRequest, error: BearerError, *, scope: str | None = None) -> HttpResponse:
    """패키지 `deny()`의 상태·`WWW-Authenticate`는 그대로, 본문만 운영자 API의 기존 꼴 `{"detail": …}`로."""
    denied = deny(request, error, resource_metadata_url=api_metadata_url(), scope=scope)
    response = JsonResponse({"detail": _OPERATOR_DETAIL[denied.status_code]}, status=denied.status_code)
    if "WWW-Authenticate" in denied:
        response["WWW-Authenticate"] = denied["WWW-Authenticate"]
    return response


# ── 관리 화면 로그인(7절) ──────────────────────────────────────────────────────


def login_config(scope: str = OPERATOR_REQUESTED_SCOPE) -> LoginConfig:
    return LoginConfig(
        client_id=settings.AUTH_CLIENT_ID,
        client_secret=settings.AUTH_CLIENT_SECRET,
        redirect_uri=settings.AUTH_REDIRECT_URI,
        authorize_url=settings.AUTH_AUTHORIZE_URL,
        token_url=settings.AUTH_TOKEN_URL,
        issuer=settings.AUTH_ISSUER,
        resource=settings.AUTH_RESOURCE,
        scope=scope,
    )


def token_transport() -> TokenTransport | None:
    """`POST /token` 전송. None이면 패키지 기본(urllib). 시험은 `FakeTokenEndpoint`를 돌려주게 바꾼다."""
    return None


def finish_login(query: dict[str, str], store: CookieAttemptStore) -> LoginResult:
    """콜백 검증 끝까지(패키지 `complete_login`). 실패는 `LoginRejected`. 두 진입 모두 `openid`라 ID 토큰은 필수다."""
    return complete_login(
        query,
        login_config(),
        store,
        jwks=jwks_cache(),
        clock=clock,
        extra_claims_hook=account_rule(),
        transport=token_transport(),
    )


ATTEMPT_COOKIE = "op_login"
ATTEMPT_COOKIE_PATH = "/api/auth/callback"
_ATTEMPT_SALT = "flow.operator-login.attempt"


class CookieAttemptStore:
    """패키지 `AttemptStore`를 서명된 httpOnly 쿠키 한 칸으로. 만료(600초)는 패키지가 `started_at`으로 본다.

    `save`는 쿠키 값을 `saved`에 두기만 한다(응답에 심는 것은 뷰). `take`는 읽은 시도를 `taken`에도 남긴다 —
    거절 응답을 시작한 화면(관리 화면/고객 화면)으로 돌려보낼 때 쓴다. 쿠키 지우기는 콜백 응답이 한다."""

    def __init__(self, raw: str | None = None) -> None:
        self._raw = raw
        self.saved: str | None = None
        self.taken: LoginAttempt | None = None

    def save(self, attempt: LoginAttempt) -> None:
        self.saved = signing.dumps(attempt.to_dict(), salt=_ATTEMPT_SALT)

    def take(self) -> LoginAttempt | None:
        raw, self._raw = self._raw, None
        if not raw:
            return None
        try:
            data: object = signing.loads(raw, salt=_ATTEMPT_SALT)
        except signing.BadSignature:
            return None
        self.taken = LoginAttempt.from_dict(data)
        return self.taken
