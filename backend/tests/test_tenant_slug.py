import pytest


# ── Issue 84: Tenant Slug — 공개 URL용 고유·URL-safe 식별자 ────────────────────

@pytest.mark.django_db
def test_tenant_sets_slug_via_api(client, tenant_with_key, tenant_admin_token):
    """Tenant가 어드민에서 유효한 slug를 설정하면 저장된다 (표시명과 독립)."""
    tenant, _ = tenant_with_key

    resp = client.patch(
        "/api/tenant/slug/",
        {"slug": "abc-shop"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200

    tenant.refresh_from_db()
    assert tenant.slug == "abc-shop"
    assert tenant.name == "Test Corp"  # 표시명은 그대로


@pytest.mark.django_db
def test_invalid_slug_format_rejected(client, tenant_with_key, tenant_admin_token):
    """형식 위반(공백·특수문자·선후행/연속 하이픈·자모·한자·이모지)은 거부되고 저장되지 않는다.

    (대문자는 이제 허용 — issue 187. 한글 관련 거부는 아래 한글 슬러그 테스트가 함께 검증.)"""
    tenant, _ = tenant_with_key

    for bad in ["ab c", "a@b", "-abc", "abc-", "a--b", "", "ㄱㄴ", "商店", "가게😀", "가@게"]:
        resp = client.patch(
            "/api/tenant/slug/",
            {"slug": bad},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )
        assert resp.status_code == 400, f"{bad!r} 는 거부되어야 한다 (got {resp.status_code})"

    tenant.refresh_from_db()
    assert tenant.slug is None  # 아무것도 저장되지 않음


@pytest.mark.django_db
def test_reserved_slug_rejected(client, tenant_with_key, tenant_admin_token):
    """예약어(admin·api·chatbot 등)는 라우트 충돌 방지를 위해 거부된다."""
    tenant, _ = tenant_with_key

    for reserved in ["admin", "api", "chatbot"]:
        resp = client.patch(
            "/api/tenant/slug/",
            {"slug": reserved},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )
        assert resp.status_code == 400, f"{reserved!r} 예약어는 거부되어야 한다"

    tenant.refresh_from_db()
    assert tenant.slug is None


def _second_tenant_token():
    """별도 Tenant + 그 Admin 멤버 토큰을 만든다 (전역 unique 테스트용)."""
    import secrets
    from apps.tenants.models import Tenant
    from tests.utils import make_membership, member_token

    t2 = Tenant.objects.create_with_key(name="Other Co", raw_key=secrets.token_urlsafe(32))
    agent = make_membership(t2, "admin")  # slug 변경은 Admin
    return t2, member_token(agent)


@pytest.mark.django_db
def test_duplicate_slug_rejected_globally(client, tenant_with_key, tenant_admin_token):
    """이미 다른 Tenant가 쓰는 slug는 전역 고유성으로 거부된다."""
    tenant1, _ = tenant_with_key
    resp1 = client.patch(
        "/api/tenant/slug/",
        {"slug": "shared-name"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp1.status_code == 200

    tenant2, token2 = _second_tenant_token()
    resp2 = client.patch(
        "/api/tenant/slug/",
        {"slug": "shared-name"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token2}",
    )
    assert resp2.status_code == 400, "중복 slug는 거부되어야 한다"

    tenant2.refresh_from_db()
    assert tenant2.slug is None


@pytest.mark.django_db
def test_slug_resolves_to_active_tenant(client, tenant_with_key, tenant_admin_token):
    """slug로 활성 Tenant를 조회한다. 미존재·정지된 Tenant는 None (85 연결 경로가 사용)."""
    from apps.tenants.models import Tenant

    tenant, _ = tenant_with_key
    client.patch(
        "/api/tenant/slug/",
        {"slug": "abc-shop"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    resolved = Tenant.resolve_slug("abc-shop")
    assert resolved is not None and resolved.id == tenant.id

    assert Tenant.resolve_slug("nonexistent") is None

    tenant.is_active = False
    tenant.save(update_fields=["is_active"])
    assert Tenant.resolve_slug("abc-shop") is None, "정지된 Tenant는 해석되지 않아야 한다"


# ── Issue 187: 한글 Tenant Slug (완성형 한글 + 라틴 + 숫자 + 하이픈) ──────────────

def test_korean_and_mixed_slugs_valid():
    """완성형 한글·영한혼합·대문자 슬러그는 유효하다(순수 함수)."""
    from apps.tenants.slug import is_valid_slug, normalize_slug
    for good in ["우리가게", "서울cafe", "store-강남", "MyStore", "abc-shop", "강남점2"]:
        assert is_valid_slug(normalize_slug(good)), f"{good!r} should be valid"


def test_non_hangul_letters_rejected():
    """자모 단독·한자·이모지·특수문자·하이픈 규칙 위반은 거부된다(순수 함수)."""
    from apps.tenants.slug import is_valid_slug, normalize_slug
    for bad in ["ㄱㄴ", "商店", "가게😀", "가@게", "가 게", "-가", "가-", "가--게", ""]:
        assert not is_valid_slug(normalize_slug(bad)), f"{bad!r} should be invalid"


def test_normalize_slug_nfc_and_trim():
    """normalize_slug는 NFC 정규화 + 앞뒤 공백 제거(대소문자 보존)."""
    import unicodedata
    from apps.tenants.slug import normalize_slug
    nfd = unicodedata.normalize("NFD", "강남")   # 자모 분리형
    assert nfd != "강남"                          # precondition: 바이트가 다름
    assert normalize_slug(f"  {nfd}  ") == "강남"  # trim + NFC 완성형
    assert normalize_slug("MyStore") == "MyStore"  # 대소문자 보존


def test_slug_key_is_case_insensitive():
    """slug_key는 대소문자 무시 비교 키(라틴만 접힘, 한글 무영향)."""
    from apps.tenants.slug import slug_key
    assert slug_key("MyStore") == slug_key("mystore")
    assert slug_key("우리가게") == slug_key("우리가게")


def test_reserved_uppercase_bypass_rejected():
    """예약어를 대문자로 우회해도 거부된다(slug_key 비교)."""
    from apps.tenants.slug import is_valid_slug
    for r in ["Admin", "API", "ChatBot"]:
        assert not is_valid_slug(r), f"{r!r} (예약어 대문자 우회) should be invalid"


def test_slug_too_long_rejected():
    """63자 초과는 거부된다(DB max_length)."""
    from apps.tenants.slug import is_valid_slug
    assert is_valid_slug("가" * 63)
    assert not is_valid_slug("가" * 64)


@pytest.mark.django_db
def test_korean_slug_via_api(client, tenant_with_key, tenant_admin_token):
    """한글 슬러그를 등록하고 그 한글로 조회된다."""
    from apps.tenants.models import Tenant
    tenant, _ = tenant_with_key
    resp = client.patch(
        "/api/tenant/slug/", {"slug": "우리가게"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    tenant.refresh_from_db()
    assert tenant.slug == "우리가게"
    assert Tenant.resolve_slug("우리가게").id == tenant.id


@pytest.mark.django_db
def test_slug_resolve_and_duplicate_are_case_insensitive(client, tenant_with_key, tenant_admin_token):
    """원형은 보존하되 조회·중복은 대소문자 무시다."""
    from apps.tenants.models import Tenant
    tenant, _ = tenant_with_key
    resp = client.patch(
        "/api/tenant/slug/", {"slug": "MyStore"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    tenant.refresh_from_db()
    assert tenant.slug == "MyStore"                       # 원형 보존
    assert Tenant.resolve_slug("mystore").id == tenant.id  # 대소문자 무시 조회
    assert Tenant.resolve_slug("MYSTORE").id == tenant.id

    # 다른 Tenant가 대소문자만 다른 slug를 못 가져간다
    _t2, token2 = _second_tenant_token()
    dup = client.patch(
        "/api/tenant/slug/", {"slug": "mystore"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token2}",
    )
    assert dup.status_code == 400


@pytest.mark.django_db
def test_nfd_slug_saved_as_nfc(client, tenant_with_key, tenant_admin_token):
    """자모 분리(NFD) 입력은 완성형(NFC)으로 저장된다."""
    import unicodedata
    from apps.tenants.models import Tenant
    tenant, _ = tenant_with_key
    nfd = unicodedata.normalize("NFD", "강남점")
    resp = client.patch(
        "/api/tenant/slug/", {"slug": nfd},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    tenant.refresh_from_db()
    assert tenant.slug == unicodedata.normalize("NFC", "강남점")
    assert Tenant.resolve_slug("강남점").id == tenant.id


@pytest.mark.django_db
def test_get_config_includes_slug(client, tenant_with_key, tenant_admin_token):
    """config 조회 응답에 현재 slug가 포함된다(새로고침 시 입력 복원용)."""
    tenant, _ = tenant_with_key
    client.patch("/api/tenant/slug/", {"slug": "우리가게"}, content_type="application/json",
                 HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    resp = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "우리가게"


@pytest.mark.django_db
def test_get_config_slug_empty_when_unset(client, tenant_with_key, tenant_admin_token):
    """slug 미설정이면 빈 문자열(None 아님)."""
    resp = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == ""
