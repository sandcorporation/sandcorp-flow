"""허용 사이트(임베드 출처) 목록과 동적 frame-ancestors(#248, ADR-0034 E3).

- 검증은 ``normalize_embed_origins`` 한 함수다(형식·와일드카드·http는 localhost만·최대 20개·헤더 주입 거부).
- 정책은 ``frame_ancestors(slug, key)``다. 목록이 비면 ``*``, 아니면 ``'self'`` + 목록. 흐름 목록이 테넌트 목록을 대신하고,
  기본 흐름 주소는 테넌트 목록만 본다. 없는 slug·키는 ``'self'``.
- nginx ``auth_request``가 부르는 내부 엔드포인트 ``/_internal/embed-policy``는 ``X-Original-URI``로 판정해 204로 답한다.
DB·Redis·인증은 실제 객체다.
"""

from __future__ import annotations

import pytest

from apps.flows import embed_policy
from apps.flows.embed_policy import EmbedOriginsInvalid, frame_ancestors, normalize_embed_origins
from apps.flows.models import Flow
from apps.tenants.models import TenantConfig
from tests.test_flow_api import BASE, h
from tests.test_flow_embed_api import deployed_flow

CONFIG = "/api/tenant/config/"


def jput(client, url, body, token):
    return client.put(url, data=body, content_type="application/json", **h(token))


def jpatch(client, url, body, token):
    return client.patch(url, data=body, content_type="application/json", **h(token))


# ── 검증 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given, expected",
    [
        (["https://example.com"], ["https://example.com"]),
        (["HTTPS://Example.COM"], ["https://example.com"]),  # 소문자로 맞춘다
        (["https://example.com/"], ["https://example.com"]),  # 끝 빗금 하나는 지운다(경로 아님)
        (["https://a.com", "https://A.com", "https://b.com"], ["https://a.com", "https://b.com"]),  # 중복 제거·순서 유지
        (["  https://a.com  ", ""], ["https://a.com"]),  # 앞뒤 공백·빈 줄은 무시
        (["https://*.example.com"], ["https://*.example.com"]),  # 하위 도메인 와일드카드
        (["https://shop.example.com:8443"], ["https://shop.example.com:8443"]),
        (["http://localhost:5173", "http://127.0.0.1:*", "http://localhost"], ["http://localhost:5173", "http://127.0.0.1:*", "http://localhost"]),
        (["https://xn--3e0b707e.kr"], ["https://xn--3e0b707e.kr"]),
        ([], []),
    ],
)
def test_valid_origins_are_normalized(given, expected):
    assert normalize_embed_origins(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "example.com",  # scheme 없음
        "ftp://example.com",  # http(s)만
        "http://example.com",  # http는 localhost·127.0.0.1만
        "https://example.com/path",  # 경로
        "https://example.com?x=1",  # 쿼리
        "https://example.com#top",
        "*",  # * 단독
        "https://*",
        "https://*.com",  # 와일드카드 뒤에 최소 두 조각
        "https://a.*.example.com",  # 와일드카드는 맨 앞만
        "https://example.com:*",  # 포트 *는 로컬(http localhost)만
        "https://example.com:0",
        "https://example.com:70000",
        "https://user@example.com",
        "https:",
        "https://",
        "'none'",  # 키워드
        "'self'",
        "data:",
        "https://-bad.com",
    ],
)
def test_malformed_origin_is_rejected_with_the_item_in_the_message(bad):
    with pytest.raises(EmbedOriginsInvalid) as e:
        normalize_embed_origins([bad])
    assert bad.strip() in e.value.message


@pytest.mark.parametrize(
    "injection",
    [
        "https://a.com https://evil.com",  # 공백 — 출처 하나 더 끼우기
        "https://a.com\thttps://evil.com",
        "https://a.com\r\nX-Evil: 1",  # 줄바꿈 — 헤더 끼우기
        "https://a.com; script-src *",  # 세미콜론 — 지시문 끼우기
        "https://a.com'",  # 따옴표 — 키워드 끼우기
        'https://a.com"',
        "https://a.com,https://evil.com",
    ],
)
def test_header_injection_is_rejected(injection):
    with pytest.raises(EmbedOriginsInvalid):
        normalize_embed_origins([injection])


def test_at_most_twenty_origins():
    many = [f"https://s{i}.example.com" for i in range(21)]
    assert len(normalize_embed_origins(many[:20])) == 20
    with pytest.raises(EmbedOriginsInvalid) as e:
        normalize_embed_origins(many)
    assert "20" in e.value.message


def test_non_string_items_are_rejected():
    with pytest.raises(EmbedOriginsInvalid):
        normalize_embed_origins([123])  # type: ignore[list-item]


# ── 정책 결정 ────────────────────────────────────────────────────────────────


def _setup(tenant, slug="fa-corp", tenant_origins=(), flow_origins=()):
    tenant.slug = slug
    tenant.save(update_fields=["slug"])
    TenantConfig.objects.update_or_create(tenant=tenant, defaults={"embed_origins": list(tenant_origins)})
    flow = deployed_flow(tenant)
    Flow.objects.filter(pk=flow.pk).update(embed_enabled=True, embed_origins=list(flow_origins))
    embed_policy.forget_tenant(tenant.id)
    return Flow.objects.get(pk=flow.pk)


@pytest.mark.django_db
def test_empty_lists_allow_anywhere(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = _setup(tenant)
    assert frame_ancestors("fa-corp", "") == "*"
    assert frame_ancestors("fa-corp", flow.embed_key) == "*"


@pytest.mark.django_db
def test_tenant_list_applies_to_default_and_key_addresses(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = _setup(tenant, tenant_origins=["https://shop.example.com", "https://*.example.org"])
    expected = "'self' https://shop.example.com https://*.example.org"
    assert frame_ancestors("fa-corp", "") == expected
    assert frame_ancestors("fa-corp", flow.embed_key) == expected


@pytest.mark.django_db
def test_flow_list_replaces_tenant_list_on_its_key_address(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = _setup(tenant, tenant_origins=["https://shop.example.com"], flow_origins=["https://campaign.example.net"])
    assert frame_ancestors("fa-corp", flow.embed_key) == "'self' https://campaign.example.net"  # 합치지 않는다


@pytest.mark.django_db
def test_flow_list_alone_restricts_even_when_tenant_list_is_empty(tenant_with_key):
    tenant, _ = tenant_with_key
    flow = _setup(tenant, flow_origins=["https://campaign.example.net"])
    assert frame_ancestors("fa-corp", flow.embed_key) == "'self' https://campaign.example.net"
    assert frame_ancestors("fa-corp", "") == "*"


@pytest.mark.django_db
def test_default_address_sees_only_tenant_list(tenant_with_key):
    """기본 흐름 주소는 기본 흐름 자신의 목록도 보지 않는다 — 기본 흐름을 바꿔도 붙일 수 있는 사이트가 그대로다."""
    tenant, _ = tenant_with_key
    flow = _setup(tenant, tenant_origins=["https://shop.example.com"], flow_origins=["https://campaign.example.net"])
    Flow.objects.filter(pk=flow.pk).update(is_public=True)
    embed_policy.forget_tenant(tenant.id)
    assert frame_ancestors("fa-corp", "") == "'self' https://shop.example.com"
    assert frame_ancestors("fa-corp", flow.embed_key) == "'self' https://campaign.example.net"


@pytest.mark.django_db
def test_unknown_slug_or_key_is_self_only(tenant_with_key):
    tenant, _ = tenant_with_key
    _setup(tenant)
    assert frame_ancestors("no-such-tenant", "") == "'self'"
    assert frame_ancestors("fa-corp", "nokey123") == "'self'"


@pytest.mark.django_db
def test_slug_is_matched_like_the_widget_resolves_it(tenant_with_key):
    tenant, _ = tenant_with_key
    _setup(tenant, tenant_origins=["https://shop.example.com"])
    assert frame_ancestors("FA-Corp", "") == "'self' https://shop.example.com"


# ── 캐시와 무효화 ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_policy_is_cached_and_saving_tenant_list_clears_it(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    _setup(tenant)
    assert frame_ancestors("fa-corp", "") == "*"
    # 저장 경로를 거치지 않고 바꾸면 캐시가 그대로 답한다(캐시가 실제로 있다).
    TenantConfig.objects.filter(tenant=tenant).update(embed_origins=["https://direct.example.com"])
    assert frame_ancestors("fa-corp", "") == "*"
    # 설정 저장(API)은 캐시를 지운다.
    r = jpatch(client, CONFIG, {"embed_origins": ["https://shop.example.com"]}, tenant_admin_token)
    assert r.status_code == 200, r.content
    assert frame_ancestors("fa-corp", "") == "'self' https://shop.example.com"


@pytest.mark.django_db
def test_saving_flow_list_clears_cache(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = _setup(tenant)
    assert frame_ancestors("fa-corp", flow.embed_key) == "*"
    r = jput(client, f"{BASE}{flow.id}/embed-origins/", {"origins": ["https://campaign.example.net"]}, tenant_admin_token)
    assert r.status_code == 200, r.content
    assert frame_ancestors("fa-corp", flow.embed_key) == "'self' https://campaign.example.net"


@pytest.mark.django_db
def test_policy_still_answers_when_redis_is_down(tenant_with_key, settings):
    tenant, _ = tenant_with_key
    _setup(tenant, tenant_origins=["https://shop.example.com"])
    settings.REDIS_URL = "redis://127.0.0.1:1/0"  # 닿지 않는 Redis
    assert frame_ancestors("fa-corp", "") == "'self' https://shop.example.com"


# ── 내부 정책 엔드포인트(nginx auth_request) ─────────────────────────────────


@pytest.mark.django_db
def test_internal_endpoint_answers_204_with_header_from_original_uri(client, tenant_with_key):
    tenant, _ = tenant_with_key
    flow = _setup(tenant, tenant_origins=["https://shop.example.com"], flow_origins=["https://campaign.example.net"])
    cases = {
        "/chatbot/fa-corp/": "'self' https://shop.example.com",
        "/chatbot/fa-corp/?utm=x": "'self' https://shop.example.com",
        "/chatbot/fa-corp": "'self' https://shop.example.com",
        f"/chatbot/fa-corp/{flow.embed_key}/": "'self' https://campaign.example.net",
        f"/chatbot/fa-corp/{flow.embed_key}": "'self' https://campaign.example.net",
        "/chatbot/nobody/": "'self'",
        "/chatbot/": "'self'",
        "/something/else": "'self'",
    }
    for uri, value in cases.items():
        r = client.get("/_internal/embed-policy", HTTP_X_ORIGINAL_URI=uri)
        assert r.status_code == 204, uri
        assert r["X-Frame-Ancestors"] == value, uri
    assert client.get("/_internal/embed-policy")["X-Frame-Ancestors"] == "'self'"  # 머리 없음


@pytest.mark.django_db
def test_internal_endpoint_decodes_percent_encoded_slug(client, tenant_with_key):
    tenant, _ = tenant_with_key
    _setup(tenant, slug="한글상점", tenant_origins=["https://shop.example.com"])
    r = client.get("/_internal/embed-policy", HTTP_X_ORIGINAL_URI="/chatbot/%ED%95%9C%EA%B8%80%EC%83%81%EC%A0%90/")
    assert r["X-Frame-Ancestors"] == "'self' https://shop.example.com"


def test_internal_endpoint_is_not_under_public_api_prefix(client):
    """nginx는 /api/·/mcp·/.well-known 만 앱으로 넘긴다 — 정책 엔드포인트는 그 밖이라 바깥에서 닿지 않는다(nginx 시험이 확인)."""
    assert client.get("/api/_internal/embed-policy").status_code == 404


# ── 편집 API ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_admin_edits_tenant_list_and_reads_it_back_normalized(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    _setup(tenant)
    r = jpatch(client, CONFIG, {"embed_origins": ["HTTPS://Shop.example.com/", "https://shop.example.com"]}, tenant_admin_token)
    assert r.status_code == 200, r.content
    assert r.json()["embed_origins"] == ["https://shop.example.com"]
    assert client.get(CONFIG, **h(tenant_admin_token)).json()["embed_origins"] == ["https://shop.example.com"]


@pytest.mark.django_db
def test_invalid_tenant_list_is_400_with_server_message(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    _setup(tenant)
    r = jpatch(client, CONFIG, {"embed_origins": ["https://a.com; script-src *"]}, tenant_admin_token)
    assert r.status_code == 400
    assert "https://a.com; script-src *" in r.json()["detail"]
    assert TenantConfig.objects.get(tenant=tenant).embed_origins == []


@pytest.mark.django_db
def test_member_cannot_change_lists_but_round_trip_passes(client, tenant_with_key, tenant_admin_token, tenant_member_token):
    tenant, _ = tenant_with_key
    flow = _setup(tenant, tenant_origins=["https://shop.example.com"])
    r = jpatch(client, CONFIG, {"embed_origins": ["https://other.example.com"]}, tenant_member_token)
    assert r.status_code == 403
    # 화면은 설정 전체를 되돌려 보낸다 — 같은 값은 통과.
    assert jpatch(client, CONFIG, {"embed_origins": ["https://shop.example.com"]}, tenant_member_token).status_code == 200
    r = jput(client, f"{BASE}{flow.id}/embed-origins/", {"origins": ["https://x.example.com"]}, tenant_member_token)
    assert r.status_code == 403


@pytest.mark.django_db
def test_admin_edits_flow_list_and_flow_out_carries_it(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = _setup(tenant)
    url = f"{BASE}{flow.id}/embed-origins/"
    r = jput(client, url, {"origins": ["https://Campaign.example.net"]}, tenant_admin_token)
    assert r.status_code == 200, r.content
    assert r.json()["embed_origins"] == ["https://campaign.example.net"]
    detail = client.get(f"{BASE}{flow.id}/", **h(tenant_admin_token)).json()["flow"]
    assert detail["embed_origins"] == ["https://campaign.example.net"]
    # 비우면 테넌트 목록을 따른다.
    assert jput(client, url, {"origins": []}, tenant_admin_token).json()["embed_origins"] == []


@pytest.mark.django_db
def test_invalid_flow_list_is_400_with_code_and_message(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = _setup(tenant)
    r = jput(client, f"{BASE}{flow.id}/embed-origins/", {"origins": ["http://example.com"]}, tenant_admin_token)
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_embed_origins"
    assert "http://example.com" in r.json()["detail"]
    assert Flow.objects.get(pk=flow.pk).embed_origins == []


@pytest.mark.django_db
def test_flow_list_of_foreign_or_archived_flow_is_404(client, tenant_with_key, tenant_admin_token):
    tenant, _ = tenant_with_key
    flow = _setup(tenant)
    from django.utils import timezone

    Flow.objects.filter(pk=flow.pk).update(archived_at=timezone.now(), embed_enabled=False)
    r = jput(client, f"{BASE}{flow.id}/embed-origins/", {"origins": []}, tenant_admin_token)
    assert r.status_code == 404
