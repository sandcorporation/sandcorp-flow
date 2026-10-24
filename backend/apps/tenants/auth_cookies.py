"""Refresh 쿠키 plumbing (ADR-0013).

set/clear가 동일한 name·path를 쓰도록 한 곳에 모은다. 경로는 각 subject의 auth
서브트리로 한정 → refresh·logout 엔드포인트에만 쿠키가 전송되어 노출면을 최소화한다.
"""
from django.conf import settings

from apps.tenants.models import Membership, Operator
from apps.tenants.refresh_tokens import REFRESH_ABSOLUTE_LIFETIME

OPERATOR = "operator"
MEMBER = "member"

# (쿠키 이름, 경로)
_COOKIE = {
    OPERATOR: ("op_refresh", "/api/operator/auth"),
    MEMBER: ("member_refresh", "/api/tenant/members/auth"),
}


def _kind(subject) -> str:
    if isinstance(subject, Operator):
        return OPERATOR
    if isinstance(subject, Membership):
        return MEMBER
    raise TypeError(f"지원하지 않는 subject 타입: {type(subject)!r}")


def cookie_name(kind: str) -> str:
    return _COOKIE[kind][0]


def set_refresh_cookie(response, subject, raw: str) -> None:
    name, path = _COOKIE[_kind(subject)]
    response.set_cookie(
        name,
        raw,
        max_age=int(REFRESH_ABSOLUTE_LIFETIME.total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,  # dev(http localhost)에선 False, prod(https)에선 True
        samesite="Strict",
        path=path,
    )


def clear_refresh_cookie(response, kind: str) -> None:
    name, path = _COOKIE[kind]
    response.delete_cookie(name, path=path)
