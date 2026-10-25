"""공개 흐름 보기 API(#39) — 인증 없음, 읽기 전용.

``GET /api/public/flows/{slug}/``: 공개 보기가 켜진 테넌트의 공개 흐름 배포 버전(허용 목록 투영, ``public_view``).
- 없는 slug·공개 보기 꺼짐·보관·미배포는 모두 **같은 404**(본문·헤더가 같다 — 존재 오라클 없음). 404·429는 ``no-store``.
- 200은 ``Cache-Control: public, max-age=60`` — 공개 보기를 끈 뒤에도 프록시·브라우저에 최대 60초 남을 수 있다.
- IP당 분당 ``PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE``(기본 60, Redis 고정 윈도우). IP는 ``client_ip``(XFF 끝에서 신뢰 프록시 수).
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja import Router, Schema

from apps.flows.public_view import public_flow
from apps.flows.schema import JSON

public_router = Router(tags=["public"], auth=None)

WINDOW_SECONDS = 60
NOT_FOUND = {"detail": "Not Found"}


class PublicFlowOut(Schema):
    slug: str
    brand_name: str
    flow_name: str
    version: int
    graph: dict[str, list[dict[str, JSON]]]
    template: str | None


class PublicDetailOut(Schema):
    detail: str


def _allow(ip: str) -> bool:
    from apps.chat.sse import get_redis_client

    r = get_redis_client()
    key = f"ratelimit:public_flow:{ip}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, WINDOW_SECONDS)
    return count <= settings.PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE


@public_router.get("/flows/{slug}/", response={200: PublicFlowOut, 404: PublicDetailOut, 429: PublicDetailOut})
def get_public_flow(request: HttpRequest, slug: str, response: HttpResponse):
    from apps.tenants.onboarding import client_ip

    if not _allow(client_ip(request)):
        response["Cache-Control"] = "no-store"
        response["Retry-After"] = str(WINDOW_SECONDS)
        return 429, {"detail": "요청이 너무 많습니다. 잠시 뒤 다시 열어 주세요."}
    found = public_flow(slug)
    if found is None:
        response["Cache-Control"] = "no-store"
        return 404, NOT_FOUND
    response["Cache-Control"] = "public, max-age=60"
    return 200, found
