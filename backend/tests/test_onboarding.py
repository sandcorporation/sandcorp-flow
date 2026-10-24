"""온보딩(#41): 멤버십 없는 member가 조직을 만들고 첫 Admin으로 바로 테넌트 세션을 연다.

콜백이 심는 `member_ticket` 쿠키를 그대로 쓴다(`issue_member_ticket`) — 실제 DB·Redis로 돈다.
"""
import pytest
from django.test import Client

from apps.tenants.members import issue_member_ticket

ISS = "https://auth.sandcorp.co.kr"


@pytest.fixture(autouse=True)
def clear_onboarding_rate_limit():
    from apps.chat.sse import get_redis_client

    r = get_redis_client()
    for key in r.scan_iter("ratelimit:onboarding:*"):
        r.delete(key)
    yield


def _member(sub="m_ONBOARD"):
    from apps.tenants.members import member_user

    return member_user(ISS, sub)


def _client_for(user) -> Client:
    c = Client()
    c.cookies["member_ticket"] = issue_member_ticket(user)
    return c


def _create(c: Client, name="새 조직", slug="new-org", **extra):
    return c.post("/api/auth/member/tenants", {"name": name, "slug": slug}, content_type="application/json", **extra)


@pytest.mark.django_db
def test_member_without_membership_creates_tenant_and_becomes_admin_with_session():
    from django.conf import settings
    from apps.tenants.models import Membership, Tenant

    user = _member()
    c = _client_for(user)
    r = _create(c, name="  모래상회 ", slug="morae-shop")
    assert r.status_code == 201
    body = r.json()
    tenant = Tenant.objects.get(id=body["tenant_id"])
    assert (tenant.name, tenant.slug, tenant.created_by_id) == ("모래상회", "morae-shop", user.id)
    assert tenant.trial_turns_remaining == settings.TRIAL_TURNS
    assert Membership.objects.get(user=user, tenant=tenant).role == "admin"
    assert body["tenant_name"] == "모래상회"

    # 받은 Access로 바로 테넌트 화면을 쓰고, Refresh 쿠키로 새로고침해도 세션이 이어진다.
    me = c.get("/api/tenant/members/me", HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
    assert me.status_code == 200 and me.json()["role"] == "admin"
    refreshed = c.post("/api/tenant/members/auth/refresh")
    assert refreshed.status_code == 200
    # 표는 썼으니 지운다 — 같은 표로 또 만들 수 없다.
    assert c.cookies["member_ticket"].value == ""


@pytest.mark.django_db
def test_new_tenant_has_existing_chat_config():
    from apps.tenants.models import TenantConfig

    r = _create(_client_for(_member()))
    assert TenantConfig.objects.filter(tenant_id=r.json()["tenant_id"]).exists()


@pytest.mark.django_db
def test_without_ticket_is_rejected():
    r = _create(Client())
    assert r.status_code == 401


@pytest.mark.django_db
def test_forged_ticket_is_rejected():
    c = Client()
    c.cookies["member_ticket"] = "forged"
    assert _create(c).status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["기존 조직", "기존 조직".upper(), " 기존 조직 "])
def test_duplicate_name_is_rejected_case_insensitively(name):
    import secrets
    from apps.tenants.models import Tenant

    Tenant.objects.create_with_key(name="기존 조직", raw_key=secrets.token_urlsafe(16))
    r = _create(_client_for(_member()), name=name, slug="fresh")
    assert r.status_code == 409 and r.json()["reason"] == "name_taken"


@pytest.mark.django_db
def test_duplicate_slug_is_rejected_case_insensitively():
    import secrets
    from apps.tenants.models import Tenant

    t = Tenant.objects.create_with_key(name="다른 조직", raw_key=secrets.token_urlsafe(16))
    t.slug = "Taken"
    t.save(update_fields=["slug"])
    r = _create(_client_for(_member()), name="내 조직", slug="taken")
    assert r.status_code == 409 and r.json()["reason"] == "slug_taken"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,slug,reason",
    [
        ("", "ok-slug", "name_invalid"),
        ("   ", "ok-slug", "name_invalid"),
        ("가" * 256, "ok-slug", "name_invalid"),
        ("이름", "", "slug_invalid"),
        ("이름", "-bad", "slug_invalid"),
        ("이름", "a b", "slug_invalid"),
        ("이름", "admin", "slug_invalid"),
    ],
)
def test_invalid_name_or_slug_is_rejected(name, slug, reason):
    from apps.tenants.models import Tenant

    r = _create(_client_for(_member()), name=name, slug=slug)
    assert r.status_code == 400 and r.json()["reason"] == reason
    assert not Tenant.objects.exists()


@pytest.mark.django_db
def test_per_user_creation_limit(settings):
    settings.TENANT_CREATE_LIMIT_PER_USER = 2
    user = _member()
    assert _create(_client_for(user), name="하나", slug="one").status_code == 201
    assert _create(_client_for(user), name="둘", slug="two").status_code == 201
    r = _create(_client_for(user), name="셋", slug="three")
    assert r.status_code == 409 and r.json()["reason"] == "limit_reached"
    # 다른 사람은 상한과 무관하다.
    assert _create(_client_for(_member("m_OTHER")), name="셋", slug="three").status_code == 201


@pytest.mark.django_db
def test_ip_rate_limit_counts_attempts_per_ip(settings):
    settings.ONBOARDING_RATE_LIMIT_PER_HOUR = 2
    settings.TRUSTED_PROXY_COUNT = 2  # 운영: NPM → nginx
    c = _client_for(_member())
    # 클라이언트가 앞에 위조 값을 넣어도, NPM이 덧붙인 값(끝에서 두 번째)으로 센다.
    ip = {"HTTP_X_FORWARDED_FOR": "1.2.3.4, 203.0.113.9, 172.17.0.1"}
    assert _create(c, name="", slug="x", **ip).status_code == 400  # 실패한 시도도 센다
    assert _create(c, name="", slug="x", HTTP_X_FORWARDED_FOR="9.9.9.9, 203.0.113.9, 172.17.0.1").status_code == 400
    assert _create(c, name="이름", slug="ok", **ip).status_code == 429
    # 다른 클라이언트 IP는 따로 센다.
    assert _create(c, name="이름", slug="ok", HTTP_X_FORWARDED_FOR="203.0.113.10, 172.17.0.1").status_code == 201
