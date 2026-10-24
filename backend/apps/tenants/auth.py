import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from jose import jwt, JWTError
from django.conf import settings
from ninja.security import HttpBearer

if TYPE_CHECKING:
    from apps.tenants.resource_token import AccessToken

logger = logging.getLogger(__name__)


ALGORITHM = "HS256"
# 단수명 access(ADR-0013): 탈취 시 피해를 줄이려 짧게 유지. 만료는 refresh로 무중단 재발급.
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def create_operator_token(operator) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(operator.id),
        "type": "operator",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm=ALGORITHM)


def verify_operator_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "operator":
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

    1. 옛 Operator JWT(비밀번호 로그인, HS256) — #21에서 걷어낸다.
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
        from apps.tenants.models import Operator

        payload = verify_operator_token(token)
        if payload:
            return Operator.objects.filter(id=payload["sub"]).first()

        verified = _verify_operator_resource_token(token)
        operator, created = Operator.objects.get_or_create(username=verified.sub)
        if created:
            operator.set_unusable_password()
            operator.save(update_fields=["password"])
        return operator


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


def create_tenant_agent_token(agent) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(agent.id),
        "tenant_id": str(agent.tenant_id),
        "type": "tenant_agent",
        "role": agent.role,  # UI 게이팅용(백엔드 authz는 DB의 agent.role로 판정)
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm=ALGORITHM)


def verify_tenant_agent_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "tenant_agent":
            return None
        return payload
    except JWTError:
        return None


class TenantAgentAuth(HttpBearer):
    def authenticate(self, request, token: str):
        payload = verify_tenant_agent_token(token)
        if not payload:
            return None
        from apps.tenants.models import TenantAgent
        try:
            return TenantAgent.objects.select_related("tenant").get(
                id=payload["sub"], is_active=True
            )
        except TenantAgent.DoesNotExist:
            return None


operator_auth = OperatorAuth()
tenant_key_auth = TenantKeyAuth()
tenant_agent_auth = TenantAgentAuth()
