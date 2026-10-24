"""MCP 도구 등록부(#75). 도구 = 이름·설명·입력 pydantic·필요 스코프·쓰기 여부·함수.

새 도구는 모듈 하나에서 `@register(...)`로 등록하고, 그 모듈을 `apps/mcp/tools/__init__.py`에서 import한다.

    class ListFlowsArgs(TenantArgs):        # 테넌트 도구는 TenantArgs(`tenant` slug 인자)를 상속
        pass

    @register("list_flows", "테넌트의 흐름 목록. …", ListFlowsArgs, scope="flow.read")
    async def list_flows(ctx: ToolContext, args: ListFlowsArgs) -> JSONObject:
        tenant = ctx.require_membership().tenant
        ...

- 입력 모델이 `TenantArgs`를 상속하면 디스패처가 `tenant`를 멤버십으로 풀어 `ctx.membership`에 넣는다(실패는 도구 오류).
- `writes=True`면 그 멤버십이 Admin일 때만 함수가 불린다(#74 결정 4). 스코프(`flow.write`)는 상한으로 따로 본다.
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
Scope = Literal["flow.read", "flow.write"]
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
    handler: ToolHandler


_TOOLS: dict[str, Tool] = {}


def register[A: BaseModel](
    name: str,
    description: str,
    args_model: type[A],
    *,
    scope: Scope,
    writes: bool = False,
) -> Callable[[Callable[[ToolContext, A], Awaitable[JSONObject]]], Callable[[ToolContext, A], Awaitable[JSONObject]]]:
    """도구를 등록한다. 이름이 겹치면 import 때 `ValueError`."""

    def decorate(
        fn: Callable[[ToolContext, A], Awaitable[JSONObject]],
    ) -> Callable[[ToolContext, A], Awaitable[JSONObject]]:
        if name in _TOOLS:
            raise ValueError(f"duplicate MCP tool: {name}")

        async def handler(ctx: ToolContext, args: BaseModel) -> JSONObject:
            assert isinstance(args, args_model)
            return await fn(ctx, args)

        _TOOLS[name] = Tool(name, description, args_model, scope, writes, handler)
        return fn

    return decorate


def unregister(name: str) -> None:
    """시험이 임시로 등록한 도구를 지운다."""
    _TOOLS.pop(name, None)


def get_tool(name: str) -> Tool | None:
    return _TOOLS.get(name)


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())
