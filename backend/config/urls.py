from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET

from apps.mcp.views import mcp_endpoint, oauth_protected_resource_mcp
from config.api import api


@require_GET
def oauth_protected_resource(request: HttpRequest) -> JsonResponse:
    """RFC 9728 보호 리소스 메타데이터(sandcorp-auth 규칙 6절). 인증 없음."""
    from apps.tenants.resource_token import get_validator, protected_resource_metadata
    from django.conf import settings

    body = protected_resource_metadata(resource=get_validator().audience, issuer=settings.AUTH_ISSUER)
    return JsonResponse(body)


@require_GET
def embed_policy(request: HttpRequest) -> HttpResponse:
    """nginx ``auth_request``가 위젯 주소(``/chatbot/…``)마다 부르는 내부 정책(#248, ADR-0034). 인증 없음.

    원래 주소는 ``X-Original-URI``(nginx ``$request_uri``)로 받고 frame-ancestors 값을 ``X-Frame-Ancestors``에 실어 204로 답한다.
    nginx는 ``/api/``·``/mcp``·``/.well-known`` 만 앱으로 넘기므로 이 경로는 바깥에서 닿지 않는다(nginx ``internal`` 위치로만).
    """
    from apps.flows.embed_policy import frame_ancestors_for_uri

    response = HttpResponse(status=204)
    response["X-Frame-Ancestors"] = frame_ancestors_for_uri(request.headers.get("X-Original-URI", ""))
    return response


urlpatterns = [
    path("api/", api.urls),
    path("_internal/embed-policy", embed_policy),
    path(".well-known/oauth-protected-resource", oauth_protected_resource),
    # MCP 서버(#74): API와 다른 리소스(aud …/mcp)라 메타데이터도 경로별(RFC 9728 3.1).
    path("mcp", mcp_endpoint),
    path(".well-known/oauth-protected-resource/mcp", oauth_protected_resource_mcp),
]
