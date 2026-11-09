from typing import TypedDict

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from apps.tenants.secrets_api import domain_router, secret_router
from apps.tenants.auth import ResourceAuthFailed
from apps.core.api import router as core_router
from apps.tenants.api import auth_router, invite_router, member_router, operator_router, tenant_router
from apps.chat.api import chat_router
from apps.rag.api import rag_router
from apps.memory.api import memory_router, session_router
from apps.escalation.api import escalation_router
from apps.flows.api import deploy_request_router, error_body, flow_router
from apps.flows.services import FlowServiceError
from apps.usage.api import tenant_usage_router, operator_usage_router
from apps.tenants.try_api import try_router
from apps.flows.public_api import public_router
from apps.mcp.api import audit_router

class DocsRoutes(TypedDict):
    docs_url: str | None
    openapi_url: str | None


def docs_routes(enabled: bool) -> DocsRoutes:
    """API 문서 경로. 운영(API_DOCS_ENABLED=False)에서는 /api/docs·/api/openapi.json을 끈다(#301). 개발·시험은 열어 둔다."""
    return {"docs_url": "/docs" if enabled else None, "openapi_url": "/openapi.json" if enabled else None}


api = NinjaAPI(title="Sandcorp Flow API", version="1.0.0", **docs_routes(settings.API_DOCS_ENABLED))


@api.exception_handler(FlowServiceError)
def _flow_service_error(request: HttpRequest, exc: FlowServiceError) -> HttpResponse:
    """흐름 서비스 오류 → ``{detail, code, ...}`` + 상태 코드(``apps.flows.api.error_body``)."""
    status, body = error_body(exc)
    return api.create_response(request, body, status=status)


@api.exception_handler(ResourceAuthFailed)
def _resource_auth_failed(request: HttpRequest, exc: ResourceAuthFailed) -> HttpResponse:
    """Operator API 거부(sandcorp-auth 규칙 5절). 응답은 어댑터(`authkit.operator_denial`)가 만든다 —
    거부 이유는 응답에 싣지 않는다(로그에만)."""
    return exc.response

api.add_router("/", core_router)
api.add_router("/auth", auth_router)
api.add_router("/operator", operator_router)
api.add_router("/tenant", tenant_router)
api.add_router("/tenant/flows", flow_router)
api.add_router("/tenant/deploy-requests", deploy_request_router)
api.add_router("/tenant/members", member_router)
api.add_router("/tenant/invites", invite_router)
api.add_router("/tenant/secrets", secret_router)
api.add_router("/tenant/allowed-domains", domain_router)
api.add_router("/chat", chat_router)
api.add_router("/tenant/documents", rag_router)
api.add_router("/tenant/visitors", memory_router)
api.add_router("/tenant/sessions", session_router)
api.add_router("/tenant/escalations", escalation_router)
api.add_router("/tenant/usage", tenant_usage_router)
api.add_router("/tenant/audit-events", audit_router)  # MCP 쓰기 감사 로그(#208)
api.add_router("/operator/usage", operator_usage_router)
api.add_router("/try", try_router)  # 공개(인증 없음) — 세션 쿠키로 자기 체험 테넌트만(#135)
api.add_router("/public", public_router)  # 공개(인증 없음) — 공개 흐름 보기 읽기 전용(#39)
