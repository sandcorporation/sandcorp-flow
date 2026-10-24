import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from jose import jwt, JWTError
from django.conf import settings
from ninja.security import HttpBearer

if TYPE_CHECKING:
    from apps.tenants.models import Membership
    from apps.tenants.resource_token import AccessToken

logger = logging.getLogger(__name__)


ALGORITHM = "HS256"
# 단수명 access(ADR-0013): 탈취 시 피해를 줄이려 짧게 유지. 만료는 refresh로 무중단 재발급.
ACCESS_TOKEN_EXPIRE_MINUTES = 30


OPERATOR_SESSION_TYPE = "operator_session"


def create_operator_access_token(operator, minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """Flow 자기 세션의 Operator Access Token(ADR-0013). sandcorp-auth 로그인 콜백 뒤 refresh가 발급한다.
    `minutes`를 바꾸는 곳은 e2e 시드(`seed_e2e_operator`)뿐이다."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = {
        "sub": str(operator.id),
        "type": OPERATOR_SESSION_TYPE,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm=ALGORITHM)


def verify_operator_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != OPERATOR_SESSION_TYPE:
            return None
        return payload
    except JWTError:
        return None


class ResourceAuthFailed(Exception):
    """Operator API 거부 — 5절 상태·`WWW-Authenticate` 헤더로 응답한다(config/api.py 처리기)."""

    def __init__(self, status: int, www_authenticate: str) -> None:
        super().__init__(status, www_authenticate)
        self.status = status
        self.www_authenticate = www_authenticate


OPERATOR_SCOPE = "admin"


class OperatorAuth(HttpBearer):
    """Operator API 가드. 두 경로를 받는다.

    1. Flow 세션 Access Token(HS256, ADR-0013) — sandcorp-auth 로그인 콜백(#21)이 연 세션.
       대표 `User`에 이어진 Operator만 통과한다. 비밀번호 로그인 시절 토큰(`type=operator`)은 받지 않는다.
    2. sandcorp-auth Resource Token(ADR-0028): A1을 통과한 대표 토큰(`acct` 없음,
       sub == AUTH_OWNER_SUB) + `admin` 스코프일 때만 연다. 일반 사용자 토큰(`acct=member`)은
       검증을 통과해도 403이다 — 스코프로 대표 여부를 판단하지 않는다.

    실패는 5절대로: 토큰 없음 401, 틀린 토큰 401 `invalid_token`, 권한 없음 403 `insufficient_scope`.
    """

    def __call__(self, request):
        from apps.tenants.resource_token import get_validator, site_origin, www_authenticate

        value = request.headers.get(self.header)
        parts = value.split(" ") if value else []
        if not parts or parts[0].lower() != self.openapi_scheme:  # B1·B2
            origin = site_origin(get_validator().audience)
            raise ResourceAuthFailed(401, www_authenticate(origin))
        return self.authenticate(request, " ".join(parts[1:]))

    def authenticate(self, request, token: str):
        from apps.tenants.models import Operator, User
        from apps.tenants.operator_login import owner_operator
        from apps.tenants.resource_token import get_validator, site_origin, www_authenticate

        payload = verify_operator_access_token(token)
        if payload:
            operator = Operator.objects.filter(id=payload["sub"], user__acct=User.Acct.OWNER).first()
            if operator is None:
                origin = site_origin(get_validator().audience)
                raise ResourceAuthFailed(401, www_authenticate(origin, error="invalid_token"))
            return operator

        verified = _verify_operator_resource_token(token)
        return owner_operator(get_validator().issuer, verified.sub)


def _verify_operator_resource_token(token: str) -> "AccessToken":
    from apps.tenants.resource_token import (
        InsufficientScope,
        TokenRejected,
        get_validator,
        site_origin,
        www_authenticate,
    )

    validator = get_validator()
    origin = site_origin(validator.audience)
    try:
        verified = validator.validate(token, required_scope=OPERATOR_SCOPE)
    except TokenRejected as exc:
        logger.info("Resource Token 거부: %s", exc)
        raise ResourceAuthFailed(401, www_authenticate(origin, error="invalid_token")) from exc
    except InsufficientScope as exc:
        logger.info("Resource Token 스코프 부족: %s 필요", exc.required_scope)
        raise ResourceAuthFailed(
            403, www_authenticate(origin, error="insufficient_scope", scope=exc.required_scope)
        ) from exc
    if verified.acct != "owner":  # 일반 사용자는 스코프와 무관하게 Operator가 아니다
        logger.info("Resource Token 거부: 일반 사용자 %s는 Operator가 아님", verified.sub)
        raise ResourceAuthFailed(
            403, www_authenticate(origin, error="insufficient_scope", scope=OPERATOR_SCOPE)
        )
    return verified


class TenantKeyAuth(HttpBearer):
    def authenticate(self, request, token: str):
        from apps.tenants.models import Tenant
        tenant = Tenant.verify_key(token)
        if not tenant:
            return None
        return tenant


MEMBER_SESSION_TYPE = "tenant_member"


def create_member_access_token(membership: "Membership") -> str:
    """테넌트 세션 Access Token(ADR-0013). `sub`=멤버십 id. `role`은 화면 게이팅용일 뿐 —
    인가는 DB의 `membership.role`로 판정한다."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(membership.id),
        "tenant_id": str(membership.tenant_id),
        "type": MEMBER_SESSION_TYPE,
        "role": membership.role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm=ALGORITHM)


def verify_member_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != MEMBER_SESSION_TYPE:
            return None
        return payload
    except JWTError:
        return None


def active_membership_for_token(token: str) -> "Membership | None":
    """토큰이 가리키는 멤버십이 지금도 활성(멤버십·Tenant 모두)이면 그것을, 아니면 None."""
    payload = verify_member_access_token(token)
    if not payload:
        return None
    from apps.tenants.models import Membership

    return (
        Membership.objects.select_related("tenant", "user")
        .filter(id=payload["sub"], is_active=True, tenant__is_active=True)
        .first()
    )


class MemberAuth(HttpBearer):
    """테넌트 API 가드 — 멤버 세션 Access Token. `request.auth`는 `Membership`."""

    def authenticate(self, request, token: str):
        return active_membership_for_token(token)


class MemberOrTenantKeyAuth(HttpBearer):
    """멤버 세션 또는 TENANT_KEY(Admin 등가 break-glass, ADR-0025 D). `request.auth`는
    `Membership` 또는 `Tenant`."""

    def authenticate(self, request, token: str):
        membership = active_membership_for_token(token)
        if membership is not None:
            return membership
        from apps.tenants.models import Tenant

        return Tenant.verify_key(token)


operator_auth = OperatorAuth()
tenant_key_auth = TenantKeyAuth()
member_auth = MemberAuth()
member_or_tenant_key_auth = MemberOrTenantKeyAuth()
