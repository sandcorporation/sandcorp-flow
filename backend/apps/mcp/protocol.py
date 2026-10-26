"""MCP JSON-RPC 처리(#75) — `initialize`·`ping`·`tools/list`·`tools/call`.

전송(HTTP 상태·인증·크기·배치)은 `views.py`가, 메시지 하나의 뜻은 여기가 맡는다. JSON-RPC 오류는 HTTP 200에
`error`로 싣고, 도구가 못 한 일은 `result.isError`로 싣는다(MCP 규약).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from apps.mcp import tools as _tools  # noqa: F401  도구 등록(import 부작용)
from apps.mcp import audit, confirm
from apps.mcp.access import TenantArgs, caller_memberships, require_tool_permission, resolve_membership
from apps.mcp.models import McpAuditEvent
from apps.mcp.registry import (
    LEGACY_SCOPE_NOTICE,
    JSONObject,
    Tool,
    ToolContext,
    ToolError,
    all_tools,
    get_tool,
    used_legacy_scope,
)
from apps.flows.schema import JSON
from apps.tenants.models import Membership
from apps.tenants.resource_token import AccessToken, InsufficientScope

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INFO = {"name": "sandcorp-flow", "version": "0.1.0"}
INSTRUCTIONS = (
    "Sandcorp Flow 상담 흐름 도구. 먼저 list_tenants로 내 테넌트와 역할을 보고, 흐름 JSON을 쓰기 전에 "
    "get_flow_schema를 한 번 읽는다. 테넌트 도구는 tenant(slug 또는 id) 인자를 받는다(멤버십이 하나면 생략).\n"
    "권한: 연결할 때 받는 스코프 4개(flow.read 읽기 · flow.write 편집·시험·설정·문서 URL · flow.deploy 배포·승인 · "
    "flow.admin 팀·삭제·토글)는 상한이고, 실제로 되는지는 테넌트 역할이 정한다. 흐름 편집·배포·배포 승인·팀 관리·"
    "감사 로그 조회는 Admin만, 설정 수정(update_settings)과 문서 URL 등록·재수집·삭제·그래프 재빌드·방문자 기억 삭제는 "
    "Member도 된다. 스코프가 모자라면 커넥터를 다시 연결한다.\n"
    "2단계: 설명 끝에 (2단계)가 붙은 도구는 처음 부르면 실행하지 않고 미리보기와 confirm_token을 돌려준다. "
    "미리보기를 사용자에게 그대로 보이고 확인받은 뒤에만, 같은 인자에 confirm_token을 실어 다시 부른다"
    "(5분 안, 한 번만). 사용자가 확인하지 않았으면 부르지 않는다.\n"
    "비밀 값·API 키·Provider·웹훅·초대 링크는 다루지 않는다. 쓰기 호출은 모두 감사 로그에 남는다."
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
        "annotations": tool.annotations(),
    }


@dataclass
class _Outcome:
    """감사 로그에 남길 결과. 기본은 internal — 예상 못 한 길로 빠져도 「성공」으로 남지 않게."""

    code: str = McpAuditEvent.INTERNAL
    membership: Membership | None = None
    stage: str | None = None  # 2단계 도구의 단계(preview/confirm, #209)
    legacy_scope: bool = False  # #207: 옛 스코프(flow.write)로만 통과한 호출 — 호환 분기를 지울 때 같이 지운다


async def _call(request_id: RequestId, params: dict[str, JSON], *, token: AccessToken, issuer: str) -> JSONObject:
    name = params.get("name")
    arguments = params.get("arguments", {})
    tool = get_tool(name) if isinstance(name, str) else None
    if tool is None:
        return error_response(request_id, INVALID_PARAMS, f"unknown tool: {name}")
    if not tool.writes:
        return await _run(request_id, tool, arguments, token=token, issuer=issuer, outcome=_Outcome())

    outcome = _Outcome()
    recorded = arguments
    if tool.confirm:  # 단계는 토큰을 실었는지로 정한다(검사에서 막혀도). 토큰 값은 기록하지 않는다.
        given = isinstance(arguments, dict) and arguments.get(confirm.CONFIRM_FIELD) is not None
        outcome.stage = McpAuditEvent.CONFIRM if given else McpAuditEvent.PREVIEW
        if given and isinstance(arguments, dict):
            recorded = {**arguments, confirm.CONFIRM_FIELD: "[redacted]"}

    # 쓰기 도구: 부르기 전에 기록한다. 기록이 안 되면 실행하지 않는다(#208 — 기록 없는 쓰기를 만들지 않는다).
    try:
        event_id = await audit.begin(tool.name, token, issuer, recorded)
    except Exception:
        logger.exception("MCP audit write failed; %s was not run", tool.name)
        return error_response(request_id, INTERNAL_ERROR, "internal error")
    try:
        return await _run(request_id, tool, arguments, token=token, issuer=issuer, outcome=outcome)
    finally:
        try:
            await audit.finish(event_id, outcome.code, outcome.membership, outcome.stage, outcome.legacy_scope)
        except Exception:  # 결과는 이미 정해졌다 — 행은 started로 남는다
            logger.exception("MCP audit result write failed for %s (event %s)", tool.name, event_id)


async def _run(
    request_id: RequestId, tool: Tool, arguments: JSON, *, token: AccessToken, issuer: str, outcome: _Outcome
) -> JSONObject:
    if not isinstance(arguments, dict):
        outcome.code = "invalid_arguments"
        return error_response(request_id, INVALID_PARAMS, "arguments must be an object")
    outcome.legacy_scope = used_legacy_scope(tool, token.scopes)  # #207
    if tool.scope not in token.scopes and not outcome.legacy_scope:  # 스코프는 상한(#74 결정 4) — 403으로 올려 클라이언트가 더 받게
        outcome.code = "insufficient_scope"
        if tool.writes:
            outcome.membership = await _membership_or_none(token, issuer, arguments.get("tenant"))
        raise InsufficientScope(tool.scope)

    try:
        args = tool.args_model.model_validate(arguments)
    except ValidationError as exc:
        outcome.code = "invalid_arguments"
        return _result(request_id, _tool_error(ToolError("invalid_arguments", _brief(exc))))

    try:
        ctx = ToolContext(token=token, issuer=issuer)
        if isinstance(args, TenantArgs):
            memberships = await caller_memberships(token, issuer)
            membership = resolve_membership(memberships, args.tenant)
            outcome.membership = membership
            require_tool_permission(membership, tool.permission)  # 판정은 has_permission 한 곳(#208)
            ctx = ToolContext(token=token, issuer=issuer, memberships=memberships, membership=membership)
        elif tool.writes or tool.permission is not None:  # 쓰기·권한 도구는 언제나 테넌트 도구다(등록 실수)
            raise RuntimeError(f"tool {tool.name} must take TenantArgs")
        output = await (_two_step(tool, ctx, args) if tool.confirm else tool.handler(ctx, args))
    except ToolError as exc:
        outcome.code = exc.code
        return _result(request_id, _tool_error(exc))
    except Exception:
        logger.exception("MCP tool %s failed", tool.name)
        return error_response(request_id, INTERNAL_ERROR, "internal error")
    outcome.code = McpAuditEvent.OK
    if outcome.legacy_scope:
        output = {**output, "notice": LEGACY_SCOPE_NOTICE}  # #207
    text = json.dumps(output, ensure_ascii=False)
    return _result(request_id, {"content": [{"type": "text", "text": text}], "structuredContent": output, "isError": False})


async def _two_step(tool: Tool, ctx: ToolContext, args: BaseModel) -> JSONObject:
    """2단계 도구(#209): 토큰이 없으면 미리보기 + 토큰, 있으면 검사 → 소비 → 실행. 권한·스코프 검사는 이미 지났다."""
    assert tool.preview is not None
    membership = ctx.require_membership()
    binding = confirm.binding_for(tool.name, str(membership.tenant_id), ctx.token.sub, args)
    given = getattr(args, confirm.CONFIRM_FIELD, None)
    if not isinstance(given, str):
        preview = await tool.preview(ctx, args)
        issued = confirm.issue(binding)
        return {"confirm_required": True, "preview": preview, "confirm_token": issued.token, "expires_in": issued.expires_in}
    await confirm.spend(confirm.verify(given, binding))
    return await tool.handler(ctx, args)


async def _membership_or_none(token: AccessToken, issuer: str, tenant: JSON) -> Membership | None:
    """감사 기록용 — 스코프에서 막힌 호출도 어느 테넌트였는지 남긴다. 못 풀면 None(사유는 묻지 않는다)."""
    try:
        return resolve_membership(await caller_memberships(token, issuer), tenant if isinstance(tenant, str) else None)
    except ToolError:
        return None


def _tool_error(exc: ToolError) -> JSONObject:
    return {"content": [{"type": "text", "text": f"{exc.code}: {exc.message}"}], "isError": True}


def _brief(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())
