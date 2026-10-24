"""테넌트 비밀·허용 도메인 API(#33, ADR-0029). 로직은 ``apps.tenants.tenant_secrets``에 있다.

- ``/api/tenant/secrets/`` — 쓰기 전용. GET은 이름·시각만, PUT ``/{name}``은 만들기·값 교체, DELETE는 지우기.
  입력 검사는 스키마 제약이 아니라 서비스에서 한다(Ninja 422 본문이 입력값을 되돌려 보내지 않게).
- ``/api/tenant/allowed-domains/`` — 목록·더하기(정규화해 저장)·지우기.
- 쓰기는 ``secrets.manage``/``domains.manage``(Admin 전용), 읽기는 멤버 누구나. 다른 테넌트 행은 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.http import HttpRequest, HttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.tenants import tenant_secrets as ts
from apps.tenants.auth import member_or_tenant_key_auth
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import DOMAINS_MANAGE, SECRETS_MANAGE, require_permission

secret_router = Router(tags=["tenant-secrets"], auth=member_or_tenant_key_auth)
domain_router = Router(tags=["tenant-allowed-domains"], auth=member_or_tenant_key_auth)


class SecretOut(Schema):
    name: str
    created_at: datetime
    updated_at: datetime


class SecretIn(Schema):
    value: str


class DomainOut(Schema):
    id: uuid.UUID
    pattern: str
    created_at: datetime


class DomainIn(Schema):
    pattern: str


def _tenant_id(request: HttpRequest) -> uuid.UUID:
    auth: Membership | Tenant = request.auth  # type: ignore[attr-defined]  # Ninja가 인증 뒤 붙인다
    return auth.tenant_id if isinstance(auth, Membership) else auth.id


@secret_router.get("/", response=list[SecretOut])
def list_secrets(request: HttpRequest):
    return ts.list_secrets(_tenant_id(request))


@secret_router.put("/{name}", response=SecretOut)
def put_secret(request: HttpRequest, name: str, body: SecretIn):
    require_permission(request.auth, SECRETS_MANAGE)  # type: ignore[attr-defined]
    try:
        return ts.set_secret(_tenant_id(request), name, body.value)
    except ts.SecretError as exc:
        raise HttpError(422, str(exc)) from None


@secret_router.delete("/{name}", response={204: None})
def delete_secret(request: HttpRequest, name: str):
    require_permission(request.auth, SECRETS_MANAGE)  # type: ignore[attr-defined]
    try:
        ts.delete_secret(_tenant_id(request), name)
    except ts.SecretNotFound:
        raise HttpError(404, "비밀을 찾을 수 없습니다.") from None
    return HttpResponse(status=204)


@domain_router.get("/", response=list[DomainOut])
def list_domains(request: HttpRequest):
    return ts.list_domains(_tenant_id(request))


@domain_router.post("/", response={201: DomainOut})
def add_domain(request: HttpRequest, body: DomainIn):
    require_permission(request.auth, DOMAINS_MANAGE)  # type: ignore[attr-defined]
    try:
        return 201, ts.add_domain(_tenant_id(request), body.pattern)
    except ts.DomainExists as exc:
        raise HttpError(409, str(exc)) from None
    except ts.DomainError as exc:
        raise HttpError(422, str(exc)) from None


@domain_router.delete("/{domain_id}", response={204: None})
def delete_domain(request: HttpRequest, domain_id: uuid.UUID):
    require_permission(request.auth, DOMAINS_MANAGE)  # type: ignore[attr-defined]
    if not ts.delete_domain(_tenant_id(request), domain_id):
        raise HttpError(404, "허용 도메인을 찾을 수 없습니다.")
    return HttpResponse(status=204)
