"""`/mcp` 전송(Streamable HTTP의 JSON 응답 모드, 세션 없음)과 `/.well-known/oauth-protected-resource/mcp`.

POST 한 메시지 → JSON 한 답. 알림은 202, GET 등은 405, 배치는 400, 본문 상한 초과는 413.
인증은 MCP 리소스 검증기(`get_mcp_validator`, aud=`AUTH_MCP_RESOURCE`)로 `flow.read`가 기본이고, 도구마다 더 필요한
스코프는 `tools/call`에서 본다(모자라면 403 `insufficient_scope`).
"""

from __future__ import annotations

import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.mcp.protocol import (
    INVALID_REQUEST,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    dispatch,
    error_response,
    is_notification,
)
from apps.mcp.registry import MCP_SCOPES
from apps.tenants.resource_token import (
    AccessToken,
    InsufficientScope,
    TokenRejected,
    get_mcp_validator,
    protected_resource_metadata,
    site_origin,
    www_authenticate,
)

logger = logging.getLogger(__name__)

MCP_METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
MAX_BODY_BYTES = 1024 * 1024
BASE_SCOPE = "flow.read"


def _auth_failed(status: int, header: str) -> HttpResponse:
    body = {"error": "unauthorized" if status == 401 else "forbidden"}
    response = JsonResponse(body, status=status)
    response["WWW-Authenticate"] = header
    return response


def _rpc_error(status: int, code: int, message: str) -> JsonResponse:
    return JsonResponse(error_response(None, code, message), status=status)


async def _authenticate(request: HttpRequest) -> AccessToken | HttpResponse:
    validator = get_mcp_validator()
    origin = site_origin(validator.audience)
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():  # B1·B2
        return _auth_failed(401, www_authenticate(origin, metadata_path=MCP_METADATA_PATH))
    try:
        # JWKS 조회가 동기 HTTP라 스레드에서 돈다.
        return await sync_to_async(validator.validate)(token.strip(), required_scope=BASE_SCOPE)
    except TokenRejected as exc:
        logger.info("MCP token rejected: %s", exc)
        return _auth_failed(401, www_authenticate(origin, error="invalid_token", metadata_path=MCP_METADATA_PATH))
    except InsufficientScope as exc:
        return _auth_failed(403, www_authenticate(origin, error="insufficient_scope", scope=exc.required_scope))


@csrf_exempt
async def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response

    token = await _authenticate(request)
    if isinstance(token, HttpResponse):
        return token

    version = request.headers.get("MCP-Protocol-Version")
    if version is not None and version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _rpc_error(400, INVALID_REQUEST, f"unsupported MCP-Protocol-Version: {version}")

    declared = request.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES or len(request.body) > MAX_BODY_BYTES:
        return _rpc_error(413, INVALID_REQUEST, f"request body over {MAX_BODY_BYTES} bytes")

    try:
        message = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(400, PARSE_ERROR, "parse error")
    if isinstance(message, list):
        return _rpc_error(400, INVALID_REQUEST, "batches are not supported")
    if not isinstance(message, dict):
        return _rpc_error(400, INVALID_REQUEST, "request must be a JSON object")
    if is_notification(message):
        return HttpResponse(status=202)

    try:
        answer = await dispatch(message, token=token, issuer=get_mcp_validator().issuer)
    except InsufficientScope as exc:
        origin = site_origin(get_mcp_validator().audience)
        return _auth_failed(403, www_authenticate(origin, error="insufficient_scope", scope=exc.required_scope))
    return JsonResponse(answer)


@require_GET
def oauth_protected_resource_mcp(request: HttpRequest) -> JsonResponse:
    """RFC 9728 경로별 메타데이터 — MCP 리소스. 인증 없음."""
    body = protected_resource_metadata(
        resource=get_mcp_validator().audience, issuer=settings.AUTH_ISSUER, scopes=MCP_SCOPES
    )
    return JsonResponse(body)
