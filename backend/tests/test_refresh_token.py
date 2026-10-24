"""Issue 97 — Refresh Token Service (Session Family 발급·회전·폐기) deep module.

ADR-0013. 실제 DB·실제 crypto로 외부 행위만 검증한다(내부 컬럼 들여다보지 않음).
"""
import pytest


def _make_operator(username="op1"):
    from apps.tenants.models import Operator
    op = Operator.objects.create(username=username)
    return op


# ── Tracer bullet: 발급 → 회전 ────────────────────────────────────────────────

@pytest.mark.django_db
def test_issue_and_rotate_returns_new_token_for_same_subject():
    from apps.tenants.refresh_tokens import issue_session, rotate

    op = _make_operator()
    raw = issue_session(op)

    subject, new_raw = rotate(raw)

    assert subject == op
    assert new_raw and new_raw != raw


# ── 재사용 감지: 회전된 토큰을 다시 쓰면 family 전체 폐기 ──────────────────────

@pytest.mark.django_db
def test_reusing_rotated_token_revokes_whole_family():
    from apps.tenants.refresh_tokens import issue_session, rotate, RefreshRejected

    op = _make_operator()
    raw = issue_session(op)
    _, new_raw = rotate(raw)  # raw 소비됨

    # 옛(이미 회전된) 토큰 재사용 → 거부
    with pytest.raises(RefreshRejected):
        rotate(raw)

    # 도난 간주로 family 전체 폐기 → 정상 발급됐던 new_raw도 죽는다
    with pytest.raises(RefreshRejected):
        rotate(new_raw)


# ── 절대 수명 상한(14일): 회전이 연장하지 않음 ────────────────────────────────

@pytest.mark.django_db
def test_rotation_rejected_after_absolute_cap():
    from datetime import timedelta
    from django.utils import timezone
    from apps.tenants.models import RefreshToken
    from apps.tenants.refresh_tokens import issue_session, rotate, RefreshRejected

    op = _make_operator()
    raw = issue_session(op)
    # 14일이 지나 절대 캡을 넘긴 상황을 시뮬레이션(테스트 셋업)
    RefreshToken.objects.filter(operator=op).update(
        family_expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(RefreshRejected):
        rotate(raw)


@pytest.mark.django_db
def test_rotation_inherits_absolute_cap_without_extending():
    from apps.tenants.models import RefreshToken
    from apps.tenants.refresh_tokens import issue_session, rotate

    op = _make_operator()
    raw = issue_session(op)
    original_cap = RefreshToken.objects.get(operator=op).family_expires_at

    _, r2 = rotate(raw)
    rotate(r2)  # 몇 번 더 회전해도

    caps = set(RefreshToken.objects.filter(operator=op).values_list("family_expires_at", flat=True))
    assert caps == {original_cap}  # 모든 회전 토큰이 같은 절대 캡을 상속(연장 없음)


# ── 다중기기: Family 단위 폐기, revoke_all로 전체 폐기 ─────────────────────────

@pytest.mark.django_db
def test_revoke_all_kills_every_family():
    from apps.tenants.refresh_tokens import issue_session, rotate, revoke_all, RefreshRejected

    op = _make_operator()
    raw1 = issue_session(op)  # 기기 1
    raw2 = issue_session(op)  # 기기 2

    revoke_all(op)

    with pytest.raises(RefreshRejected):
        rotate(raw1)
    with pytest.raises(RefreshRejected):
        rotate(raw2)


@pytest.mark.django_db
def test_revoking_one_device_leaves_other_device_alive():
    from apps.tenants.models import RefreshToken
    from apps.tenants.refresh_tokens import issue_session, rotate, revoke_family, _hash, RefreshRejected

    op = _make_operator()
    raw1 = issue_session(op)  # 기기 1
    raw2 = issue_session(op)  # 기기 2
    fam1 = RefreshToken.objects.get(token_hash=_hash(raw1)).family_id

    revoke_family(fam1)

    with pytest.raises(RefreshRejected):
        rotate(raw1)
    # 기기 2는 멀쩡 — 한 기기 폐기가 다른 기기로 번지지 않는다
    subject, _ = rotate(raw2)
    assert subject == op


@pytest.mark.django_db
def test_membership_subject_round_trips():
    from apps.tenants.models import Tenant
    from tests.utils import make_membership
    from apps.tenants.refresh_tokens import issue_session, rotate

    import secrets
    tenant = Tenant.objects.create_with_key(name="T", raw_key=secrets.token_urlsafe(16))
    agent = make_membership(tenant, "member")

    raw = issue_session(agent)
    subject, new_raw = rotate(raw)

    assert subject == agent
    assert new_raw != raw
