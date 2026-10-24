"""고객(일반 사용자) 멤버십·초대·테넌트 선택 — deep module (#40, ADR-0028 「고객 토큰 검증」).

- `member_user(iss, sub, identity)`      : 일반 사용자 `User(iss, sub)`를 찾거나 만들고 프로필 캐시를 갱신한다.
- `active_memberships(user)`             : 지금 세션을 열 수 있는 멤버십(멤버십·Tenant 모두 활성).
- `create_invite(tenant, role, by)`      : 초대 링크 발급 → (초대 행, 원문 토큰). 원문은 이때 한 번만 나온다.
- `accept_invite(user, raw)`             : 초대를 써서 멤버십을 만든다. 만료·재사용·폐기·없음은 `InviteRejected`.
- `issue_member_ticket(user)` / `read_member_ticket(raw)`
                                         : 콜백 뒤 「테넌트 선택」·「온보딩」(#41)을 잇는 10분짜리 서명 표.
- `forget_user(iss, sub)`                : sub로 일반 사용자를 잊는다(멤버십·테넌트 세션까지). 탈퇴 웹훅(#55)이 부른다.

이메일은 식별에 쓰지 않는다 — 사용자는 언제나 `(iss, sub)`다.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.tenants.models import Membership, Tenant, TenantInvite, User

if TYPE_CHECKING:
    from apps.tenants.operator_login import IdentityClaims

INVITE_LIFETIME = timedelta(days=7)
TICKET_MAX_AGE_SECONDS = 600
_TICKET_SALT = "flow.member-login.ticket"

InviteRejectReason = Literal["invite_invalid", "invite_expired", "invite_used"]


class InviteRejected(Exception):
    def __init__(self, reason: InviteRejectReason) -> None:
        super().__init__(reason)
        self.reason: InviteRejectReason = reason


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def member_user(iss: str, sub: str, identity: IdentityClaims | None = None) -> User:
    with transaction.atomic():
        user, _ = User.objects.get_or_create(iss=iss, sub=sub, defaults={"acct": User.Acct.MEMBER})
        if identity is not None:
            user.name = identity.get("name")
            user.email = identity.get("email")
            user.email_verified = identity.get("email_verified")
            user.save(update_fields=["name", "email", "email_verified", "updated_at"])
    return user


def active_memberships(user: User) -> list[Membership]:
    return list(
        Membership.objects.select_related("tenant", "user")
        .filter(user=user, is_active=True, tenant__is_active=True)
        .order_by("created_at")
    )


def create_invite(
    tenant: Tenant, role: str, created_by: Membership | None = None
) -> tuple[TenantInvite, str]:
    if role not in (Membership.ROLE_ADMIN, Membership.ROLE_MEMBER):
        raise ValueError(f"알 수 없는 역할: {role!r}")
    raw = secrets.token_urlsafe(32)  # 256비트 — 추측 불가
    invite = TenantInvite.objects.create(
        tenant=tenant,
        role=role,
        token_hash=_hash(raw),
        created_by=created_by,
        expires_at=timezone.now() + INVITE_LIFETIME,
    )
    return invite, raw


def pending_invites(tenant: Tenant) -> list[TenantInvite]:
    """아직 쓸 수 있는 초대(안 쓰고, 안 폐기하고, 안 만료)."""
    return list(
        TenantInvite.objects.filter(
            tenant=tenant, used_at__isnull=True, revoked=False, expires_at__gt=timezone.now()
        ).order_by("-created_at")
    )


def accept_invite(user: User, raw: str) -> Membership:
    """초대 1회 사용. 이미 활성 멤버면 그대로 두고(역할을 바꾸지 않는다), 비활성 멤버면 초대 역할로 되살린다."""
    with transaction.atomic():
        invite = (
            TenantInvite.objects.select_for_update()
            .select_related("tenant")
            .filter(token_hash=_hash(raw))
            .first()
        )
        if invite is None or invite.revoked or not invite.tenant.is_active:
            raise InviteRejected("invite_invalid")
        if invite.used_at is not None:
            raise InviteRejected("invite_used")
        if timezone.now() >= invite.expires_at:
            raise InviteRejected("invite_expired")
        membership, created = Membership.objects.get_or_create(
            user=user, tenant=invite.tenant, defaults={"role": invite.role}
        )
        if not created and not membership.is_active:
            membership.is_active = True
            membership.role = invite.role
            membership.save(update_fields=["is_active", "role"])
        invite.used_at = timezone.now()
        invite.used_by = user
        invite.save(update_fields=["used_at", "used_by"])
    return membership


def issue_member_ticket(user: User) -> str:
    return signing.dumps({"u": str(user.id)}, salt=_TICKET_SALT)


def read_member_ticket(raw: str | None) -> User | None:
    if not raw:
        return None
    try:
        payload = signing.loads(raw, salt=_TICKET_SALT, max_age=TICKET_MAX_AGE_SECONDS)
    except signing.BadSignature:  # 만료(SignatureExpired)도 여기로 온다
        return None
    user_id = payload.get("u") if isinstance(payload, dict) else None
    if not isinstance(user_id, str):
        return None
    return User.objects.filter(id=user_id, acct=User.Acct.MEMBER).first()


def forget_user(iss: str, sub: str) -> bool:
    """일반 사용자를 지운다 — 멤버십·테넌트 세션(Refresh Family)은 CASCADE, 상담 기록의 담당자는 비운다.

    대표(`acct="owner"`)는 지우지 않는다(Operator가 함께 사라진다). 지운 게 있으면 True.
    마지막 Admin이 떠난 Tenant는 TENANT_KEY로 Admin 초대 링크를 다시 발급해 복구한다(ADR-0025 D).
    """
    deleted, _ = User.objects.filter(iss=iss, sub=sub, acct=User.Acct.MEMBER).delete()
    return deleted > 0
