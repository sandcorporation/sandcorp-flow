import io

import pytest


# ── Issue 95: 재임베딩 재구축 (Embedding Provider 변경 시) ─────────────────────

@pytest.mark.django_db
def test_reembed_preserves_structure_and_freshness(client, tenant_admin_token, tenant_with_key):
    """재임베딩은 그래프 구조(Entity·Text Unit)를 보존하고, freshness를 fresh로 되돌리며,
    재구축 후에도 검색이 동작한다(새 임베딩 공간)."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.reembed import reembed_tenant
    from apps.rag.ingesters import get_embeddings

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"The return policy allows returns within 30 days. FOOTSWITCH is a foot controller.")
    f.name = "policy.txt"
    r = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert r.status_code == 201

    gs = GraphStore(str(tenant.id))
    mentions_before = {m["mention_id"] for m in gs.query_mentions()}
    assert mentions_before, "인제스션으로 Mention이 생성돼야 한다"

    reembed_tenant(str(tenant.id))

    # 구조(Entity Mention) 보존 — 재추출 없음
    assert {m["mention_id"] for m in gs.query_mentions()} == mentions_before
    assert gs.get_freshness() == "fresh"
    # 재구축 후 검색 동작(새 임베딩/인덱스)
    qe = get_embeddings(["return policy"])[0]
    assert gs.vector_search(qe, top_k=3), "재임베딩 후 Text Unit 검색이 동작해야 한다"


@pytest.mark.django_db
def test_embed_provider_change_triggers_reembed_llm_change_does_not(client, tenant_admin_token, tenant_with_key):
    """Embedding Provider 변경은 재임베딩을 트리거하고, LLM Provider 변경은 트리거하지 않는다."""
    from django.conf import settings
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))

    def patch(body):
        return client.patch(
            "/api/tenant/config/", body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )

    # LLM provider 변경 → 재구축 안 함(freshness 그대로)
    gs.set_freshness("stale")
    patch({"llm_provider_type": "custom", "llm_base_url": "https://x/v1"})
    assert gs.get_freshness() == "stale"

    # Embedding provider 변경 → 재임베딩(EAGER) → fresh
    gs.set_freshness("stale")
    patch({"embed_provider_type": "custom", "embed_base_url": f"{settings.OLLAMA_BASE_URL}/v1", "embed_model": "bge-m3"})
    assert gs.get_freshness() == "fresh"
