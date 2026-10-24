"""#55 — sandcorp-auth 탈퇴 웹훅(`user.deleted`)을 `/api/auth/webhook`으로 받는다.

규칙 원문은 sandcorp-auth `docs/resource-server-token-validation.md` 11절(W1~W5).
auth가 보내는 요청을 그대로 흉내 낸다 — 본문 바이트에 `HMAC-SHA256(비밀, "<t>." + 본문)`을 붙인다.
엔드포인트·검증·DB·세션 폐기는 실제로 돈다. 시계 경계(300/301초)는 문서 예시의 `now=` 인자로 고정한다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from django.test import Client

from tests.utils import make_membership

SECRET = "whsec_test_0123456789abcdef"
ISS = "https://auth.sandcorp.co.kr"
SUB = "m_01JB0Z8F3W4Q2Y6K7M9N0P1R2S"
URL = "/api/auth/webhook"


@pytest.fixture(autouse=True)
def webhook_secret(settings):
    settings.AUTH_WEBHOOK_SECRET = SECRET
    settings.AUTH_ISSUER = ISS


def event_body(event_id: str = "01JB0Z8F3W4Q2Y6K7M9N0P1R2S", type_: str = "user.deleted", sub: str = SUB) -> bytes:
    # auth가 보내는 모양(공백 없는 JSON). 받는 쪽은 이 바이트 그대로 검증해야 한다.
    return json.dumps(
        {"id": event_id, "type": type_, "sub": sub, "occurred_at": "2026-10-24T03:15:00Z"},
        separators=(",", ":"),
    ).encode()


def sign(body: bytes, t: int | None = None, secret: str = SECRET) -> str:
    t = int(time.time()) if t is None else t
    v1 = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def deliver(body: bytes, signature: str | None = None, *, event_id: str | None = None):
    headers = {"HTTP_X_SANDCORP_SIGNATURE": sign(body) if signature is None else signature,
               "HTTP_X_SANDCORP_EVENT": "user.deleted"}
    if event_id:
        headers["HTTP_X_SANDCORP_DELIVERY"] = event_id
    return Client().post(URL, data=body, content_type="application/json", **headers)


def new_tenant(name: str = "Withdraw Co"):
    import secrets

    from apps.tenants.models import Tenant

    return Tenant.objects.create_with_key(name=name, raw_key=secrets.token_urlsafe(16))


# ── W5 정상 처리 ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_user_deleted_forgets_the_user_and_revokes_sessions() -> None:
    from apps.tenants.models import Membership, RefreshToken, User
    from apps.tenants.refresh_tokens import issue_session

    tenant = new_tenant()
    make_membership(tenant, "admin")  # 다른 Admin
    m = make_membership(tenant, "member", sub=SUB, name="홍길동", email="hong@example.com")
    raw_refresh = issue_session(m)

    r = deliver(event_body())

    assert r.status_code == 200
    assert not User.objects.filter(sub=SUB).exists()  # 이름·이메일 캐시까지 사라진다
    assert not Membership.objects.filter(user__sub=SUB).exists()
    assert not RefreshToken.objects.filter(membership_id=m.id).exists()
    # 탈퇴 뒤 그 sub의 Refresh로는 갱신할 수 없다.
    c = Client()
    c.cookies["member_refresh"] = raw_refresh
    assert c.post("/api/tenant/members/auth/refresh").status_code == 401


@pytest.mark.django_db
def test_owner_sub_is_never_removed() -> None:
    from apps.tenants.models import Operator
    from apps.tenants.operator_login import owner_operator

    owner_operator(ISS, "farrar142")

    assert deliver(event_body(sub="farrar142")).status_code == 200
    assert Operator.objects.filter(user__sub="farrar142").exists()


@pytest.mark.django_db
def test_unknown_sub_is_acknowledged() -> None:
    assert deliver(event_body(sub="m_NOBODY")).status_code == 200


@pytest.mark.django_db
def test_last_admin_withdrawal_keeps_the_tenant_running_and_recoverable() -> None:
    """마지막 Admin이 탈퇴해도 Tenant는 지우지도 멈추지도 않는다 — TENANT_KEY로 Admin 초대를 다시 낸다."""
    import secrets

    from apps.tenants.models import Membership, Tenant, TenantConfig

    raw_key = secrets.token_urlsafe(16)
    tenant = Tenant.objects.create_with_key(name="Orphan Co", raw_key=raw_key)
    make_membership(tenant, "admin", sub=SUB)
    make_membership(tenant, "member")

    assert deliver(event_body()).status_code == 200

    tenant.refresh_from_db()
    assert tenant.is_active is True
    assert TenantConfig.objects.filter(tenant=tenant).exists()
    assert not Membership.objects.filter(tenant=tenant, role="admin").exists()
    # 복구: TENANT_KEY(break-glass)로 Admin 초대 링크 발급.
    r = Client().post(
        "/api/tenant/invites/", {"role": "admin"}, content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert r.status_code in (200, 201), r.content
    assert r.json()["role"] == "admin"


# ── W5 모르는 type ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_unknown_type_is_ignored_with_2xx() -> None:
    from apps.tenants.models import User

    make_membership(new_tenant(), "member", sub=SUB)

    assert deliver(event_body(type_="user.renamed")).status_code == 200
    assert User.objects.filter(sub=SUB).exists()


# ── W4 중복 전송 ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_redelivery_of_same_id_is_2xx_and_has_one_side_effect() -> None:
    from apps.tenants.models import User

    tenant = new_tenant()
    make_membership(tenant, "member", sub=SUB)
    body = event_body()

    assert deliver(body).status_code == 200
    # 같은 sub가 (가령 새 로그인으로) 다시 생겨도, 같은 이벤트 id의 재전송은 아무것도 하지 않는다.
    make_membership(tenant, "member", sub=SUB)
    assert deliver(body, sign(body, int(time.time()) + 1)).status_code == 200
    assert User.objects.filter(sub=SUB).exists()


@pytest.mark.django_db
def test_processed_ids_are_kept_at_least_three_days_then_pruned() -> None:
    from datetime import timedelta

    from django.utils import timezone

    from apps.tenants.models import AuthWebhookEvent

    assert deliver(event_body("01OLD")).status_code == 200
    AuthWebhookEvent.objects.filter(event_id="01OLD").update(received_at=timezone.now() - timedelta(days=3, hours=23))
    assert deliver(event_body("01MID")).status_code == 200
    assert AuthWebhookEvent.objects.filter(event_id="01OLD").exists()  # 3일은 넘겼어도 보관 기간(7일) 안

    AuthWebhookEvent.objects.filter(event_id="01OLD").update(received_at=timezone.now() - timedelta(days=8))
    assert deliver(event_body("01NEW")).status_code == 200
    assert set(AuthWebhookEvent.objects.values_list("event_id", flat=True)) == {"01MID", "01NEW"}


# ── W1~W3 거부 ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=00", "v1=00", "t=1700000000"])
def test_malformed_signature_header_is_400(header: str) -> None:
    assert deliver(event_body(), header).status_code == 400


@pytest.mark.django_db
def test_missing_signature_header_is_400() -> None:
    r = Client().post(URL, data=event_body(), content_type="application/json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_stale_timestamp_is_400() -> None:
    body = event_body()
    assert deliver(body, sign(body, int(time.time()) - 400)).status_code == 400
    assert deliver(body, sign(body, int(time.time()) + 400)).status_code == 400


def test_tolerance_boundary_is_300_seconds_with_fixed_clock() -> None:
    from apps.tenants.withdrawal import WebhookRejected, verify_sandcorp_webhook

    now = 1_800_000_000.0
    body = event_body()
    for t in (int(now) - 300, int(now) + 300):
        assert verify_sandcorp_webhook(SECRET, body, sign(body, t), now=now)["sub"] == SUB
    for t in (int(now) - 301, int(now) + 301):
        with pytest.raises(WebhookRejected) as exc:
            verify_sandcorp_webhook(SECRET, body, sign(body, t), now=now)
        assert exc.value.status == 400


@pytest.mark.django_db
def test_tampered_signature_is_401() -> None:
    from apps.tenants.models import User

    make_membership(new_tenant(), "member", sub=SUB)
    body = event_body()
    good = sign(body)
    bad = good[:-1] + ("0" if good[-1] != "0" else "1")

    assert deliver(body, bad).status_code == 401
    assert deliver(body, sign(body, secret="wrong-secret")).status_code == 401
    assert User.objects.filter(sub=SUB).exists()


@pytest.mark.django_db
def test_body_changed_by_one_byte_is_401() -> None:
    """서명은 받은 바이트 그대로 검증한다 — 같은 JSON이라도 다시 직렬화하면 맞지 않는다."""
    from apps.tenants.models import User

    make_membership(new_tenant(), "member", sub=SUB)
    body = event_body()
    signature = sign(body)
    changed = body.replace(b'"type":', b'"type" :')  # 의미는 같고 바이트만 다르다

    assert deliver(changed, signature).status_code == 401
    assert User.objects.filter(sub=SUB).exists()


@pytest.mark.django_db
def test_empty_secret_rejects_every_request(settings) -> None:
    from apps.tenants.models import User

    make_membership(new_tenant(), "member", sub=SUB)
    body = event_body()
    signature = sign(body, secret="")
    settings.AUTH_WEBHOOK_SECRET = ""

    assert deliver(body, signature).status_code == 503
    assert User.objects.filter(sub=SUB).exists()


@pytest.mark.django_db
def test_signed_but_not_an_event_is_400() -> None:
    for body in (b"not json", b'{"type":"user.deleted"}', b"[]"):
        assert deliver(body).status_code == 400


# ── #66 Operator 목록의 「주인 없음」 ───────────────────────────────────────


@pytest.mark.django_db
def test_operator_list_shows_ownerless_after_last_admin_withdraws_and_clears_on_invite_accept(operator_token) -> None:
    from apps.tenants.members import accept_invite
    from apps.tenants.models import User

    tenant = new_tenant("Ownerless Co")
    other = new_tenant("Owned Co")
    make_membership(tenant, "admin", sub=SUB)
    make_membership(tenant, "member")
    make_membership(other, "admin")
    auth = {"HTTP_AUTHORIZATION": f"Bearer {operator_token}"}

    def counts() -> dict[str, int]:
        rows = Client().get("/api/operator/tenants/", **auth).json()
        return {r["name"]: r["active_admin_count"] for r in rows}

    assert counts()["Ownerless Co"] == 1
    assert deliver(event_body()).status_code == 200
    assert counts() == {"Ownerless Co": 0, "Owned Co": 1}

    r = Client().post(f"/api/operator/tenants/{tenant.id}/admin-invite", **auth)
    assert r.status_code == 201, r.content
    from urllib.parse import parse_qs, urlparse

    raw = parse_qs(urlparse(r.json()["url"]).query)["invite"][0]
    newcomer = User.objects.create(iss="https://auth.sandcorp.co.kr", sub="m_NEWADMIN0000000000000000", acct=User.Acct.MEMBER)
    accept_invite(newcomer, raw)
    assert counts()["Ownerless Co"] == 1


@pytest.mark.django_db
def test_operator_admin_invite_needs_operator_and_active_tenant(operator_token) -> None:
    tenant = new_tenant("Paused Co")
    assert Client().post(f"/api/operator/tenants/{tenant.id}/admin-invite").status_code == 401
    tenant.is_active = False
    tenant.save()
    r = Client().post(
        f"/api/operator/tenants/{tenant.id}/admin-invite", HTTP_AUTHORIZATION=f"Bearer {operator_token}"
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_operator_list_counts_with_constant_queries(operator_token, django_assert_max_num_queries) -> None:
    for i in range(5):
        make_membership(new_tenant(f"T{i}"), "admin")
    with django_assert_max_num_queries(4):
        Client().get("/api/operator/tenants/", HTTP_AUTHORIZATION=f"Bearer {operator_token}")
