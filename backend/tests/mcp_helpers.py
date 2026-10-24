"""MCP 시험 도우미(#75에서 옮김) — 토큰·호출·테넌트/멤버십·시험용 쓰기 도구.

픽스처(`clock`·`signer`·autouse `flow_validator`·`write_tool`)는 쓰는 시험 모듈에서 import해 둔다.
외부 경계(JWKS·시계)만 #20 대역(`resource_token_fakes`)으로 바꾸고, 검증기·DB·뷰는 실제 객체다.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator

import pytest
from django.test import Client

from apps.mcp import registry
from apps.mcp.access import TenantArgs
from apps.mcp.registry import JSONObject, ToolContext
from apps.tenants import resource_token
from apps.tenants.models import Membership, Tenant, User
from resource_token_fakes import (
    ISSUER,
    MEMBER_SUB,
    OWNER,
    FakeJwksEndpoint,
    FrozenClock,
    Signer,
    make_validator,
    new_signer,
    public_jwk,
)

MCP = "https://flow.sandcorp.co.kr/mcp"
API = "https://flow.sandcorp.co.kr/api"
MCP_METADATA = 'Bearer resource_metadata="https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp"'


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def signer(clock: FrozenClock) -> Signer:
    return new_signer(clock)


@pytest.fixture(autouse=True)
def flow_validator(signer: Signer, clock: FrozenClock) -> Iterator[None]:
    endpoint = FakeJwksEndpoint([public_jwk(signer.private_key, signer.kid)])
    resource_token.use_validator(make_validator(endpoint, clock))
    yield
    resource_token.use_validator(None)


def mcp_token(signer: Signer, *, sub: str = MEMBER_SUB, scope: str = "flow.read flow.write", **extra: object) -> str:
    return signer.member_token(sub=sub, aud=MCP, scope=scope, **extra)


def owner_token(signer: Signer, scope: str = "flow.read flow.write") -> str:
    return signer.token(aud=MCP, scope=scope)


def post(body: object, token: str | None = None, *, raw: bytes | None = None, headers: dict[str, str] | None = None):
    client = Client()
    if token is not None:
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    data = raw if raw is not None else json.dumps(body)
    return client.post("/mcp", data=data, content_type="application/json", headers=headers)


def rpc(method: str, params: dict[str, object] | None = None, *, id: int = 1) -> dict[str, object]:
    message: dict[str, object] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(token: str, tool: str, arguments: dict[str, object] | None = None) -> dict:
    response = post(rpc("tools/call", {"name": tool, "arguments": arguments or {}}), token)
    assert response.status_code == 200, response.content
    return response.json()


def tool_text(body: dict) -> str:
    return body["result"]["content"][0]["text"]


def make_tenant(slug: str | None, *, active: bool = True) -> Tenant:
    tenant = Tenant.objects.create_with_key(name=f"{slug or 'noslug'} corp", raw_key=secrets.token_urlsafe(16))
    tenant.slug = slug
    tenant.is_active = active
    tenant.save(update_fields=["slug", "is_active"])
    return tenant


def member_of(tenant: Tenant, role: str = Membership.ROLE_MEMBER, *, sub: str = MEMBER_SUB, active: bool = True) -> Membership:
    user, _ = User.objects.get_or_create(
        iss=ISSUER, sub=sub, defaults={"acct": User.Acct.OWNER if sub == OWNER else User.Acct.MEMBER}
    )
    return Membership.objects.create(user=user, tenant=tenant, role=role, is_active=active)


# ── 시험용 쓰기 도구(쓰기 도구는 #76에서 생긴다) ─────────────────────────────────


class _EchoArgs(TenantArgs):
    text: str = ""


@pytest.fixture
def write_tool() -> Iterator[str]:
    async def echo(ctx: ToolContext, args: _EchoArgs) -> JSONObject:
        assert ctx.membership is not None
        return {"tenant": ctx.membership.tenant.slug, "text": args.text}

    name = "test_echo_write"
    registry.register(name, "시험용 쓰기 도구.", _EchoArgs, scope="flow.write", writes=True)(echo)
    yield name
    registry.unregister(name)
