"""MCP JSON-RPC 처리(#75) — `initialize`·`ping`·`tools/list`·`tools/call`.

전송(HTTP 상태·인증·크기·배치)은 `views.py`가, 메시지 하나의 뜻은 여기가 맡는다. JSON-RPC 오류는 HTTP 200에
`error`로 싣고, 도구가 못 한 일은 `result.isError`로 싣는다(MCP 규약).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from apps.mcp import tools as _tools  # noqa: F401  도구 등록(import 부작용)
from apps.mcp.access import TenantArgs, caller_memberships, require_editor, resolve_membership
from apps.mcp.registry import JSONObject, Tool, ToolContext, ToolError, all_tools, get_tool
from apps.flows.schema import JSON
from apps.tenants.resource_token import AccessToken, InsufficientScope

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INFO = {"name": "sandcorp-flow", "version": "0.1.0"}
INSTRUCTIONS = (
    "Sandcorp Flow 상담 흐름 도구. 먼저 list_tenants로 내 테넌트와 역할을 보고, 흐름 JSON을 쓰기 전에 "
    "get_flow_schema를 한 번 읽는다. 테넌트 도구는 tenant(slug) 인자를 받는다(멤버십이 하나면 생략). "
    "편집·배포는 Admin만 된다."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

type RequestId = str | int | None


def error_response(request_id: RequestId, code: int, message: str) -> JSONObject:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: RequestId, result: JSONObject) -> JSONObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def is_notification(message: dict[str, JSON]) -> bool:
    return "id" not in message and isinstance(message.get("method"), str)


async def dispatch(message: dict[str, JSON], *, token: AccessToken, issuer: str) -> JSONObject:
    """요청 메시지 하나 → 응답 하나. 도구 스코프가 모자라면 `InsufficientScope`(뷰가 403으로)."""
    raw_id = message.get("id")
    request_id: RequestId = raw_id if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) else None
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or request_id is None:
        return error_response(request_id, INVALID_REQUEST, "invalid JSON-RPC 2.0 request")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return error_response(request_id, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if isinstance(asked, str) and asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        return _result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(SERVER_INFO),
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [_describe(t) for t in all_tools()]})
    if method == "tools/call":
        return await _call(request_id, params, token=token, issuer=issuer)
    return error_response(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _describe(tool: Tool) -> JSONObject:
    schema: JSONObject = tool.args_model.model_json_schema()
    schema.pop("title", None)
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": schema,
        "annotations": {"readOnlyHint": not tool.writes},
    }


async def _call(request_id: RequestId, params: dict[str, JSON], *, token: AccessToken, issuer: str) -> JSONObject:
    name = params.get("name")
    arguments = params.get("arguments", {})
    tool = get_tool(name) if isinstance(name, str) else None
    if tool is None:
        return error_response(request_id, INVALID_PARAMS, f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        return error_response(request_id, INVALID_PARAMS, "arguments must be an object")
    if tool.scope not in token.scopes:  # 스코프는 상한(#74 결정 4) — 403으로 올려 클라이언트가 더 받게
        raise InsufficientScope(tool.scope)

    try:
        args = tool.args_model.model_validate(arguments)
    except ValidationError as exc:
        return _result(request_id, _tool_error(ToolError("invalid_arguments", _brief(exc))))

    try:
        ctx = ToolContext(token=token, issuer=issuer)
        if isinstance(args, TenantArgs):
            memberships = await caller_memberships(token, issuer)
            membership = resolve_membership(memberships, args.tenant)
            if tool.writes:
                require_editor(membership)
            ctx = ToolContext(token=token, issuer=issuer, memberships=memberships, membership=membership)
        elif tool.writes:  # 쓰기 도구는 언제나 테넌트 도구다(등록 실수)
            raise RuntimeError(f"write tool {tool.name} must take TenantArgs")
        output = await tool.handler(ctx, args)
    except ToolError as exc:
        return _result(request_id, _tool_error(exc))
    except Exception:
        logger.exception("MCP tool %s failed", tool.name)
        return error_response(request_id, INTERNAL_ERROR, "internal error")
    text = json.dumps(output, ensure_ascii=False)
    return _result(request_id, {"content": [{"type": "text", "text": text}], "structuredContent": output, "isError": False})


def _tool_error(exc: ToolError) -> JSONObject:
    return {"content": [{"type": "text", "text": f"{exc.code}: {exc.message}"}], "isError": True}


def _brief(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())
