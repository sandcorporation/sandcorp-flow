"""설정 권한(#162, ADR-0025): Provider 키·base_url·종류·모델과 웹훅 주소는 Admin 전용, 시스템 프롬프트·상담 문구는 Member 허용.

「값이 바뀔 때만」 검사한다 — 화면이 설정 전체를 되돌려 보내므로 같은 값(키는 가림 값 ********, 비어 있던 키의 빈 값)은 통과.
"""
from __future__ import annotations

import json

import pytest

MASK = "********"


def _get(client, token):
    return client.get("/api/tenant/config/", HTTP_AUTHORIZATION=f"Bearer {token}").json()


def _patch(client, token, body):
    return client.patch("/api/tenant/config/", data=json.dumps(body), content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.fixture
def seeded(client, tenant_admin_token):
    """Admin이 키·웹훅을 심은 테넌트(type은 플랫폼 기본이라 외부 검증 호출 없음)."""
    res = _patch(client, tenant_admin_token, {
        "llm_api_key": "sk-seed-llm", "embed_api_key": "sk-seed-embed", "ocr_api_key": "sk-seed-ocr",
        "webhook_url": "https://hooks.example.com/a", "webhook_type": "slack",
    })
    assert res.status_code == 200, res.content


CHANGES = [
    ("llm_api_key", "sk-new"), ("embed_api_key", "sk-new"), ("ocr_api_key", "sk-new"),
    ("llm_base_url", "https://api.example.com/v1"), ("embed_base_url", "https://api.example.com/v1"),
    ("ocr_base_url", "https://api.example.com/v1"),
    ("llm_provider_type", "openai"), ("embed_provider_type", "openai"), ("ocr_provider_type", "openai"),
    ("model_id", "other-model"), ("extraction_model", "other-model"), ("embed_model", "other-embed"),
    ("embed_dim", 123), ("ocr_model", "other-ocr"),
    ("webhook_url", "https://hooks.example.com/b"), ("webhook_type", "generic"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", CHANGES)
def test_member_cannot_change_provider_or_webhook_field(client, seeded, tenant_member_token, field, value):
    res = _patch(client, tenant_member_token, {field: value})
    assert res.status_code == 403, (field, res.content)


@pytest.mark.django_db
def test_member_full_body_round_trip_with_masked_keys_is_ok(client, seeded, tenant_member_token):
    body = _get(client, tenant_member_token)
    assert body["llm_api_key"] == MASK
    body["system_prompt"] = "새 시스템 프롬프트 #162"
    body["welcome_message"] = "환영 #162"
    res = _patch(client, tenant_member_token, body)
    assert res.status_code == 200, res.content
    assert res.json()["system_prompt"] == "새 시스템 프롬프트 #162"


@pytest.mark.django_db
def test_member_empty_key_where_none_stored_is_not_a_change(client, tenant_admin_token, tenant_member_token):
    body = _get(client, tenant_member_token)
    assert body["llm_api_key"] == ""
    body["agent_display_name"] = "도우미"
    assert _patch(client, tenant_member_token, body).status_code == 200


@pytest.mark.django_db
def test_member_cannot_clear_stored_key_with_empty_string(client, seeded, tenant_member_token):
    assert _patch(client, tenant_member_token, {"llm_api_key": ""}).status_code == 403


@pytest.mark.django_db
def test_member_edits_prompt_and_texts(client, seeded, tenant_member_token):
    res = _patch(client, tenant_member_token, {"system_prompt": "p", "welcome_message": "w", "agent_display_name": "a",
                                               "scope_description": "범위", "scope_refusal_message": "거절"})
    assert res.status_code == 200, res.content


@pytest.mark.django_db
def test_admin_changes_all(client, seeded, tenant_admin_token):
    res = _patch(client, tenant_admin_token, {
        "llm_api_key": "sk-new", "webhook_url": "https://hooks.example.com/b", "webhook_type": "generic",
        "model_id": "m2", "embed_dim": 321, "system_prompt": "x",
    })
    assert res.status_code == 200, res.content


@pytest.mark.django_db
def test_member_cannot_quick_setup_or_load_models(client, seeded, tenant_member_token):
    h = {"HTTP_AUTHORIZATION": f"Bearer {tenant_member_token}"}
    res = client.post("/api/tenant/providers/quick-setup", data=json.dumps({"api_key": "sk-x"}),
                      content_type="application/json", **h)
    assert res.status_code == 403
    # 가림 키로 저장된 키를 임의 base_url에 보내 보는 길(키 유출)도 막는다.
    res = client.post("/api/tenant/providers/models",
                      data=json.dumps({"kind": "llm", "type": "custom", "base_url": "https://evil.example.com/v1", "api_key": MASK}),
                      content_type="application/json", **h)
    assert res.status_code == 403
