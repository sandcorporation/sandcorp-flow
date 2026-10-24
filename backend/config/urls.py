from django.http import HttpRequest, JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET

from config.api import api


@require_GET
def oauth_protected_resource(request: HttpRequest) -> JsonResponse:
    """RFC 9728 보호 리소스 메타데이터(sandcorp-auth 규칙 6절). 인증 없음."""
    from apps.tenants.resource_token import get_validator, protected_resource_metadata
    from django.conf import settings

    body = protected_resource_metadata(resource=get_validator().audience, issuer=settings.AUTH_ISSUER)
    return JsonResponse(body)


urlpatterns = [
    path("api/", api.urls),
    path(".well-known/oauth-protected-resource", oauth_protected_resource),
]
