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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from apps.flows.schema import JSON
from apps.tenants.models import Membership
from apps.tenants.resource_token import AccessToken

type JSONObject = dict[str, JSON]
Scope = Literal["flow.read", "flow.write", "flow.deploy", "flow.admin"]
# 보호 리소스 메타데이터가 알리는 스코프. `flow.deploy`·`flow.admin`은 auth 등록 뒤 T3(#210)에서 연다.
MCP_SCOPES: list[str] = ["flow.read", "flow.write"]


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
) -> Callable[[Callable[[ToolContext, A], Awaitable[JSONObject]]], Callable[[ToolContext, A], Awaitable[JSONObject]]]:
    """도구를 등록한다. 이름이 겹치면 import 때 `ValueError`.

    `permission`은 일부러 기본값이 없다 — 새 도구를 쓰는 사람이 「누가 부를 수 있나」를 늘 적게 한다.
    `destructive`를 안 주면 쓰기 도구는 true(보수적), 읽기 도구는 false다. 읽기 도구는 파괴적일 수 없다.
    """
    is_destructive = writes if destructive is None else destructive
    if is_destructive and not writes:
        raise ValueError(f"read-only MCP tool {name} cannot be destructive")

    def decorate(
        fn: Callable[[ToolContext, A], Awaitable[JSONObject]],
    ) -> Callable[[ToolContext, A], Awaitable[JSONObject]]:
        if name in _TOOLS:
            raise ValueError(f"duplicate MCP tool: {name}")

        async def handler(ctx: ToolContext, args: BaseModel) -> JSONObject:
            assert isinstance(args, args_model)
            return await fn(ctx, args)

        _TOOLS[name] = Tool(name, description, args_model, scope, writes, permission, is_destructive, idempotent, handler)
        return fn

    return decorate


def unregister(name: str) -> None:
    """시험이 임시로 등록한 도구를 지운다."""
    _TOOLS.pop(name, None)


def get_tool(name: str) -> Tool | None:
    return _TOOLS.get(name)


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())
