"""Refresh Token Service — Session Family 발급·회전·폐기 (ADR-0013, issue 97).

deep module: 해시·family 추적·회전·재사용 감지·절대 캡·폐기를 작은 인터페이스 뒤에 둔다.
- issue_session(subject) -> raw_refresh        : 새 Family 시작(최초 로그인)
- rotate(raw_refresh)   -> (subject, raw)      : 회전. 재사용/만료/폐기/미존재 시 RefreshRejected
- revoke_family(family_id)                     : 한 기기 세션 폐기
- revoke_all(subject)                          : 주체의 전 기기 세션 폐기
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.utils import timezone

from apps.tenants.models import Membership, Operator, RefreshToken

REFRESH_ABSOLUTE_LIFETIME = timedelta(days=14)


class RefreshRejected(Exception):
    """refresh 토큰이 미존재·만료·폐기·재사용되어 거부됨."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _subject_kwargs(subject):
    if isinstance(subject, Operator):
        return {"operator": subject}
    if isinstance(subject, Membership):
        return {"membership": subject}
    raise TypeError(f"지원하지 않는 subject 타입: {type(subject)!r}")


def _issue_in_family(subject_kwargs, family_id, family_expires_at) -> str:
    raw = secrets.token_urlsafe(32)
    RefreshToken.objects.create(
        family_id=family_id,
        token_hash=_hash(raw),
        family_expires_at=family_expires_at,
        **subject_kwargs,
    )
    return raw


def issue_session(subject) -> str:
    return _issue_in_family(
        _subject_kwargs(subject),
        family_id=uuid.uuid4(),
        family_expires_at=timezone.now() + REFRESH_ABSOLUTE_LIFETIME,
    )


def rotate(raw: str):
    row = RefreshToken.objects.filter(token_hash=_hash(raw)).first()
    if row is None or row.revoked:
        raise RefreshRejected()
    if row.used:
        # 이미 회전된 토큰이 다시 왔다 = 도난 정황 → family 전체 폐기
        revoke_family(row.family_id)
        raise RefreshRejected()
    if timezone.now() > row.family_expires_at:
        # 절대 14일 캡 경과 — 회전이 이 시계를 밀지 않으므로 탈취자도 여기서 멈춘다
        raise RefreshRejected()
    row.used = True
    row.save(update_fields=["used"])
    subject = row.operator or row.membership
    subject_kwargs = {"operator": row.operator} if row.operator_id else {"membership": row.membership}
    new_raw = _issue_in_family(subject_kwargs, row.family_id, row.family_expires_at)
    return subject, new_raw


def revoke_family(family_id) -> None:
    RefreshToken.objects.filter(family_id=family_id, revoked=False).update(revoked=True)


def revoke_all(subject) -> None:
    RefreshToken.objects.filter(revoked=False, **_subject_kwargs(subject)).update(revoked=True)


def revoke_session(raw: str) -> None:
    """주어진 refresh 원문이 속한 Family(=이 기기 세션)를 폐기한다. 미존재면 무시."""
    row = RefreshToken.objects.filter(token_hash=_hash(raw)).first()
    if row:
        revoke_family(row.family_id)


def prune_refresh_tokens() -> int:
    """만료(절대 캡 경과)·폐기 RefreshToken row를 삭제하고 삭제 건수를 반환한다.

    동시 Family 수 상한이 없으므로 이 정리가 유일한 GC 경로(주기 작업으로 호출).
    """
    from django.db.models import Q

    qs = RefreshToken.objects.filter(
        Q(revoked=True) | Q(family_expires_at__lt=timezone.now())
    )
    deleted, _ = qs.delete()
    return deleted
