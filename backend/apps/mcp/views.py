"""`/mcp` 전송(Streamable HTTP의 JSON 응답 모드, 세션 없음)과 `/.well-known/oauth-protected-resource/mcp`.

POST 한 메시지 → JSON 한 답. 알림은 202, GET 등은 405, 배치는 400, 본문 상한 초과는 413.
인증은 sandcorp-authkit(어댑터 `authkit.mcp_bearer_config`, aud=`AUTH_MCP_RESOURCE`)로 `flow.read`가 기본이고, 도구마다 더 필요한
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
from apps.tenants import authkit
from sandcorp_authkit.django import (
    AuthNotConfigured,
    MissingToken,
    authenticate,
    deny,
    metadata_response,
)
from sandcorp_authkit.tokens import AccessToken, InsufficientScope, TokenRejected

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024
BASE_SCOPE = "flow.read"


def _rpc_error(status: int, code: int, message: str) -> JsonResponse:
    return JsonResponse(error_response(None, code, message), status=status)


async def _authenticate(request: HttpRequest) -> AccessToken | HttpResponse:
    config = authkit.mcp_bearer_config()
    try:
        # JWKS 조회가 동기 HTTP라 스레드에서 돈다.
        return await sync_to_async(authenticate)(request, BASE_SCOPE, config)
    except (AuthNotConfigured, MissingToken, TokenRejected, InsufficientScope) as exc:
        return deny(request, exc, resource_metadata_url=config.resource_metadata_url)


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
        answer = await dispatch(message, token=token, issuer=settings.AUTH_ISSUER)
    except InsufficientScope as exc:
        return deny(request, exc, resource_metadata_url=authkit.mcp_bearer_config().resource_metadata_url)
    return JsonResponse(answer)


@require_GET
def oauth_protected_resource_mcp(request: HttpRequest) -> JsonResponse:
    """RFC 9728 경로별 메타데이터 — MCP 리소스. 인증 없음."""
    return metadata_response(settings.AUTH_MCP_RESOURCE, settings.AUTH_ISSUER, MCP_SCOPES)
