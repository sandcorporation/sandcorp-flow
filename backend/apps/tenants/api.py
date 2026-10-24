import secrets
from typing import List
from ninja import Router, Schema
from urllib.parse import urlencode
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from apps.tenants.auth import (
    create_operator_access_token, create_tenant_agent_token,
    operator_auth, tenant_key_auth, tenant_agent_auth,
)
from apps.tenants.auth_cookies import (
    set_refresh_cookie, clear_refresh_cookie, cookie_name, OPERATOR, TENANT_AGENT,
)
from apps.tenants.refresh_tokens import (
    issue_session, rotate, revoke_all, revoke_session, RefreshRejected,
)
from apps.tenants.models import Operator, Tenant, TenantConfig, TenantAgent
from apps.tenants.operator_login import (
    ATTEMPT_MAX_AGE_SECONDS, LoginAttempt, LoginRejected, complete_login, start_login,
)

operator_router = Router(tags=["operator"])
tenant_router = Router(tags=["tenant"], auth=tenant_agent_auth)
agent_router = Router(tags=["tenant-agents"])


class LoginOut(Schema):
    access_token: str


class TenantIn(Schema):
    name: str


class TenantOut(Schema):
    id: str
    name: str
    is_active: bool
    created_at: str


class TenantCreatedOut(Schema):
    id: str
    name: str
    is_active: bool
    tenant_key: str
    agent_username: str
    agent_temp_password: str


class TenantConfigOut(Schema):
    model_id: str
    system_prompt: str
    agent_display_name: str
    webhook_url: str
    webhook_type: str
    welcome_message: str
    brand_name: str
    hitl_enabled: bool
    hitl_timezone: str
    hitl_schedule: dict
    hitl_holidays: list
    require_identity_verification: bool
    topic_scope_enabled: bool
    scope_description: str
    scope_refusal_message: str
    llm_provider_type: str
    llm_base_url: str
    llm_api_key: str
    extraction_model: str
    embed_provider_type: str
    embed_base_url: str
    embed_api_key: str
    embed_model: str
    embed_dim: int
    ocr_provider_type: str
    ocr_base_url: str
    ocr_api_key: str
    ocr_model: str
    # 공개 챗봇 URL slug(Tenant 모델 소유, config 아님). 미설정이면 빈 문자열. 새로고침 시
    # 어드민 입력 복원·임베드 URL 생성에 쓴다.
    slug: str = ""
    # 서버 capability(저장 필드 아님): dev에서만 플랫폼 기본(OpenRouter/ollama) Provider 폴백이
    # 켜진다. 어드민 UI는 이 값이 false면 "기본" Provider 옵션을 숨긴다(ADR-0012).
    platform_default_providers_enabled: bool


class TenantConfigIn(Schema):
    model_id: str | None = None
    system_prompt: str | None = None
    agent_display_name: str | None = None
    webhook_url: str | None = None
    webhook_type: str | None = None
    welcome_message: str | None = None
    brand_name: str | None = None
    hitl_enabled: bool | None = None
    hitl_timezone: str | None = None
    hitl_schedule: dict | None = None
    hitl_holidays: list | None = None
    require_identity_verification: bool | None = None
    topic_scope_enabled: bool | None = None
    scope_description: str | None = None
    scope_refusal_message: str | None = None
    llm_provider_type: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    extraction_model: str | None = None
    embed_provider_type: str | None = None
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    embed_model: str | None = None
    embed_dim: int | None = None
    ocr_provider_type: str | None = None
    ocr_base_url: str | None = None
    ocr_api_key: str | None = None
    ocr_model: str | None = None


class SlugIn(Schema):
    slug: str


class ResetKeyOut(Schema):
    new_tenant_key: str


class AgentLoginIn(Schema):
    tenant_name: str
    username: str
    password: str


class AgentLoginOut(Schema):
    access_token: str


class AgentOut(Schema):
    id: str
    username: str
    is_active: bool
    role: str


class AgentCreateIn(Schema):
    username: str
    role: str | None = None  # 미지정 시 Member(ADR-0025)


class AgentRoleIn(Schema):
    role: str


class AgentCreatedOut(Schema):
    id: str
    username: str
    is_active: bool
    role: str
    temp_password: str


class ChangePasswordIn(Schema):
    current_password: str
    new_password: str


def _make_agent_auth():
    """Returns auth that accepts EITHER TenantAgentAuth OR TenantKeyAuth."""
    from ninja.security import HttpBearer
    from apps.tenants.auth import verify_tenant_agent_token

    class DualAuth(HttpBearer):
        def authenticate(self, request, token: str):
            payload = verify_tenant_agent_token(token)
            if payload:
                try:
                    return TenantAgent.objects.select_related("tenant").get(
                        id=payload["sub"], is_active=True
                    )
                except TenantAgent.DoesNotExist:
                    pass
            tenant = Tenant.verify_key(token)
            if tenant:
                return tenant
            return None

    return DualAuth()


_dual_auth = _make_agent_auth()


@agent_router.post("/auth/login", response={200: AgentLoginOut, 401: dict}, auth=None)
def agent_login(request, body: AgentLoginIn, response: HttpResponse):
    try:
        # 조직 이름은 전역 unique·대소문자 무시 로그인 식별자(ADR-0025)
        agent = TenantAgent.objects.select_related("tenant").get(
            tenant__name__iexact=body.tenant_name.strip(), username=body.username, is_active=True
        )
    except TenantAgent.DoesNotExist:
        return 401, {"detail": "Invalid credentials"}
    except TenantAgent.MultipleObjectsReturned:
        return 401, {"detail": "Invalid credentials"}
    if not agent.check_password(body.password):
        return 401, {"detail": "Invalid credentials"}
    set_refresh_cookie(response, agent, issue_session(agent))
    return 200, {"access_token": create_tenant_agent_token(agent)}


@agent_router.post("/auth/signup", response={201: AgentLoginOut, 400: dict, 409: dict, 429: dict}, auth=None)
def agent_signup(request, body: AgentLoginIn, response: HttpResponse):
    """공개 가입(ADR-0025) — 조직 이름·username·password로 Tenant + 첫 Tenant Admin 생성 후 즉시 로그인.

    남용 방지: IP당 1시간 1회(성공 가입만 슬롯 소비 — 실패는 안 막음).
    """
    from apps.tenants.registration import register_tenant, DuplicateOrgName, InvalidSignup
    from apps.tenants.signup_rate_limit import client_ip, signup_allowed, mark_signup
    ip = client_ip(request)
    if not signup_allowed(ip):
        return 429, {"detail": "가입은 IP당 1시간에 1회만 가능합니다. 잠시 후 다시 시도해 주세요."}
    try:
        _tenant, agent = register_tenant(body.tenant_name, body.username, body.password)
    except DuplicateOrgName:
        return 409, {"detail": "이미 사용 중인 조직 이름입니다."}
    except InvalidSignup as e:
        return 400, {"detail": str(e)}
    mark_signup(ip)  # 성공 시에만 기록
    set_refresh_cookie(response, agent, issue_session(agent))
    return 201, {"access_token": create_tenant_agent_token(agent)}


@agent_router.post("/auth/refresh", response={200: AgentLoginOut, 401: dict}, auth=None)
def agent_refresh(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(TENANT_AGENT))
    if not raw:
        return 401, {"detail": "No refresh token"}
    try:
        subject, new_raw = rotate(raw)
    except RefreshRejected:
        return 401, {"detail": "Invalid refresh token"}
    if not isinstance(subject, TenantAgent) or not subject.is_active:
        return 401, {"detail": "Invalid refresh token"}
    set_refresh_cookie(response, subject, new_raw)
    return 200, {"access_token": create_tenant_agent_token(subject)}


@agent_router.post("/auth/logout", response={200: dict}, auth=None)
def agent_logout(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(TENANT_AGENT))
    if raw:
        revoke_session(raw)  # 이 기기 Family만 폐기
    clear_refresh_cookie(response, TENANT_AGENT)
    return 200, {"detail": "logged out"}


@agent_router.post("/auth/logout-all", response={200: dict}, auth=tenant_agent_auth)
def agent_logout_all(request, response: HttpResponse):
    revoke_all(request.auth)  # 주체의 전 기기 Family 폐기
    clear_refresh_cookie(response, TENANT_AGENT)
    return 200, {"detail": "logged out everywhere"}


def _get_tenant_from_auth(auth_obj):
    if isinstance(auth_obj, TenantAgent):
        return auth_obj.tenant
    return auth_obj


def _is_last_active_admin(agent) -> bool:
    """이 agent가 조직의 마지막 활성 Admin인지 — 비활성화·강등 차단용(lockout 방지, issue 210)."""
    if agent.role != TenantAgent.ROLE_ADMIN or not agent.is_active:
        return False
    return not TenantAgent.objects.filter(
        tenant_id=agent.tenant_id, role=TenantAgent.ROLE_ADMIN, is_active=True
    ).exclude(id=agent.id).exists()


@agent_router.get("/", response=list[AgentOut], auth=_dual_auth)
def list_agents(request):
    tenant = _get_tenant_from_auth(request.auth)
    return [
        {"id": str(a.id), "username": a.username, "is_active": a.is_active, "role": a.role}
        for a in TenantAgent.objects.filter(tenant=tenant)
    ]


@agent_router.post("/", response={201: AgentCreatedOut}, auth=_dual_auth)
def create_agent(request, body: AgentCreateIn):
    from apps.tenants.permissions import require_permission, AGENTS_MANAGE
    require_permission(request.auth, AGENTS_MANAGE)
    tenant = _get_tenant_from_auth(request.auth)
    role = body.role if body.role in (TenantAgent.ROLE_ADMIN, TenantAgent.ROLE_MEMBER) else TenantAgent.ROLE_MEMBER
    temp_password = secrets.token_urlsafe(16)
    agent = TenantAgent(tenant=tenant, username=body.username, role=role)
    agent.set_password(temp_password)
    agent.save()
    return 201, {
        "id": str(agent.id),
        "username": agent.username,
        "is_active": agent.is_active,
        "role": agent.role,
        "temp_password": temp_password,
    }


@agent_router.patch("/{agent_id}/deactivate", response={200: AgentOut, 409: dict}, auth=_dual_auth)
def deactivate_agent(request, agent_id: str):
    from django.shortcuts import get_object_or_404
    from apps.tenants.permissions import require_permission, AGENTS_MANAGE
    require_permission(request.auth, AGENTS_MANAGE)
    tenant = _get_tenant_from_auth(request.auth)
    agent = get_object_or_404(TenantAgent, id=agent_id, tenant=tenant)
    if _is_last_active_admin(agent):
        return 409, {"detail": "마지막 Admin은 비활성화할 수 없습니다."}
    agent.is_active = False
    agent.save()
    return 200, {"id": str(agent.id), "username": agent.username, "is_active": agent.is_active, "role": agent.role}


@agent_router.patch("/{agent_id}/role", response={200: AgentOut, 400: dict, 409: dict}, auth=_dual_auth)
def change_agent_role(request, agent_id: str, body: AgentRoleIn):
    from django.shortcuts import get_object_or_404
    from apps.tenants.permissions import require_permission, AGENTS_MANAGE
    require_permission(request.auth, AGENTS_MANAGE)
    tenant = _get_tenant_from_auth(request.auth)
    agent = get_object_or_404(TenantAgent, id=agent_id, tenant=tenant)
    if body.role not in (TenantAgent.ROLE_ADMIN, TenantAgent.ROLE_MEMBER):
        return 400, {"detail": "유효하지 않은 역할입니다."}
    if body.role == TenantAgent.ROLE_MEMBER and _is_last_active_admin(agent):
        return 409, {"detail": "마지막 Admin은 강등할 수 없습니다."}
    agent.role = body.role
    agent.save(update_fields=["role"])
    return 200, {"id": str(agent.id), "username": agent.username, "is_active": agent.is_active, "role": agent.role}


@agent_router.post("/me/change-password", response={200: dict, 400: dict}, auth=tenant_agent_auth)
def change_password(request, body: ChangePasswordIn):
    from apps.tenants.password_policy import password_policy_error
    agent = request.auth
    if not agent.check_password(body.current_password):
        return 400, {"detail": "현재 비밀번호가 올바르지 않습니다."}
    pw_err = password_policy_error(body.new_password)
    if pw_err:
        return 400, {"detail": pw_err}
    agent.set_password(body.new_password)
    agent.save()
    revoke_all(agent)  # 비번 변경 시 기존 모든 세션의 refresh 폐기(침해 대응)
    return 200, {"detail": "비밀번호가 변경되었습니다."}


# ── Operator 로그인: sandcorp-auth 기밀 클라이언트(ADR-0028, #21) ────────────────
# 브라우저 이동(302)이라 생성 클라이언트에 넣지 않는다. 시도(state·PKCE·nonce)는 서명된 httpOnly
# 쿠키에 맡긴다 — 인가 서버에서 돌아오는 교차 사이트 GET에도 실려야 하므로 SameSite=Lax.
LOGIN_ATTEMPT_COOKIE = "op_login"
LOGIN_ATTEMPT_COOKIE_PATH = "/api/operator/auth/callback"


def _login_redirect(reason: str | None = None) -> HttpResponseRedirect:
    target = settings.AUTH_LOGIN_REDIRECT
    if reason:
        target = f"{target}?{urlencode({'login_error': reason})}"
    response = HttpResponseRedirect(target)
    response.delete_cookie(LOGIN_ATTEMPT_COOKIE, path=LOGIN_ATTEMPT_COOKIE_PATH)
    return response


@operator_router.get("/auth/authorize", auth=None, include_in_schema=False)
def operator_authorize(request):
    try:
        url, attempt = start_login()
    except LoginRejected as exc:
        return _login_redirect(exc.reason)
    response = HttpResponseRedirect(url)
    response.set_cookie(
        LOGIN_ATTEMPT_COOKIE,
        attempt.dumps(),
        max_age=ATTEMPT_MAX_AGE_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=LOGIN_ATTEMPT_COOKIE_PATH,
    )
    return response


@operator_router.get("/auth/callback", auth=None, include_in_schema=False)
def operator_callback(request):
    attempt = LoginAttempt.loads(request.COOKIES.get(LOGIN_ATTEMPT_COOKIE))
    try:
        operator = complete_login(request.GET.dict(), attempt)
    except LoginRejected as exc:
        return _login_redirect(exc.reason)
    response = _login_redirect()
    set_refresh_cookie(response, operator, issue_session(operator))
    return response


@operator_router.post("/auth/refresh", response={200: LoginOut, 401: dict}, auth=None)
def operator_refresh(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(OPERATOR))
    if not raw:
        return 401, {"detail": "No refresh token"}
    try:
        subject, new_raw = rotate(raw)
    except RefreshRejected:
        return 401, {"detail": "Invalid refresh token"}
    if not isinstance(subject, Operator) or subject.user_id is None:
        return 401, {"detail": "Invalid refresh token"}
    set_refresh_cookie(response, subject, new_raw)
    return 200, {"access_token": create_operator_access_token(subject)}


@operator_router.post("/auth/logout", response={200: dict}, auth=None)
def operator_logout(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(OPERATOR))
    if raw:
        revoke_session(raw)  # 이 기기 Family만 폐기
    clear_refresh_cookie(response, OPERATOR)
    return 200, {"detail": "logged out"}


@operator_router.post("/auth/logout-all", response={200: dict}, auth=operator_auth)
def operator_logout_all(request, response: HttpResponse):
    revoke_all(request.auth)  # 주체의 전 기기 Family 폐기
    clear_refresh_cookie(response, OPERATOR)
    return 200, {"detail": "logged out everywhere"}


@operator_router.post("/tenants/", response={201: TenantCreatedOut}, auth=operator_auth)
def create_tenant(request, body: TenantIn):
    raw_key = secrets.token_urlsafe(32)
    tenant = Tenant.objects.create_with_key(name=body.name, raw_key=raw_key)
    temp_password = secrets.token_urlsafe(16)
    agent = TenantAgent(tenant=tenant, username="admin", role=TenantAgent.ROLE_ADMIN)
    agent.set_password(temp_password)
    agent.save()
    return 201, {
        "id": str(tenant.id),
        "name": tenant.name,
        "is_active": tenant.is_active,
        "tenant_key": raw_key,
        "agent_username": "admin",
        "agent_temp_password": temp_password,
    }


@operator_router.get("/tenants/", response=List[TenantOut], auth=operator_auth)
def list_tenants(request):
    return [
        {"id": str(t.id), "name": t.name, "is_active": t.is_active, "created_at": t.created_at.isoformat()}
        for t in Tenant.objects.all()
    ]


@operator_router.patch("/tenants/{tenant_id}/suspend", response={200: TenantOut}, auth=operator_auth)
def suspend_tenant(request, tenant_id: str):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    tenant.is_active = False
    tenant.save()
    return {"id": str(tenant.id), "name": tenant.name, "is_active": tenant.is_active, "created_at": tenant.created_at.isoformat()}


@operator_router.delete("/tenants/{tenant_id}", response={204: None}, auth=operator_auth)
def delete_tenant(request, tenant_id: str):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    tenant.delete()
    return 204, None


# GET 응답에서 암호화 키를 가리는 마스크. 이 값으로 되돌아오면 변경 없음으로 본다.
_KEY_MASK = "********"


def _config_out(config):
    from django.conf import settings

    return {
        "model_id": config.model_id,
        "system_prompt": config.system_prompt,
        "agent_display_name": config.agent_display_name,
        "webhook_url": config.webhook_url,
        "webhook_type": config.webhook_type,
        "welcome_message": config.welcome_message,
        "brand_name": config.brand_name,
        "hitl_enabled": config.hitl_enabled,
        "hitl_timezone": config.hitl_timezone,
        "hitl_schedule": config.hitl_schedule,
        "hitl_holidays": config.hitl_holidays,
        "require_identity_verification": config.require_identity_verification,
        "topic_scope_enabled": config.topic_scope_enabled,
        "scope_description": config.scope_description,
        "scope_refusal_message": config.scope_refusal_message,
        "llm_provider_type": config.llm_provider_type,
        "llm_base_url": config.llm_base_url,
        # 키는 평문·암호문 모두 노출하지 않고 설정 여부만 마스킹으로 알린다.
        "llm_api_key": _KEY_MASK if config.llm_api_key else "",
        "extraction_model": config.extraction_model,
        "embed_provider_type": config.embed_provider_type,
        "embed_base_url": config.embed_base_url,
        "embed_api_key": _KEY_MASK if config.embed_api_key else "",
        "embed_model": config.embed_model,
        "embed_dim": config.embed_dim,
        "ocr_provider_type": config.ocr_provider_type,
        "ocr_base_url": config.ocr_base_url,
        "ocr_api_key": _KEY_MASK if config.ocr_api_key else "",
        "ocr_model": config.ocr_model,
        "slug": config.tenant.slug or "",   # slug는 Tenant 소유(OneToOne related_name="config")
        "platform_default_providers_enabled": getattr(
            settings, "PLATFORM_DEFAULT_PROVIDERS_ENABLED", False
        ),
    }


@tenant_router.get("/config/", response=TenantConfigOut)
def get_config(request):
    return _config_out(request.auth.tenant.config)


def _embed_signature(config):
    # 벡터 공간을 정하는 요소들. 키 변경만으로는 재임베딩하지 않는다.
    return (config.embed_provider_type, config.embed_base_url, config.embed_model, config.embed_dim)


def _validate_changed_provider(config, body, kind):
    """provider 연결 필드가 바뀌었고 type이 non-empty면 connectivity를 검증한다.
    검증 실패 시 ProviderError를 올린다. 미변경/플랫폼기본이면 검증하지 않는다(연결성만)."""
    from apps.agent.provider_models import validate_provider
    from apps.tenants.crypto import decrypt_secret

    if kind == "llm":
        new_type = body.llm_provider_type if body.llm_provider_type is not None else config.llm_provider_type
        new_base = body.llm_base_url if body.llm_base_url is not None else config.llm_base_url
        new_key_raw, stored_enc = body.llm_api_key, config.llm_api_key
        cur_type, cur_base = config.llm_provider_type, config.llm_base_url
        model = body.model_id if body.model_id is not None else config.model_id
    elif kind == "embed":
        new_type = body.embed_provider_type if body.embed_provider_type is not None else config.embed_provider_type
        new_base = body.embed_base_url if body.embed_base_url is not None else config.embed_base_url
        new_key_raw, stored_enc = body.embed_api_key, config.embed_api_key
        cur_type, cur_base = config.embed_provider_type, config.embed_base_url
        model = body.embed_model if body.embed_model is not None else config.embed_model
    else:  # ocr
        new_type = body.ocr_provider_type if body.ocr_provider_type is not None else config.ocr_provider_type
        new_base = body.ocr_base_url if body.ocr_base_url is not None else config.ocr_base_url
        new_key_raw, stored_enc = body.ocr_api_key, config.ocr_api_key
        cur_type, cur_base = config.ocr_provider_type, config.ocr_base_url
        model = body.ocr_model if body.ocr_model is not None else config.ocr_model

    key_changed = new_key_raw is not None and new_key_raw != _KEY_MASK
    changed = (new_type != cur_type) or (new_base != cur_base) or key_changed
    if not changed or not new_type:
        return
    eff_key = (new_key_raw if key_changed else (decrypt_secret(stored_enc) if stored_enc else "")) or ""
    # tenant_id로 검증 임베딩 프로브도 사용량 기록(issue 204) — embed 분기만 기록한다.
    validate_provider(kind, new_type, new_base, eff_key, model, tenant_id=config.tenant_id)


@tenant_router.patch("/config/", response={200: TenantConfigOut, 400: dict})
def update_config(request, body: TenantConfigIn):
    config = request.auth.tenant.config
    _old_embed = _embed_signature(config)

    # provider 연결 필드가 바뀌었으면 저장 전에 검증 — 실패하면 broken provider를 거부한다.
    from apps.agent.provider_models import ProviderError
    try:
        _validate_changed_provider(config, body, "llm")
        _validate_changed_provider(config, body, "embed")
        _validate_changed_provider(config, body, "ocr")
    except ProviderError as e:
        return 400, {"detail": str(e)}

    # 주제범위 제어를 켜려면 범위 설명이 있어야 한다(빈 채로 켜면 판정 기준이 없어 fail-open으로
    # 무력화됨 — 사용자에게 명시적으로 막아 안내한다, issue 199).
    eff_scope_enabled = body.topic_scope_enabled if body.topic_scope_enabled is not None else config.topic_scope_enabled
    eff_scope_desc = body.scope_description if body.scope_description is not None else config.scope_description
    if eff_scope_enabled and not (eff_scope_desc or "").strip():
        return 400, {"detail": "주제범위 제어를 켜려면 응대 범위 설명을 입력해야 합니다."}

    for field in ("model_id", "system_prompt", "agent_display_name", "webhook_url", "webhook_type", "welcome_message", "brand_name", "hitl_enabled", "hitl_timezone", "hitl_schedule", "hitl_holidays", "require_identity_verification", "topic_scope_enabled", "scope_description", "scope_refusal_message", "llm_provider_type", "llm_base_url", "extraction_model", "embed_provider_type", "embed_base_url", "embed_model", "embed_dim", "ocr_provider_type", "ocr_base_url", "ocr_model"):
        value = getattr(body, field)
        if value is not None:
            setattr(config, field, value)
    # API 키는 평문으로 받아 암호화 저장한다(write-only). 단, GET이 돌려준 마스크 값을
    # 그대로 되돌려 보낸 경우(어드민 round-trip)는 무시해 실제 키를 보존한다.
    from apps.tenants.crypto import encrypt_secret
    if body.llm_api_key is not None and body.llm_api_key != _KEY_MASK:
        config.llm_api_key = encrypt_secret(body.llm_api_key)
    if body.embed_api_key is not None and body.embed_api_key != _KEY_MASK:
        config.embed_api_key = encrypt_secret(body.embed_api_key)
    if body.ocr_api_key is not None and body.ocr_api_key != _KEY_MASK:
        config.ocr_api_key = encrypt_secret(body.ocr_api_key)
    config.save()

    # Embedding Provider(벡터 공간) 변경 시 재임베딩 재구축을 트리거한다(LLM 변경은 제외).
    if _embed_signature(config) != _old_embed:
        from apps.rag.tasks import reembed_tenant_task
        reembed_tenant_task.delay(str(config.tenant_id))
    return _config_out(config)


class ProviderModelsIn(Schema):
    kind: str            # "llm" | "embed"
    type: str            # "" | openai | anthropic | custom
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class ProviderModelsOut(Schema):
    models: List[str]


@tenant_router.post("/providers/models", response={200: ProviderModelsOut, 400: dict})
def provider_models(request, body: ProviderModelsIn):
    """폼의 현재 provider 값으로 모델 목록을 조회한다(어드민 "모델 불러오기").

    마스크 키(********)면 저장된 키를 복호화해 쓰고, type=""(플랫폼 기본)은 kind로
    base_url/api_key를 해석한다. 응답엔 모델 id만(키 미노출). 실패 시 400 + 메시지.
    """
    from django.conf import settings
    from apps.agent.provider_models import list_provider_models, ProviderError

    config = request.auth.tenant.config
    type_, base_url, api_key = body.type, body.base_url, body.api_key

    if type_ == "":
        if not getattr(settings, "PLATFORM_DEFAULT_PROVIDERS_ENABLED", False):
            return 400, {"detail": "플랫폼 기본 Provider가 비활성화되어 있습니다"}
        if body.kind == "embed":
            base_url, api_key = f"{settings.OLLAMA_BASE_URL}/v1", "ollama"
        else:
            base_url, api_key = settings.OPEN_ROUTER_BASE_URL, settings.OPEN_ROUTER_API_KEY
    elif api_key == _KEY_MASK:
        from apps.tenants.crypto import decrypt_secret
        stored = {"embed": config.embed_api_key, "ocr": config.ocr_api_key}.get(
            body.kind, config.llm_api_key
        )
        api_key = decrypt_secret(stored) if stored else ""

    try:
        models = list_provider_models(body.kind, type_, base_url, api_key)
    except ProviderError as e:
        return 400, {"detail": str(e)}
    return 200, {"models": models}


class QuickSetupIn(Schema):
    api_key: str


@tenant_router.post("/providers/quick-setup", response={200: TenantConfigOut, 400: dict})
def provider_quick_setup(request, body: QuickSetupIn):
    """OpenAI 키 1개로 LLM·Embedding·OCR 3종을 기본값으로 한 번에 설정한다(PRD-openai-quick-setup).

    키 검증 실패 시 400 + 미저장(원자성). 임베딩 provider가 바뀌면 재임베딩을 트리거한다
    (신규 테넌트엔 no-op).
    """
    from apps.tenants.quick_setup import openai_quick_setup
    from apps.agent.provider_models import ProviderError

    config = request.auth.tenant.config
    _old_embed = _embed_signature(config)
    try:
        openai_quick_setup(config, body.api_key)
    except ProviderError as e:
        return 400, {"detail": str(e)}

    if _embed_signature(config) != _old_embed:
        from apps.rag.tasks import reembed_tenant_task
        reembed_tenant_task.delay(str(config.tenant_id))
    return 200, _config_out(config)


@tenant_router.post("/reset-key", response={200: ResetKeyOut})
def reset_tenant_key(request):
    from apps.tenants.permissions import require_permission, TENANT_KEY_ROTATE
    require_permission(request.auth, TENANT_KEY_ROTATE)
    tenant = request.auth.tenant
    new_key = tenant.reset_key()
    return 200, {"new_tenant_key": new_key}


@tenant_router.patch("/slug/", response={200: SlugIn})
def update_slug(request, body: SlugIn):
    from ninja.errors import HttpError
    from apps.tenants.slug import is_valid_slug, normalize_slug
    from apps.tenants.permissions import require_permission, SLUG_CHANGE

    require_permission(request.auth, SLUG_CHANGE)
    slug = normalize_slug(body.slug)   # NFC + trim. 한글/대문자 보존, 검증·저장의 단일 기준.
    if not is_valid_slug(slug):
        raise HttpError(400, "Invalid slug format")
    tenant = request.auth.tenant
    # 대소문자 무시 유일성(MyStore↔mystore 충돌) — iexact로 검사.
    if Tenant.objects.filter(slug__iexact=slug).exclude(id=tenant.id).exists():
        raise HttpError(400, "Slug already taken")
    tenant.slug = slug
    tenant.save(update_fields=["slug"])
    return 200, {"slug": tenant.slug}
