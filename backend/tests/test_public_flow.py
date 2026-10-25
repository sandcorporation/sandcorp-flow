"""공개 흐름 보기(#39) — 익명 읽기 전용 흐름 API와 「공개 흐름 보기」 설정.

익명 표면에 노드 설정을 내보내므로 누출 방어가 핵심이다. 공개 응답은 노드 종류별 허용 목록으로만 직렬화한다.
시험은 비밀·헤더 값·내부 URL·허용 도메인·Provider 값을 심은 테넌트에서 응답 **문자열 전체**에 그 값들이 없음을 단언한다.
"""
from __future__ import annotations

import json

import pytest

URL = "/api/public/flows/{slug}/"

SECRET_VALUE = "sk-live-planted-secret-value-9931"
PROVIDER_KEY = "sk-provider-planted-key-4410"
INTERNAL_HOST = "internal-api.leakcorp.example"
LEAKS = [
    SECRET_VALUE,
    PROVIDER_KEY,
    "secret.api_token",  # 비밀 참조 문자열 자체도 남지 않는다
    INTERNAL_HOST,
    "X-Internal-Trace",  # 헤더 이름
    "trace-hdr-value-77",  # 헤더 값
    "body-marker-55",  # 바디
    "llm.leakcorp.example",  # Provider base_url
    "hooks.leakcorp.example",  # 웹훅
    "system-prompt-marker-3",  # 테넌트 시스템 프롬프트
    "경쟁사금지어",  # 가드레일 금지어
    "Leak Corp 내부명",  # Tenant.name(표시명은 brand_name)
    "draft-only-marker",  # 초안
    "$.status",  # HTTP 응답 저장 경로
]


def _shop_graph_with_plants() -> dict:
    """예제 ②(쇼핑몰)에 비밀·헤더·바디·내부 URL을 심은 그래프."""
    from apps.flows.example_catalog import SHOP_SUPPORT, example_graph

    graph = example_graph(SHOP_SUPPORT)
    for node in graph["nodes"]:
        if node["id"] == "order":
            node["config"] = {
                "method": "POST",
                "url": f"https://{INTERNAL_HOST}/v1/orders?debug=1",
                "headers": {"Authorization": "Bearer {{secret.api_token}}", "X-Internal-Trace": "trace-hdr-value-77"},
                "body_template": '{"note": "body-marker-55"}',
                "save": {"found": "$.found", "order_status": "$.status"},
            }
        if node["id"] == "say_missing":
            node["config"] = {"instructions": "토큰 {{secret.api_token}}을 말하지 마세요"}
    graph["nodes"].append({
        "id": "guard", "kind": "guardrail", "code": "N-99", "name": "금지어",
        "position": {"x": 0, "y": 600},
        "config": {"rules": ["banned_words"], "action": "block", "banned_words": ["경쟁사금지어"]},
    })
    out = [e for e in graph["edges"] if e["from"] == "say_missing"]
    if out:  # say_missing → guard → (원래 다음)
        graph["edges"].append({"from": "guard", "to": out[0]["to"]})
        out[0]["to"] = "guard"
    else:
        graph["edges"].append({"from": "say_missing", "to": "guard"})
    return graph


def _publish(tenant_id, graph: dict, *, name: str = "공개 흐름"):
    from apps.flows import services
    from apps.flows.models import Flow

    flow = services.create_flow(tenant_id, name, graph)
    errors = services.validate_for_tenant(tenant_id, graph)
    assert not errors, [(e.node_id, e.message) for e in errors]
    version = services.save_version(tenant_id, flow.id, note="시험").version
    services.deploy_version(tenant_id, flow.id, version.number)
    Flow.objects.for_tenant(tenant_id).exclude(pk=flow.pk).update(is_public=False)
    Flow.objects.filter(pk=flow.pk).update(is_public=True)
    return Flow.objects.get(pk=flow.pk)


@pytest.fixture
def planted(db, settings):
    """비밀·허용 도메인·Provider·웹훅을 모두 가진 테넌트 + 공개 흐름(공개 보기 켜짐)."""
    import secrets as _secrets

    from apps.flows import services
    from apps.tenants import tenant_secrets as ts
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import Tenant

    settings.PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE = 1000
    tenant = Tenant.objects.create_with_key(name="Leak Corp 내부명", raw_key=_secrets.token_urlsafe(32))
    tenant.slug = "leakcorp"
    tenant.save(update_fields=["slug"])
    ts.set_secret(tenant.id, "api_token", SECRET_VALUE)
    ts.add_domain(tenant.id, INTERNAL_HOST)
    config = tenant.config
    config.brand_name = "릭코프"
    config.system_prompt = "system-prompt-marker-3"
    config.llm_provider_type = "custom"
    config.llm_base_url = "https://llm.leakcorp.example/v1"
    config.llm_api_key = encrypt_secret(PROVIDER_KEY)
    config.webhook_url = "https://hooks.leakcorp.example/x"
    config.public_flow_view = True
    config.save()
    flow = _publish(tenant.id, _shop_graph_with_plants())
    draft = _shop_graph_with_plants()
    draft["nodes"][0]["name"] = "draft-only-marker"
    services.save_draft(tenant.id, flow.id, draft)
    return tenant, flow


def _get(client, slug: str, **extra):
    return client.get(URL.format(slug=slug), **extra)


@pytest.mark.django_db
def test_public_view_shows_deployed_graph_without_any_planted_value(client, planted):
    tenant, flow = planted
    res = _get(client, "leakcorp")

    assert res.status_code == 200
    raw = res.content.decode()
    for leak in LEAKS + [str(tenant.id), str(flow.id), str(tenant.tenant_key_hash)]:
        assert leak not in raw, leak
    body = json.loads(raw)
    assert body["brand_name"] == "릭코프" and body["flow_name"] == "공개 흐름"
    nodes = {n["id"]: n for n in body["graph"]["nodes"]}
    assert nodes["start"]["name"] != "draft-only-marker"  # 초안이 아니라 배포 버전
    order = nodes["order"]["config"]
    assert order == {"method": "POST"}  # 내부 URL·헤더·바디·저장 경로는 통째로 빠진다
    assert "instructions" not in nodes["say_missing"]["config"]  # 비밀 참조가 든 값은 값째 빠진다
    assert nodes["guard"]["config"] == {"rules": ["banned_words"], "action": "block", "target": "output",
                                        "block_message": "죄송합니다. 해당 내용은 안내해 드릴 수 없습니다."}
    assert nodes["route"]["config"]["branches"][0] == {"name": "status", "when": "label == 'order_status'"}
    assert {"from", "to", "handle"} >= set(body["graph"]["edges"][0])
    assert body["template"] is None  # 예제와 다른 흐름은 「고쳐 보기」가 없다


@pytest.mark.django_db
def test_reserved_demo_api_url_stays_but_without_query(client, db, settings):
    import secrets as _secrets

    from apps.flows.example_catalog import ACADEMY_RESERVATION, example_graph
    from apps.tenants.models import Tenant

    settings.PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE = 1000
    tenant = Tenant.objects.create_with_key(name="학원", raw_key=_secrets.token_urlsafe(32))
    tenant.slug = "academy"
    tenant.save(update_fields=["slug"])
    tenant.config.public_flow_view = True
    tenant.config.save()
    graph = example_graph(ACADEMY_RESERVATION)
    reserve = next(n for n in graph["nodes"] if n["id"] == "reserve")
    reserve["config"]["url"] += "?debug=query-marker"
    _publish(tenant.id, graph)

    body = _get(client, "academy").json()
    url = next(n for n in body["graph"]["nodes"] if n["id"] == "reserve")["config"]["url"]
    assert url.startswith("https://demo-api.flow.sandcorp.co.kr/") and "query-marker" not in url


@pytest.mark.django_db
def test_off_unknown_and_undeployed_are_the_same_404(client, planted):
    tenant, flow = planted
    unknown = _get(client, "no-such-tenant")
    tenant.config.public_flow_view = False
    tenant.config.save()
    off = _get(client, "leakcorp")
    tenant.config.public_flow_view = True
    tenant.config.save()
    from apps.flows.models import Flow  # 공개 흐름은 보관할 수 없으므로(FlowInUse) 「배포 버전 없음」으로 본다
    Flow.objects.filter(pk=flow.pk).update(deployed_version=None)
    undeployed = _get(client, "leakcorp")

    for res in (unknown, off, undeployed):
        assert res.status_code == 404
        assert res.content == unknown.content
        assert res.get("Cache-Control") == unknown.get("Cache-Control")


@pytest.mark.django_db
def test_public_view_defaults_off(client, db, settings):
    import secrets as _secrets

    from apps.flows.example_catalog import SANDCORP_HOME, example_graph
    from apps.tenants.models import Tenant

    tenant = Tenant.objects.create_with_key(name="기본", raw_key=_secrets.token_urlsafe(32))
    tenant.slug = "default-off"
    tenant.save(update_fields=["slug"])
    _publish(tenant.id, example_graph(SANDCORP_HOME))

    assert tenant.config.public_flow_view is False
    assert _get(client, "default-off").status_code == 404


@pytest.mark.django_db
def test_unmodified_example_offers_its_try_template_and_caches_briefly(client, db, settings):
    from django.core.management import call_command

    from apps.tenants.models import Tenant

    settings.PLATFORM_OPENAI_API_KEY = ""
    call_command("seed_sandcorp", "--skip-knowledge")
    res = _get(client, "sandcorp")

    assert res.status_code == 200
    assert res.json()["template"] == "sandcorp_home"
    assert res["Cache-Control"] == "public, max-age=60"
    assert Tenant.objects.get(slug="sandcorp").config.public_flow_view is True


@pytest.mark.django_db
def test_rate_limit_per_ip(client, planted, settings):
    from apps.chat.sse import get_redis_client

    get_redis_client().delete(*get_redis_client().keys("ratelimit:public_flow:*") or ["_"])
    settings.PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE = 2
    codes = [_get(client, "leakcorp", REMOTE_ADDR="203.0.113.9").status_code for _ in range(3)]
    other = _get(client, "leakcorp", REMOTE_ADDR="203.0.113.10").status_code

    assert codes == [200, 200, 429] and other == 200


# ── 허용 목록 전수 ────────────────────────────────────────────────────────────

EXPECTED_ALLOWLIST = {
    "start": set(),
    "ai_answer": {"instructions", "await_reply"},
    "condition": {"branches"},
    "handoff": {"reason"},
    "knowledge_search": {"top_k"},
    "ai_classify": {"instructions", "labels", "fields"},
    "guardrail": {"rules", "action", "target", "block_message"},
    "http_tool": {"method", "url"},
}


def test_allowlist_covers_every_node_kind_exactly():
    """새 노드 종류가 생기면(또는 허용 필드가 바뀌면) 이 시험이 깨진다 — 공개해도 되는 필드를 그때 정한다."""
    from apps.flows.public_view import PUBLIC_FIELDS
    from apps.flows.schema import NODE_CONFIG_MODELS, NodeKind

    assert {k.value for k in NodeKind} == set(EXPECTED_ALLOWLIST)
    assert {k.value: set(v) for k, v in PUBLIC_FIELDS.items()} == EXPECTED_ALLOWLIST
    for kind, fields in PUBLIC_FIELDS.items():
        assert set(fields) <= set(NODE_CONFIG_MODELS[kind].model_fields), kind


def test_unknown_kind_and_unknown_fields_are_dropped():
    from apps.flows.public_view import public_graph

    graph = {
        "nodes": [
            {"id": "a", "kind": "future_kind", "code": "N-01", "position": {"x": 1, "y": 2}, "config": {"x": "leak"}},
            {"id": "b", "kind": "ai_answer", "code": "N-02", "config": {"instructions": "안녕", "hidden": "leak"},
             "extra": "leak"},
        ],
        "edges": [{"from": "a", "to": "b", "handle": None, "meta": "leak"}],
    }
    out = public_graph(graph)

    assert "leak" not in json.dumps(out, ensure_ascii=False)
    assert out["nodes"][0]["config"] == {} and out["nodes"][1]["config"] == {"instructions": "안녕"}
    assert out["edges"] == [{"from": "a", "to": "b", "handle": "next"}]


# ── 설정: Admin만 ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_only_admin_can_turn_public_view_on(client, tenant_admin_token, tenant_member_token):
    def patch(token, value):
        return client.patch("/api/tenant/config/", data=json.dumps({"public_flow_view": value}),
                            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert patch(tenant_member_token, True).status_code == 403
    assert patch(tenant_member_token, False).status_code == 200  # 화면이 설정 전체를 되돌려 보내는 같은 값은 통과
    res = patch(tenant_admin_token, True)
    assert res.status_code == 200 and res.json()["public_flow_view"] is True
    got = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {tenant_member_token}").json()
    assert got["public_flow_view"] is True  # Member는 읽기만


# ── 설정 저장: Admin 전용 필드는 「값이 바뀔 때만」 권한 검사(#152) ──────────────────────


def _full_body(token, client):
    """관리 화면이 보내는 전체 본문 — GET이 돌려준 설정 그대로(Admin 전용 필드 포함)."""
    return client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {token}").json()


def _patch(client, token, body):
    return client.patch("/api/tenant/config/", data=json.dumps(body), content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
def test_member_saves_full_config_body_within_own_permissions(client, tenant_admin_token, tenant_member_token):
    body = _full_body(tenant_member_token, client)
    body["welcome_message"] = "환영합니다 #152"
    res = _patch(client, tenant_member_token, body)  # deploy_requires_approval·public_flow_view는 그대로 실림
    assert res.status_code == 200, res.content
    assert res.json()["welcome_message"] == "환영합니다 #152"


@pytest.mark.django_db
def test_member_cannot_change_admin_only_fields_even_in_full_body(client, tenant_admin_token, tenant_member_token):
    for field in ("deploy_requires_approval", "public_flow_view"):
        body = _full_body(tenant_member_token, client)
        body[field] = not body[field]
        assert _patch(client, tenant_member_token, body).status_code == 403, field
        assert _full_body(tenant_member_token, client)[field] is not body[field]


@pytest.mark.django_db
def test_admin_changes_every_admin_only_field(client, tenant_admin_token):
    body = _full_body(tenant_admin_token, client)
    body["deploy_requires_approval"] = not body["deploy_requires_approval"]
    body["public_flow_view"] = not body["public_flow_view"]
    res = _patch(client, tenant_admin_token, body)
    assert res.status_code == 200
    assert res.json()["deploy_requires_approval"] is body["deploy_requires_approval"]


@pytest.mark.django_db
def test_member_unchanged_admin_only_value_is_ok_when_already_on(client, tenant_admin_token, tenant_member_token):
    body = _full_body(tenant_admin_token, client)
    body["deploy_requires_approval"] = True
    body["public_flow_view"] = True
    assert _patch(client, tenant_admin_token, body).status_code == 200
    body["welcome_message"] = "켜진 값 그대로"
    assert _patch(client, tenant_member_token, body).status_code == 200


# ── 시드: env 기반 Provider ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_seed_sets_providers_from_platform_env_encrypted(settings, capsys):
    from django.core.management import call_command

    from apps.tenants.crypto import decrypt_secret
    from apps.tenants.models import Tenant

    settings.PLATFORM_OPENAI_API_KEY = "sk-from-env-7781"
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    settings.PLATFORM_EMBED_MODEL = "text-embedding-3-small"
    call_command("seed_sandcorp", "--skip-knowledge")

    config = Tenant.objects.get(slug="sandcorp").config
    assert (config.llm_provider_type, config.embed_provider_type, config.ocr_provider_type) == ("openai",) * 3
    assert config.model_id == "gpt-4o-mini" and config.ocr_model == "gpt-4o-mini"
    assert (config.embed_model, config.embed_dim) == ("text-embedding-3-small", 1536)
    assert "sk-from-env-7781" not in config.llm_api_key  # 암호화 저장
    assert {decrypt_secret(config.llm_api_key), decrypt_secret(config.embed_api_key),
            decrypt_secret(config.ocr_api_key)} == {"sk-from-env-7781"}
    assert "sk-from-env-7781" not in capsys.readouterr().out


@pytest.mark.django_db
def test_seed_without_platform_key_skips_providers_and_says_so(settings, capsys):
    from django.core.management import call_command

    from apps.tenants.models import Tenant

    settings.PLATFORM_OPENAI_API_KEY = ""
    call_command("seed_sandcorp", "--skip-knowledge")

    config = Tenant.objects.get(slug="sandcorp").config
    assert (config.llm_provider_type, config.embed_provider_type, config.ocr_provider_type) == ("", "", "")
    assert config.public_flow_view is True
    assert "PLATFORM_OPENAI_API_KEY" in capsys.readouterr().out


@pytest.mark.django_db
def test_seed_rejects_unknown_platform_embed_model(settings):
    from django.core.management import CommandError, call_command

    settings.PLATFORM_OPENAI_API_KEY = "sk-from-env-7781"
    settings.PLATFORM_EMBED_MODEL = "my-embedder"
    with pytest.raises(CommandError, match="PLATFORM_EMBED_MODEL"):
        call_command("seed_sandcorp", "--skip-knowledge")
