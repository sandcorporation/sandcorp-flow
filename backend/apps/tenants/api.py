import secrets
from typing import List, Literal
from ninja import Router, Schema
from urllib.parse import urlencode
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from apps.tenants.auth import (
    create_operator_access_token, create_member_access_token,
    operator_auth, tenant_key_auth, member_auth, member_or_tenant_key_auth,
)
from apps.tenants.auth_cookies import (
    set_refresh_cookie, clear_refresh_cookie, cookie_name, OPERATOR, MEMBER,
)
from apps.tenants.members import (
    InviteRejected, accept_invite, active_memberships, create_invite, issue_member_ticket,
    pending_invites, read_member_ticket,
)
from apps.tenants import withdrawal
from apps.tenants.permissions import AGENTS_MANAGE, require_permission
from apps.tenants.refresh_tokens import (
    issue_session, rotate, revoke_all, revoke_session, RefreshRejected,
)
from apps.tenants.models import Membership, Operator, Tenant, TenantConfig, TenantInvite
from apps.tenants.operator_login import (
    ATTEMPT_MAX_AGE_SECONDS, LoginAttempt, LoginEntry, LoginRejected, MemberLogin, complete_login,
    start_login,
)

operator_router = Router(tags=["operator"])
tenant_router = Router(tags=["tenant"], auth=member_auth)
member_router = Router(tags=["tenant-members"])
invite_router = Router(tags=["tenant-invites"])
# sandcorp-auth 로그인(/api/auth/*). Operator 전용이 아니다 — 일반 사용자(#40)도 같은 콜백을 쓴다.
auth_router = Router(tags=["auth"])


class LoginOut(Schema):
    access_token: str


class TenantIn(Schema):
    name: str


class TenantOut(Schema):
    id: str
    name: str
    is_active: bool
    created_at: str
    # 활성 Admin 수. 0이면 「주인 없음」 — 마지막 Admin이 탈퇴한 Tenant(#55). Operator가 초대로 복구한다.
    active_admin_count: int = 0


class TenantCreatedOut(Schema):
    id: str
    name: str
    is_active: bool
    tenant_key: str
    # 첫 Admin 초대 링크(7일·1회용). 비밀번호를 만들어 주지 않는다(#40).
    admin_invite_url: str
    admin_invite_expires_at: str


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
    deploy_requires_approval: bool = False
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
    deploy_requires_approval: bool | None = None
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


class MemberOut(Schema):
    id: str
    user_id: str
    name: str | None
    email: str | None
    sub_tail: str  # 이름·이메일 캐시가 없을 때 목록에서 구별하는 sub 끝 6자리
    is_active: bool
    role: str


class MeOut(Schema):
    id: str
    tenant_id: str
    tenant_name: str
    role: str
    name: str | None
    email: str | None


class MemberRoleIn(Schema):
    role: str


class InviteIn(Schema):
    role: str = Membership.ROLE_MEMBER


class InviteOut(Schema):
    id: str
    role: str
    expires_at: str
    created_at: str


class InviteCreatedOut(InviteOut):
    # 원문 토큰과 그것으로 로그인을 시작하는 주소. 이 응답에서 한 번만 나온다(저장은 해시만).
    token: str
    url: str


def _member_out(m: Membership) -> dict:
    return {
        "id": str(m.id), "user_id": str(m.user_id), "name": m.user.name, "email": m.user.email,
        "sub_tail": m.user.sub[-6:], "is_active": m.is_active, "role": m.role,
    }


def _invite_out(invite: TenantInvite) -> dict:
    return {
        "id": str(invite.id), "role": invite.role,
        "expires_at": invite.expires_at.isoformat(), "created_at": invite.created_at.isoformat(),
    }


def _invite_url(request, raw: str) -> str:
    return request.build_absolute_uri(f"/api/auth/authorize/member?{urlencode({'invite': raw})}")


def _invite_created(request, invite: TenantInvite, raw: str) -> dict:
    return {**_invite_out(invite), "token": raw, "url": _invite_url(request, raw)}


@member_router.post("/auth/refresh", response={200: LoginOut, 401: dict}, auth=None)
def member_refresh(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(MEMBER))
    if not raw:
        return 401, {"detail": "No refresh token"}
    try:
        subject, new_raw = rotate(raw)
    except RefreshRejected:
        return 401, {"detail": "Invalid refresh token"}
    if not isinstance(subject, Membership) or not subject.is_active or not subject.tenant.is_active:
        return 401, {"detail": "Invalid refresh token"}
    set_refresh_cookie(response, subject, new_raw)
    return 200, {"access_token": create_member_access_token(subject)}


@member_router.post("/auth/logout", response={200: dict}, auth=None)
def member_logout(request, response: HttpResponse):
    raw = request.COOKIES.get(cookie_name(MEMBER))
    if raw:
        revoke_session(raw)  # 이 기기 Family만 폐기
    clear_refresh_cookie(response, MEMBER)
    return 200, {"detail": "logged out"}


@member_router.post("/auth/logout-all", response={200: dict}, auth=member_auth)
def member_logout_all(request, response: HttpResponse):
    revoke_all(request.auth)  # 이 멤버십의 전 기기 Family 폐기
    clear_refresh_cookie(response, MEMBER)
    return 200, {"detail": "logged out everywhere"}


@member_router.get("/me", response=MeOut, auth=member_auth)
def member_me(request):
    m = request.auth
    return {
        "id": str(m.id), "tenant_id": str(m.tenant_id), "tenant_name": m.tenant.name,
        "role": m.role, "name": m.user.name, "email": m.user.email,
    }


def _get_tenant_from_auth(auth_obj) -> Tenant:
    if isinstance(auth_obj, Membership):
        return auth_obj.tenant
    return auth_obj


def _is_last_active_admin(member: Membership) -> bool:
    """이 멤버가 조직의 마지막 활성 Admin인지 — 비활성화·강등 차단용(lockout 방지, ADR-0025 D)."""
    if member.role != Membership.ROLE_ADMIN or not member.is_active:
        return False
    return not Membership.objects.filter(
        tenant_id=member.tenant_id, role=Membership.ROLE_ADMIN, is_active=True
    ).exclude(id=member.id).exists()


@member_router.get("/", response=list[MemberOut], auth=member_or_tenant_key_auth)
def list_members(request):
    tenant = _get_tenant_from_auth(request.auth)
    return [
        _member_out(m)
        for m in Membership.objects.select_related("user").filter(tenant=tenant).order_by("created_at")
    ]


@member_router.patch("/{member_id}/deactivate", response={200: MemberOut, 409: dict}, auth=member_or_tenant_key_auth)
def deactivate_member(request, member_id: str):
    require_permission(request.auth, AGENTS_MANAGE)
    tenant = _get_tenant_from_auth(request.auth)
    member = get_object_or_404(Membership.objects.select_related("user"), id=member_id, tenant=tenant)
    if _is_last_active_admin(member):
        return 409, {"detail": "마지막 Admin은 비활성화할 수 없습니다."}
    member.is_active = False
    member.save(update_fields=["is_active"])
    revoke_all(member)  # 열린 테넌트 세션도 닫는다
    return 200, _member_out(member)


@member_router.patch("/{member_id}/role", response={200: MemberOut, 400: dict, 409: dict}, auth=member_or_tenant_key_auth)
def change_member_role(request, member_id: str, body: MemberRoleIn):
    require_permission(request.auth, AGENTS_MANAGE)
    tenant = _get_tenant_from_auth(request.auth)
    member = get_object_or_404(Membership.objects.select_related("user"), id=member_id, tenant=tenant)
    if body.role not in (Membership.ROLE_ADMIN, Membership.ROLE_MEMBER):
        return 400, {"detail": "유효하지 않은 역할입니다."}
    if body.role == Membership.ROLE_MEMBER and _is_last_active_admin(member):
        return 409, {"detail": "마지막 Admin은 강등할 수 없습니다."}
    member.role = body.role
    member.save(update_fields=["role"])
    return 200, _member_out(member)


# ── 팀원 초대 링크(#40). TENANT_KEY로도 발급된다 — Admin 초대가 break-glass 복구 채널(ADR-0025 D) ──


@invite_router.get("/", response=list[InviteOut], auth=member_or_tenant_key_auth)
def list_invites(request):
    require_permission(request.auth, AGENTS_MANAGE)
    return [_invite_out(i) for i in pending_invites(_get_tenant_from_auth(request.auth))]


@invite_router.post("/", response={201: InviteCreatedOut, 400: dict}, auth=member_or_tenant_key_auth)
def create_tenant_invite(request, body: InviteIn):
    require_permission(request.auth, AGENTS_MANAGE)
    if body.role not in (Membership.ROLE_ADMIN, Membership.ROLE_MEMBER):
        return 400, {"detail": "유효하지 않은 역할입니다."}
    by = request.auth if isinstance(request.auth, Membership) else None
    invite, raw = create_invite(_get_tenant_from_auth(request.auth), body.role, created_by=by)
    return 201, _invite_created(request, invite, raw)


@invite_router.delete("/{invite_id}", response={204: None}, auth=member_or_tenant_key_auth)
def revoke_tenant_invite(request, invite_id: str):
    require_permission(request.auth, AGENTS_MANAGE)
    invite = get_object_or_404(TenantInvite, id=invite_id, tenant=_get_tenant_from_auth(request.auth))
    invite.revoked = True
    invite.save(update_fields=["revoked"])
    return 204, None


# ── 로그인: sandcorp-auth 기밀 클라이언트(ADR-0028, #21 대표·#40 고객) ──
# 브라우저 이동(302)이라 생성 클라이언트에 넣지 않는다. 시도(state·PKCE·nonce·초대)는 서명된 httpOnly
# 쿠키에 맡긴다 — 인가 서버에서 돌아오는 교차 사이트 GET에도 실려야 하므로 SameSite=Lax.
LOGIN_ATTEMPT_COOKIE = "op_login"
LOGIN_ATTEMPT_COOKIE_PATH = "/api/auth/callback"
# 콜백 뒤 「테넌트 선택」·「온보딩(#41)」을 잇는 서명 표(10분). /api/auth/member/* 에만 실린다.
MEMBER_TICKET_COOKIE = "member_ticket"
MEMBER_TICKET_COOKIE_PATH = "/api/auth/member"


def _redirect(target: str, **params: str) -> HttpResponseRedirect:
    if params:
        target = f"{target}?{urlencode(params)}"
    response = HttpResponseRedirect(target)
    response.delete_cookie(LOGIN_ATTEMPT_COOKIE, path=LOGIN_ATTEMPT_COOKIE_PATH)
    return response


def _login_redirect(reason: str | None = None) -> HttpResponseRedirect:
    if reason:
        return _redirect(settings.AUTH_LOGIN_REDIRECT, login_error=reason)
    return _redirect(settings.AUTH_LOGIN_REDIRECT)


def _set_member_ticket(response: HttpResponse, user) -> None:
    from apps.tenants.members import TICKET_MAX_AGE_SECONDS

    response.set_cookie(
        MEMBER_TICKET_COOKIE,
        issue_member_ticket(user),
        max_age=TICKET_MAX_AGE_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=MEMBER_TICKET_COOKIE_PATH,
    )


def _member_landing(login: MemberLogin) -> HttpResponseRedirect:
    """일반 사용자 콜백 끝. 초대가 있으면 먼저 멤버십을 만들고, 멤버십 수에 따라
    1개 → 테넌트 세션, 여럿 → 선택(`?select_tenant=1`), 없음 → 「온보딩 필요」(`?onboarding=1`)."""
    target = settings.AUTH_MEMBER_REDIRECT
    if login.invite:
        try:
            joined = accept_invite(login.user, login.invite)
        except InviteRejected as exc:
            return _redirect(target, login_error=exc.reason)
        if joined.is_active and joined.tenant.is_active:
            response = _redirect(target)
            set_refresh_cookie(response, joined, issue_session(joined))
            return response
    memberships = active_memberships(login.user)
    if len(memberships) == 1:
        response = _redirect(target)
        set_refresh_cookie(response, memberships[0], issue_session(memberships[0]))
        return response
    response = _redirect(target, **({"select_tenant": "1"} if memberships else {"onboarding": "1"}))
    _set_member_ticket(response, login.user)
    return response


def _start(entry: LoginEntry, invite: str | None = None) -> HttpResponseRedirect:
    try:
        url, attempt = start_login(invite=invite, entry=entry)
    except LoginRejected as exc:
        return _rejected_redirect(attempt_entry=entry, reason=exc.reason)
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


def _rejected_redirect(attempt_entry: LoginEntry, reason: str) -> HttpResponseRedirect:
    """거절은 시작한 화면으로 돌려보낸다(관리 화면 / 고객 화면)."""
    if attempt_entry == "member":
        return _redirect(settings.AUTH_MEMBER_REDIRECT, login_error=reason)
    return _login_redirect(reason)


@auth_router.get("/authorize", auth=None, include_in_schema=False)
def login_authorize(request, invite: str | None = None):
    """관리 화면(Operator) 진입 — `admin`까지 요청한다. `invite`가 붙으면 고객 진입으로 바꾼다
    (#61에서 나간 옛 초대 링크 호환)."""
    return _start("member" if invite else "operator", invite)


@auth_router.get("/authorize/member", auth=None, include_in_schema=False)
def login_authorize_member(request, invite: str | None = None):
    """고객(member) 진입 — `admin`을 요청하지 않는다. 초대 링크도 이 주소다."""
    return _start("member", invite)


@auth_router.get("/callback", auth=None, include_in_schema=False)
def login_callback(request):
    attempt = LoginAttempt.loads(request.COOKIES.get(LOGIN_ATTEMPT_COOKIE))
    try:
        outcome = complete_login(request.GET.dict(), attempt)
    except LoginRejected as exc:
        return _rejected_redirect(attempt.entry if attempt else "operator", exc.reason)
    if isinstance(outcome, MemberLogin):
        return _member_landing(outcome)
    response = _login_redirect()
    # Refresh 쿠키 경로는 /api/operator/auth(refresh·logout만 받는다). 콜백 응답에서 심어도 된다.
    set_refresh_cookie(response, outcome, issue_session(outcome))
    return response


@auth_router.post("/webhook", auth=None, include_in_schema=False)
def auth_webhook(request):
    """sandcorp-auth 탈퇴 웹훅(#55). 인증은 서명(HMAC)뿐이다 — 본문을 Ninja가 파싱하지 않게 인자를 두지 않는다."""
    try:
        withdrawal.receive(request.body, request.headers.get("X-Sandcorp-Signature"))
    except withdrawal.WebhookRejected as exc:
        return HttpResponse(str(exc), status=exc.status, content_type="text/plain")
    return HttpResponse(status=200)


class MemberTenantOut(Schema):
    tenant_id: str
    tenant_name: str
    role: str


class SelectTenantIn(Schema):
    tenant_id: str


class MemberTicketOut(Schema):
    name: str | None
    email: str | None
    tenants: list[MemberTenantOut]


@auth_router.get("/member/tenants", response={200: MemberTicketOut, 401: dict}, auth=None)
def member_tenants(request):
    """콜백 뒤 표(`member_ticket` 쿠키)로 고를 수 있는 테넌트 목록. 비어 있으면 온보딩 대상(#41)."""
    user = read_member_ticket(request.COOKIES.get(MEMBER_TICKET_COOKIE))
    if user is None:
        return 401, {"detail": "로그인이 필요합니다."}
    return 200, {
        "name": user.name,
        "email": user.email,
        "tenants": [
            {"tenant_id": str(m.tenant_id), "tenant_name": m.tenant.name, "role": m.role}
            for m in active_memberships(user)
        ],
    }


@auth_router.post("/member/select", response={200: LoginOut, 401: dict, 403: dict}, auth=None)
def member_select_tenant(request, body: SelectTenantIn, response: HttpResponse):
    """고른 테넌트의 세션을 연다(Refresh 쿠키 + Access Token). 멤버가 아닌 테넌트는 403."""
    user = read_member_ticket(request.COOKIES.get(MEMBER_TICKET_COOKIE))
    if user is None:
        return 401, {"detail": "로그인이 필요합니다."}
    membership = next((m for m in active_memberships(user) if str(m.tenant_id) == body.tenant_id), None)
    if membership is None:
        return 403, {"detail": "이 테넌트의 멤버가 아닙니다."}
    set_refresh_cookie(response, membership, issue_session(membership))
    response.delete_cookie(MEMBER_TICKET_COOKIE, path=MEMBER_TICKET_COOKIE_PATH)
    return 200, {"access_token": create_member_access_token(membership)}


class OnboardingIn(Schema):
    name: str
    slug: str


class OnboardingOut(Schema):
    tenant_id: str
    tenant_name: str
    access_token: str


@auth_router.post(
    "/member/tenants", response={201: OnboardingOut, 400: dict, 401: dict, 409: dict, 429: dict}, auth=None
)
def member_create_tenant(request, body: OnboardingIn, response: HttpResponse):
    """온보딩(#41): 표(`member_ticket`)를 가진 member가 조직을 만들고 첫 Admin으로 바로 세션을 연다."""
    from apps.tenants import onboarding

    user = read_member_ticket(request.COOKIES.get(MEMBER_TICKET_COOKIE))
    if user is None:
        return 401, {"detail": "로그인이 필요합니다."}
    if not onboarding.allow_attempt(onboarding.client_ip(request)):
        return 429, {"detail": "잠시 후 다시 시도해 주세요."}
    try:
        membership = onboarding.create_tenant_for(user, body.name, body.slug)
    except onboarding.OnboardingRejected as exc:
        status = 409 if exc.reason in ("name_taken", "slug_taken", "limit_reached") else 400
        return status, {"detail": exc.message, "reason": exc.reason}
    set_refresh_cookie(response, membership, issue_session(membership))
    response.delete_cookie(MEMBER_TICKET_COOKIE, path=MEMBER_TICKET_COOKIE_PATH)
    return 201, {
        "tenant_id": str(membership.tenant_id),
        "tenant_name": membership.tenant.name,
        "access_token": create_member_access_token(membership),
    }


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
    from apps.flows.defaults import ensure_default_flow

    raw_key = secrets.token_urlsafe(32)
    with transaction.atomic():
        tenant = Tenant.objects.create_with_key(name=body.name, raw_key=raw_key)
        ensure_default_flow(tenant.id)
    invite, raw_invite = create_invite(tenant, Membership.ROLE_ADMIN)
    return 201, {
        "id": str(tenant.id),
        "name": tenant.name,
        "is_active": tenant.is_active,
        "tenant_key": raw_key,
        "admin_invite_url": _invite_url(request, raw_invite),
        "admin_invite_expires_at": invite.expires_at.isoformat(),
    }


def _tenant_out(t: Tenant, active_admin_count: int) -> dict:
    return {
        "id": str(t.id), "name": t.name, "is_active": t.is_active,
        "created_at": t.created_at.isoformat(), "active_admin_count": active_admin_count,
    }


@operator_router.get("/tenants/", response=List[TenantOut], auth=operator_auth)
def list_tenants(request):
    # 활성 Admin 수는 한 쿼리로 센다(N+1 금지).
    tenants = Tenant.objects.annotate(
        _admins=Count(
            "memberships",
            filter=Q(memberships__role=Membership.ROLE_ADMIN, memberships__is_active=True),
        )
    )
    return [_tenant_out(t, t._admins) for t in tenants]  # type: ignore[attr-defined]  # annotate 필드


@operator_router.patch("/tenants/{tenant_id}/suspend", response={200: TenantOut}, auth=operator_auth)
def suspend_tenant(request, tenant_id: str):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    tenant.is_active = False
    tenant.save()
    admins = Membership.objects.filter(tenant=tenant, role=Membership.ROLE_ADMIN, is_active=True).count()
    return _tenant_out(tenant, admins)


class AdminInviteOut(Schema):
    url: str
    expires_at: str


@operator_router.post(
    "/tenants/{tenant_id}/admin-invite", response={201: AdminInviteOut, 400: dict}, auth=operator_auth
)
def operator_admin_invite(request, tenant_id: str):
    """주인 없는 Tenant(또는 추가 Admin)를 위한 Admin 초대 링크(7일·1회용). 테넌트 생성 때와 같은 경로."""
    tenant = get_object_or_404(Tenant, id=tenant_id)
    if not tenant.is_active:
        return 400, {"detail": "정지된 Tenant에는 초대를 만들 수 없습니다"}
    invite, raw = create_invite(tenant, Membership.ROLE_ADMIN)
    return 201, {"url": _invite_url(request, raw), "expires_at": invite.expires_at.isoformat()}


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
        "deploy_requires_approval": config.deploy_requires_approval,
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


class TrialOut(Schema):
    state: Literal["byok", "trial", "exhausted", "unavailable", "platform_default"]
    remaining: int
    limit: int


@tenant_router.get("/trial/", response=TrialOut)
def get_trial(request):
    """체험 한도 상태(#41) — 관리 화면 배너·요금 화면이 읽는다."""
    from apps.tenants import trial

    config = request.auth.tenant.config
    return {"state": trial.trial_state(config), "remaining": trial.remaining(config), "limit": settings.TRIAL_TURNS}


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
    if body.deploy_requires_approval is not None:  # 배포 승인 규칙을 바꾸는 건 승인 권한자만(#28)
        from apps.tenants.permissions import FLOW_APPROVE
        require_permission(request.auth, FLOW_APPROVE)
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

    for field in ("model_id", "system_prompt", "agent_display_name", "webhook_url", "webhook_type", "welcome_message", "brand_name", "hitl_enabled", "hitl_timezone", "hitl_schedule", "hitl_holidays", "require_identity_verification", "deploy_requires_approval", "topic_scope_enabled", "scope_description", "scope_refusal_message", "llm_provider_type", "llm_base_url", "extraction_model", "embed_provider_type", "embed_base_url", "embed_model", "embed_dim", "ocr_provider_type", "ocr_base_url", "ocr_model"):
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
