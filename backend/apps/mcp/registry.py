"""MCP 도구 등록부(#75). 도구 = 이름·설명·입력 pydantic·필요 스코프·쓰기 여부·함수.

새 도구는 모듈 하나에서 `@register(...)`로 등록하고, 그 모듈을 `apps/mcp/tools/__init__.py`에서 import한다.

    class ListFlowsArgs(TenantArgs):        # 테넌트 도구는 TenantArgs(`tenant` slug 인자)를 상속
        pass

    @register("list_flows", "테넌트의 흐름 목록. …", ListFlowsArgs, scope="flow.read")
    async def list_flows(ctx: ToolContext, args: ListFlowsArgs) -> JSONObject:
        tenant = ctx.require_membership().tenant
        ...

- 입력 모델이 `TenantArgs`를 상속하면 디스패처가 `tenant`를 멤버십으로 풀어 `ctx.membership`에 넣는다(실패는 도구 오류).
- `permission`은 그 멤버십에 있어야 하는 권한 비트(`apps.tenants.permissions`)다. 디스패처가 `has_permission` 한 곳에서
  판정한다(HTTP API와 같은 규칙). `None`이면 비트 없이 멤버면 된다(#208). 스코프(`flow.write` 등)는 상한으로 따로 본다.
- `writes=True`인 도구의 `tools/call`은 결과와 상관없이 MCP 감사 로그(`apps.mcp.audit`)에 한 줄씩 남는다.
- 힌트(`annotations`): `readOnlyHint = not writes`, `destructiveHint`(기본: 쓰기면 true), `idempotentHint`(기본 false).
- 실패 사유는 `ToolError(code, message)`로 던진다 — 클라이언트에 `isError: true`로 간다.

2단계 도구(#209) — 미리보기 함수를 `preview=`로 같이 등록한다(같은 입력 모델을 받고 「무엇이 바뀌는지」를 돌려준다):

    @register("delete_document", "문서를 지운다.", DeleteDocumentArgs, scope="flow.admin", permission=None,
              writes=True, preview=preview_delete_document)
    async def delete_document(ctx: ToolContext, args: DeleteDocumentArgs) -> JSONObject: ...

- `confirm_token` 없이 부르면 디스패처가 권한·스코프·인자 검사 뒤 `preview`만 부르고
  `{confirm_required, preview, confirm_token, expires_in}`을 돌려준다. 토큰을 실어 같은 인자로 부르면 본 함수를 부른다
  (토큰 규칙은 `apps.mcp.confirm`). 본 함수·미리보기 함수는 토큰을 볼 일이 없다.
- 레지스트리가 강제하는 것: 쓰기 도구여야 하고(`writes=True`), 테넌트 도구여야 하며(입력에 `tenant`),
  `destructiveHint=true`(`destructive=False`면 `ValueError`), 입력에 `confirm_token`(선택) 칸을 더하고,
  설명 끝에 `DESCRIPTION_SUFFIX`를 붙인다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, create_model

from apps.flows.schema import JSON
from apps.tenants.models import Membership
from apps.tenants.resource_token import AccessToken

type JSONObject = dict[str, JSON]
Scope = Literal["flow.read", "flow.write", "flow.deploy", "flow.admin"]
# 보호 리소스 메타데이터가 알리는 스코프. `flow.deploy`·`flow.admin`은 auth 등록 뒤 T3(#210)에서 연다.
MCP_SCOPES: list[str] = ["flow.read", "flow.write"]

CONFIRM_FIELD = "confirm_token"
DESCRIPTION_SUFFIX = "(2단계) 먼저 미리보기를 사용자에게 보이고 확인을 받은 뒤 confirm_token으로 다시 부른다."


class ToolError(Exception):
    """도구가 할 수 없는 일(사유를 에이전트가 읽고 고칠 수 있게). JSON-RPC 오류가 아니라 `isError` 결과다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolContext:
    """도구 함수가 받는 호출자 정보. `membership`은 테넌트 도구일 때만 채워진다."""

    token: AccessToken
    issuer: str
    memberships: list[Membership] = field(default_factory=list)
    membership: Membership | None = None

    def require_membership(self) -> Membership:
        if self.membership is None:  # 등록 실수(TenantArgs 없이 테넌트를 쓰는 도구)
            raise RuntimeError("tool has no tenant; its args must inherit TenantArgs")
        return self.membership


type ToolHandler = Callable[[ToolContext, BaseModel], Awaitable[JSONObject]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    scope: Scope
    writes: bool
    permission: str | None
    destructive: bool
    idempotent: bool
    handler: ToolHandler
    preview: ToolHandler | None = None  # 있으면 2단계 도구(#209)

    @property
    def confirm(self) -> bool:
        return self.preview is not None

    def annotations(self) -> JSONObject:
        return {"readOnlyHint": not self.writes, "destructiveHint": self.destructive, "idempotentHint": self.idempotent}


_TOOLS: dict[str, Tool] = {}


def register[A: BaseModel](
    name: str,
    description: str,
    args_model: type[A],
    *,
    scope: Scope,
    permission: str | None,
    writes: bool = False,
    destructive: bool | None = None,
    idempotent: bool = False,
    preview: Callable[[ToolContext, A], Awaitable[JSONObject]] | None = None,
) -> Callable[[Callable[[ToolContext, A], Awaitable[JSONObject]]], Callable[[ToolContext, A], Awaitable[JSONObject]]]:
    """도구를 등록한다. 이름이 겹치면 import 때 `ValueError`.

    `permission`은 일부러 기본값이 없다 — 새 도구를 쓰는 사람이 「누가 부를 수 있나」를 늘 적게 한다.
    `destructive`를 안 주면 쓰기 도구는 true(보수적), 읽기 도구는 false다. 읽기 도구는 파괴적일 수 없다.
    """
    is_destructive = writes if destructive is None else destructive
    if is_destructive and not writes:
        raise ValueError(f"read-only MCP tool {name} cannot be destructive")
    model: type[BaseModel] = args_model
    if preview is not None:
        if not writes:
            raise ValueError(f"two-step MCP tool {name} must be a write tool")
        if destructive is False:
            raise ValueError(f"two-step MCP tool {name} is always destructive")
        if "tenant" not in args_model.model_fields:  # 토큰이 테넌트에 묶인다
            raise ValueError(f"two-step MCP tool {name} must take TenantArgs")
        if CONFIRM_FIELD in args_model.model_fields:
            raise ValueError(f"two-step MCP tool {name} must not declare {CONFIRM_FIELD} itself")
        is_destructive = True
        description = f"{description.rstrip()} {DESCRIPTION_SUFFIX}"
        model = create_model(  # 본 모델의 하위 클래스 — 본 함수·미리보기 함수는 원래 모델 인스턴스로 받는다
            args_model.__name__,
            __base__=args_model,
            confirm_token=(
                str | None,
                Field(default=None, description="미리보기 응답의 confirm_token. 없으면 실행하지 않고 미리보기를 돌려준다."),
            ),
        )

    def decorate(
        fn: Callable[[ToolContext, A], Awaitable[JSONObject]],
    ) -> Callable[[ToolContext, A], Awaitable[JSONObject]]:
        if name in _TOOLS:
            raise ValueError(f"duplicate MCP tool: {name}")

        async def handler(ctx: ToolContext, args: BaseModel) -> JSONObject:
            assert isinstance(args, args_model)
            return await fn(ctx, args)

        preview_handler: ToolHandler | None = None
        if preview is not None:
            see = preview

            async def run_preview(ctx: ToolContext, args: BaseModel) -> JSONObject:
                assert isinstance(args, args_model)
                return await see(ctx, args)

            preview_handler = run_preview

        _TOOLS[name] = Tool(
            name, description, model, scope, writes, permission, is_destructive, idempotent, handler, preview_handler
        )
        return fn

    return decorate


def unregister(name: str) -> None:
    """시험이 임시로 등록한 도구를 지운다."""
    _TOOLS.pop(name, None)


def get_tool(name: str) -> Tool | None:
    return _TOOLS.get(name)


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())
