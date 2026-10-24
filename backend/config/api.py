from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from apps.tenants.auth import ResourceAuthFailed
from apps.core.api import router as core_router
from apps.tenants.api import auth_router, invite_router, member_router, operator_router, tenant_router
from apps.chat.api import chat_router
from apps.rag.api import rag_router
from apps.memory.api import memory_router, session_router
from apps.escalation.api import escalation_router
from apps.usage.api import tenant_usage_router, operator_usage_router

api = NinjaAPI(title="Sandcorp Flow API", version="1.0.0")


@api.exception_handler(ResourceAuthFailed)
def _resource_auth_failed(request: HttpRequest, exc: ResourceAuthFailed) -> HttpResponse:
    """Operator API 거부(sandcorp-auth 규칙 5절). 거부 이유는 응답에 싣지 않는다(로그에만)."""
    detail = "Unauthorized" if exc.status == 401 else "Forbidden"
    response = api.create_response(request, {"detail": detail}, status=exc.status)
    response["WWW-Authenticate"] = exc.www_authenticate
    return response

api.add_router("/", core_router)
api.add_router("/auth", auth_router)
api.add_router("/operator", operator_router)
api.add_router("/tenant", tenant_router)
api.add_router("/tenant/members", member_router)
api.add_router("/tenant/invites", invite_router)
api.add_router("/chat", chat_router)
api.add_router("/tenant/documents", rag_router)
api.add_router("/tenant/visitors", memory_router)
api.add_router("/tenant/sessions", session_router)
api.add_router("/tenant/escalations", escalation_router)
api.add_router("/tenant/usage", tenant_usage_router)
api.add_router("/operator/usage", operator_usage_router)
