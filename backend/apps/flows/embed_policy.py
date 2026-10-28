"""허용 사이트(임베드 출처) 목록과 위젯 주소의 frame-ancestors 정책(#248, ADR-0034 E3).

- 목록은 두 단계다. 테넌트 목록(``TenantConfig.embed_origins``)은 기본 흐름 주소 ``/chatbot/<slug>/``와 자기 목록이 없는
  흐름에 쓰고, 흐름 목록(``Flow.embed_origins``)은 비어 있지 않으면 그 흐름의 키 주소에서 테넌트 목록을 **대신한다**.
- 목록이 비면 ``*``(어디서나 붙일 수 있다 — 도입 전과 같다), 아니면 ``'self'`` + 목록. 없는 slug·키는 ``'self'``.
- nginx가 ``auth_request``로 ``/_internal/embed-policy``(``views.embed_policy``)를 불러 이 값을 CSP 헤더에 붙인다.
- 값은 Redis에 짧게(``POLICY_TTL``) 캐시하고, 목록을 저장하면 ``forget_tenant``로 지운다. Redis가 죽어도 DB로 답한다.

붙이기 통제이지 접근 통제가 아니다 — 주소를 직접 열거나 API를 부르는 것은 막지 않는다.
"""

from __future__ import annotations

import logging
import re
import uuid
from urllib.parse import unquote, urlsplit

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_EMBED_ORIGINS = 20
POLICY_TTL = 60  # 초
ANYWHERE = "*"
SELF_ONLY = "'self'"

_CACHE_KEY = "embed:fa:{}"  # 테넌트별 해시: 필드 = 흐름 키(기본 흐름 주소는 "")

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ORIGIN = re.compile(
    rf"^(?P<scheme>https?)://(?P<wild>\*\.)?(?P<host>{_LABEL}(?:\.{_LABEL})*)(?::(?P<port>\*|[0-9]{{1,5}}))?$"
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
# CSP 값에 그대로 들어가므로 출처를 하나 더 끼우거나(공백·쉼표) 지시문·키워드를 끼우는(세미콜론·따옴표) 글자를 막는다.
_FORBIDDEN = re.compile(r"[\s;,'\"]")
_CHATBOT_PATH = re.compile(r"^/chatbot/([^/]+)(?:/([^/]+))?")


class EmbedOriginsInvalid(ValueError):
    """허용 사이트 목록이 규칙에 맞지 않는다. ``message``는 화면에 그대로 보인다."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _origin_problem(item: str) -> str | None:
    m = _ORIGIN.match(item)
    if m is None:
        return "scheme://host[:port] 모양이어야 합니다(경로·쿼리·키워드 불가)"
    scheme, wild, host, port = m["scheme"], m["wild"], m["host"], m["port"]
    local = host in _LOCAL_HOSTS
    if scheme == "http" and not local:
        return "http는 localhost·127.0.0.1에만 쓸 수 있습니다"
    if wild and (local or host.count(".") < 1):
        return "와일드카드는 https://*.example.com 처럼 도메인 앞에만 씁니다"
    if port == "*" and not (scheme == "http" and local):
        return "포트 *는 http://localhost·127.0.0.1에만 쓸 수 있습니다"
    if port not in (None, "*") and not 1 <= int(port) <= 65535:
        return "포트가 범위를 벗어났습니다"
    return None


def normalize_embed_origins(values: list[str]) -> list[str]:
    """허용 사이트 목록을 검증하고 정규화한다(소문자·끝 빗금 제거·중복 제거, 순서 유지). 어기면 ``EmbedOriginsInvalid``.

    앞뒤 공백과 빈 항목은 무시한다. 항목 안의 공백·세미콜론·쉼표·따옴표는 헤더 주입이라 거부한다.
    """
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise EmbedOriginsInvalid("허용 사이트는 글자여야 합니다")
        item = raw.strip()
        if not item:
            continue
        if _FORBIDDEN.search(item):
            raise EmbedOriginsInvalid(f"허용 사이트 「{item}」에 공백·세미콜론·쉼표·따옴표를 쓸 수 없습니다")
        item = item.lower()
        if item.endswith("/") and item.count("/") == 3:
            item = item[:-1]
        problem = _origin_problem(item)
        if problem:
            raise EmbedOriginsInvalid(f"허용 사이트 「{raw.strip()}」: {problem}")
        if item not in out:
            out.append(item)
    if len(out) > MAX_EMBED_ORIGINS:
        raise EmbedOriginsInvalid(f"허용 사이트는 {MAX_EMBED_ORIGINS}개까지입니다")
    return out


def _value(origins: list[str]) -> str:
    return " ".join([SELF_ONLY, *origins]) if origins else ANYWHERE


def _compute(tenant_id: uuid.UUID, key: str) -> str:
    from apps.flows.models import Flow
    from apps.tenants.models import TenantConfig

    tenant_origins = TenantConfig.objects.filter(tenant_id=tenant_id).values_list("embed_origins", flat=True).first() or []
    if not key:
        return _value(tenant_origins)  # 기본 흐름 주소는 테넌트 목록만 본다
    flow_origins = Flow.objects.for_tenant(tenant_id).active().filter(embed_key=key).values_list("embed_origins", flat=True).first()
    if flow_origins is None:
        return SELF_ONLY
    return _value(flow_origins or tenant_origins)


def _redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)


def frame_ancestors(slug: str, key: str) -> str:
    """위젯 주소(slug, 흐름 키 — 기본 흐름 주소는 "")의 frame-ancestors 값."""
    from apps.tenants.models import Tenant

    tenant = Tenant.resolve_slug(slug)
    if tenant is None:
        return SELF_ONLY
    cache_key, field = _CACHE_KEY.format(tenant.id), key
    try:
        cached = _redis().hget(cache_key, field)
        if cached is not None:
            return cached.decode()
    except redis.RedisError:
        logger.warning("임베드 정책 캐시를 읽지 못했다 — DB로 답한다", exc_info=True)
    value = _compute(tenant.id, key)
    try:
        pipe = _redis().pipeline()
        pipe.hset(cache_key, field, value)
        pipe.expire(cache_key, POLICY_TTL)
        pipe.execute()
    except redis.RedisError:
        logger.warning("임베드 정책 캐시를 쓰지 못했다", exc_info=True)
    return value


def frame_ancestors_for_uri(uri: str) -> str:
    """nginx가 넘긴 원래 요청 주소(``$request_uri``, 쿼리·퍼센트 인코딩 포함)로 판정한다. ``/chatbot/`` 밖은 ``'self'``."""
    m = _CHATBOT_PATH.match(unquote(urlsplit(uri).path))
    if m is None:
        return SELF_ONLY
    return frame_ancestors(m[1], m[2] or "")


def forget_tenant(tenant_id: uuid.UUID) -> None:
    """테넌트의 캐시된 정책을 지운다 — 테넌트·흐름 목록을 저장한 뒤 부른다."""
    try:
        _redis().delete(_CACHE_KEY.format(tenant_id))
    except redis.RedisError:
        logger.warning("임베드 정책 캐시를 지우지 못했다 — %s초 뒤 만료된다", POLICY_TTL, exc_info=True)
