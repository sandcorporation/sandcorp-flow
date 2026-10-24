"""온보딩 — 멤버십 없는 member가 조직(Tenant)을 만든다 (#41, deep module).

- `create_tenant_for(user, name, slug)` : 조직 + 그 사람의 Admin 멤버십을 한 트랜잭션에 만든다.
  이름·slug 형식/중복, 사용자당 생성 상한을 검사하고 어긋나면 `OnboardingRejected`.
- `allow_attempt(ip)`                    : IP당 시간당 시도 상한(Redis 고정 윈도우).
- `client_ip(request)`                   : 프록시 뒤에서 클라이언트 IP.

새 조직은 체험 턴(`TRIAL_TURNS`)을 받고(모델 기본값), 기존 챗 설정 그대로 만든다. 기본 흐름은 #23 몫이다.
"""

from __future__ import annotations

import secrets
from typing import Literal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest

from apps.tenants.models import Membership, Tenant, User
from apps.tenants.slug import is_valid_slug, normalize_slug

MAX_NAME_LEN = 255
RATE_WINDOW_SECONDS = 3600

RejectReason = Literal["name_invalid", "slug_invalid", "name_taken", "slug_taken", "limit_reached"]

REJECT_MESSAGES: dict[RejectReason, str] = {
    "name_invalid": "조직 이름을 1~255자로 넣어 주세요.",
    "slug_invalid": "주소는 한글·영문·숫자와 하이픈(-)만 쓸 수 있어요(앞뒤·연속 하이픈, 예약어 제외).",
    "name_taken": "이미 쓰는 조직 이름이에요.",
    "slug_taken": "이미 쓰는 주소예요.",
    "limit_reached": "한 계정으로 만들 수 있는 조직 수를 다 썼어요.",
}


class OnboardingRejected(Exception):
    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason)
        self.reason: RejectReason = reason

    @property
    def message(self) -> str:
        return REJECT_MESSAGES[self.reason]


def create_tenant_for(user: User, name: str, slug: str) -> Membership:
    clean_name = (name or "").strip()
    clean_slug = normalize_slug(slug)
    if not clean_name or len(clean_name) > MAX_NAME_LEN:
        raise OnboardingRejected("name_invalid")
    if not is_valid_slug(clean_slug):
        raise OnboardingRejected("slug_invalid")
    if Tenant.objects.filter(created_by=user).count() >= settings.TENANT_CREATE_LIMIT_PER_USER:
        raise OnboardingRejected("limit_reached")
    if Tenant.objects.filter(name__iexact=clean_name).exists():
        raise OnboardingRejected("name_taken")
    if Tenant.objects.filter(slug__iexact=clean_slug).exists():
        raise OnboardingRejected("slug_taken")
    try:
        with transaction.atomic():
            tenant = Tenant.objects.create_with_key(name=clean_name, raw_key=secrets.token_urlsafe(32))
            tenant.slug = clean_slug
            tenant.created_by = user
            tenant.save(update_fields=["slug", "created_by"])
            return Membership.objects.create(user=user, tenant=tenant, role=Membership.ROLE_ADMIN)
    except IntegrityError:
        # 검사와 저장 사이에 같은 이름·slug가 먼저 들어온 경우(unique 제약이 최종 판정).
        if Tenant.objects.filter(name__iexact=clean_name).exists():
            raise OnboardingRejected("name_taken") from None
        raise OnboardingRejected("slug_taken") from None


def client_ip(request: HttpRequest) -> str:
    """X-Forwarded-For에서 우리 프록시들이 덧붙인 값만 믿는다. 프록시 N단(`TRUSTED_PROXY_COUNT`, 운영은
    NPM→nginx 2단)이면 끝에서 N번째가 첫 프록시가 본 클라이언트 IP다 — 그 앞은 클라이언트가 위조할 수 있다.
    XFF가 없으면 REMOTE_ADDR."""
    hops = max(1, settings.TRUSTED_PROXY_COUNT)
    forwarded = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if forwarded:
        return forwarded[-hops] if len(forwarded) >= hops else forwarded[0]
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def allow_attempt(ip: str) -> bool:
    from apps.chat.sse import get_redis_client

    r = get_redis_client()
    key = f"ratelimit:onboarding:{ip}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, RATE_WINDOW_SECONDS)
    # redis-py 스텁은 incr 반환을 동기/비동기 합 타입으로 적는다 — 동기 클라이언트라 실제로는 int.
    return int(count) <= settings.ONBOARDING_RATE_LIMIT_PER_HOUR  # pyright: ignore[reportArgumentType]
